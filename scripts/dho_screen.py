#!/usr/bin/env python3
"""Capture a RIGOL DHO800/DHO900 front-panel screen over its web WebSocket."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Optional, Tuple


DEFAULT_PORT = 9003
MAX_IMAGE_BYTES = 32 * 1024 * 1024
JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


class ScreenCaptureError(RuntimeError):
    pass


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ScreenCaptureError("WebSocket connection closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


def websocket_handshake(sock: socket.socket, host: str, port: int) -> None:
    nonce = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Origin: http://{host}\r\n"
        f"Sec-WebSocket-Key: {nonce}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))

    response = bytearray()
    while b"\r\n\r\n" not in response:
        if len(response) > 65536:
            raise ScreenCaptureError("WebSocket HTTP response headers are too large")
        response.extend(sock.recv(4096))
    head, remainder = bytes(response).split(b"\r\n\r\n", 1)
    if remainder:
        raise ScreenCaptureError("unexpected WebSocket data during HTTP upgrade")
    lines = head.decode("iso-8859-1").split("\r\n")
    if not lines or " 101 " not in f" {lines[0]} ":
        raise ScreenCaptureError(f"WebSocket upgrade failed: {lines[0] if lines else 'empty reply'}")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    expected = base64.b64encode(
        hashlib.sha1((nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    ).decode("ascii")
    if headers.get("sec-websocket-accept") != expected:
        raise ScreenCaptureError("WebSocket server returned an invalid Sec-WebSocket-Accept")


def send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    if len(payload) >= 2**63:
        raise ScreenCaptureError("WebSocket payload is too large")
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length < 65536:
        header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    sock.sendall(header + mask + masked)


def recv_frame(sock: socket.socket) -> Tuple[bool, int, bytes]:
    first, second = recv_exact(sock, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if length > MAX_IMAGE_BYTES:
        raise ScreenCaptureError(f"WebSocket frame exceeds {MAX_IMAGE_BYTES} byte safety limit")
    mask = recv_exact(sock, 4) if masked else None
    payload = recv_exact(sock, length)
    if mask is not None:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return fin, opcode, payload


def receive_binary_message(sock: socket.socket) -> bytes:
    fragments = bytearray()
    started = False
    while True:
        fin, opcode, payload = recv_frame(sock)
        if opcode == 0x8:
            raise ScreenCaptureError("WebSocket server closed before returning a screenshot")
        if opcode == 0x9:
            send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x2:
            if started:
                raise ScreenCaptureError("received a new binary message before fragments completed")
            started = True
            fragments.extend(payload)
        elif opcode == 0x0 and started:
            fragments.extend(payload)
        elif opcode == 0x1:
            message = payload.decode("utf-8", errors="replace")
            raise ScreenCaptureError(f"device returned text instead of JPEG data: {message!r}")
        else:
            raise ScreenCaptureError(f"unexpected WebSocket opcode: 0x{opcode:x}")
        if len(fragments) > MAX_IMAGE_BYTES:
            raise ScreenCaptureError(f"screenshot exceeds {MAX_IMAGE_BYTES} byte safety limit")
        if fin:
            return bytes(fragments)


def jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ScreenCaptureError("device response is not a complete JPEG image")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        segment_length = struct.unpack("!H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height, width = struct.unpack("!HH", data[offset + 3 : offset + 7])
            if width > 0 and height > 0:
                return width, height
            break
        offset += segment_length
    raise ScreenCaptureError("JPEG dimensions could not be decoded")


def capture(host: str, port: int, timeout: float) -> bytes:
    try:
        with socket.create_connection((host, port), timeout) as sock:
            sock.settimeout(timeout)
            websocket_handshake(sock, host, port)
            send_frame(sock, 0x1, b"take_screenshot")
            return receive_binary_message(sock)
    except socket.timeout as exc:
        raise ScreenCaptureError(
            f"timed out talking to screenshot service at ws://{host}:{port}"
        ) from exc
    except OSError as exc:
        raise ScreenCaptureError(
            f"cannot use screenshot service at ws://{host}:{port}: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.getenv("RIGOL_DHO_HOST"),
        help="oscilloscope IP or hostname; may also be set with RIGOL_DHO_HOST",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("RIGOL_DHO_SCREEN_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", required=True, help="destination .jpg path")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output file"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.host:
        parser.error("--host is required unless RIGOL_DHO_HOST is set")
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.force:
        parser.error(f"output already exists: {output}; use --force to replace it")
    try:
        data = capture(args.host, args.port, args.timeout)
        width, height = jpeg_dimensions(data)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    except ScreenCaptureError as exc:
        print(f"screen capture error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "host": args.host,
                "port": args.port,
                "output": str(output),
                "bytes": len(data),
                "width": width,
                "height": height,
                "format": "jpeg",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
