#!/usr/bin/env python3
"""Dependency-free SCPI client for RIGOL DHO800/DHO900 oscilloscopes."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
from dataclasses import dataclass
from typing import Optional, Union


DEFAULT_PORT = 5555
MAX_RESPONSE = 256 * 1024 * 1024


class ScpiError(RuntimeError):
    pass


class ScpiTimeout(ScpiError):
    pass


Response = Union[str, bytes]


@dataclass
class Scope:
    host: str
    port: int
    timeout: float
    sock: Optional[socket.socket] = None

    def __enter__(self) -> "Scope":
        try:
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
            self.sock.settimeout(self.timeout)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, socket.timeout) as exc:
            raise ScpiError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _socket(self) -> socket.socket:
        if self.sock is None:
            raise ScpiError("SCPI socket is not connected")
        return self.sock

    def write(self, command: str) -> None:
        command = command.rstrip("\r\n")
        if not command:
            raise ScpiError("SCPI command is empty")
        try:
            self._socket().sendall(command.encode("ascii") + b"\n")
        except UnicodeEncodeError as exc:
            raise ScpiError("SCPI commands must be ASCII") from exc
        except (OSError, socket.timeout) as exc:
            raise ScpiError(f"failed to send SCPI command: {exc}") from exc

    def _recv_exact(self, size: int) -> bytes:
        if size < 0 or size > MAX_RESPONSE:
            raise ScpiError(f"invalid or excessive SCPI payload length: {size}")
        chunks = bytearray()
        while len(chunks) < size:
            try:
                part = self._socket().recv(min(65536, size - len(chunks)))
            except socket.timeout as exc:
                raise ScpiTimeout(
                    f"timed out while receiving {size} bytes from {self.host}:{self.port}"
                ) from exc
            if not part:
                raise ScpiError(
                    f"connection closed after {len(chunks)} of {size} response bytes"
                )
            chunks.extend(part)
        return bytes(chunks)

    def _recv_one(self) -> bytes:
        try:
            first = self._socket().recv(1)
        except socket.timeout as exc:
            raise ScpiTimeout(
                f"{self.host}:{self.port} accepted TCP but did not answer SCPI before "
                f"the {self.timeout:g}s timeout"
            ) from exc
        if not first:
            raise ScpiError("instrument closed the connection without a response")
        return first

    def read_response(self) -> Response:
        first = self._recv_one()
        if first == b"#":
            digits_byte = self._recv_exact(1)
            if not digits_byte.isdigit():
                raise ScpiError(f"malformed binary block header: #{digits_byte!r}")
            digits = int(digits_byte)
            if digits == 0:
                raise ScpiError("indefinite-length binary blocks are not supported")
            length_bytes = self._recv_exact(digits)
            if not length_bytes.isdigit():
                raise ScpiError(f"malformed binary block length: {length_bytes!r}")
            return self._recv_exact(int(length_bytes))

        data = bytearray(first)
        while not data.endswith(b"\n"):
            if len(data) >= MAX_RESPONSE:
                raise ScpiError("text response exceeded the safety size limit")
            try:
                part = self._socket().recv(min(65536, MAX_RESPONSE - len(data)))
            except socket.timeout as exc:
                raise ScpiTimeout(
                    "timed out before the newline-terminated SCPI response completed"
                ) from exc
            if not part:
                break
            data.extend(part)
        return data.rstrip(b"\r\n").decode("ascii", errors="replace")

    def query(self, command: str) -> Response:
        self.write(command)
        return self.read_response()


def is_query_only(command: str) -> bool:
    segments = [segment.strip() for segment in command.split(";") if segment.strip()]
    return bool(segments) and all("?" in segment for segment in segments)


def print_response(response: Response, output: Optional[str]) -> None:
    if output:
        mode = "wb" if isinstance(response, bytes) else "w"
        kwargs = {} if isinstance(response, bytes) else {"encoding": "utf-8", "newline": ""}
        with open(output, mode, **kwargs) as handle:
            handle.write(response)
        size = len(response) if isinstance(response, bytes) else len(response.encode("utf-8"))
        print(json.dumps({"output": os.path.abspath(output), "bytes": size}, ensure_ascii=False))
        return
    if isinstance(response, bytes):
        sys.stdout.buffer.write(response)
    else:
        print(response)


def query_once(args: argparse.Namespace, command: str) -> Response:
    with Scope(args.host, args.port, args.timeout) as scope:
        return scope.query(command)


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        with Scope(args.host, args.port, args.timeout):
            pass
    except ScpiError as exc:
        print(
            json.dumps(
                {"host": args.host, "port": args.port, "tcp": False, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {"host": args.host, "port": args.port, "tcp": True, "scpi": "not tested"},
            ensure_ascii=False,
        )
    )
    return 0


def cmd_idn(args: argparse.Namespace) -> int:
    print_response(query_once(args, "*IDN?"), None)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    if not is_query_only(args.command):
        raise ScpiError(
            "read-only query mode rejects write or mixed command segments; "
            "use write with explicit authorization"
        )
    print_response(query_once(args, args.command), args.output)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    if not args.confirm_write:
        raise ScpiError("refusing to change instrument state without --confirm-write")
    with Scope(args.host, args.port, args.timeout) as scope:
        scope.write(args.command)
        if args.check_error:
            print_response(scope.query(":SYSTem:ERRor?"), None)
        else:
            print("SCPI command sent")
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    item = args.item.upper()
    source = args.source.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", item):
        raise ScpiError(f"invalid measurement item: {args.item!r}")
    if not re.fullmatch(r"(?:CHAN(?:NEL)?[1-4]|MATH[1-4]?)", source):
        raise ScpiError(f"invalid measurement source: {args.source!r}")
    print_response(query_once(args, f":MEASure:ITEM? {item},{source}"), None)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    queries = {
        "idn": "*IDN?",
        "trigger_status": ":TRIGger:STATus?",
        "sample_rate_sps": ":ACQuire:SRATe?",
        "memory_depth_points": ":ACQuire:MDEPth?",
        "timebase_scale_s": ":TIMebase:SCALe?",
        "timebase_offset_s": ":TIMebase:OFFSet?",
    }
    result = {"host": args.host, "port": args.port}
    for key, command in queries.items():
        try:
            value = query_once(args, command)
            result[key] = (
                value.decode("ascii", errors="replace") if isinstance(value, bytes) else value
            )
        except ScpiError as exc:
            result[key] = {"error": str(exc)}
            if key == "idn":
                break
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if isinstance(result.get("idn"), str) else 3


def cmd_resource(args: argparse.Namespace) -> int:
    print(f"TCPIP0::{args.host}::{args.port}::SOCKET")
    return 0


def cmd_self_test(_: argparse.Namespace) -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    port = listener.getsockname()[1]
    failures = []

    def server() -> None:
        replies = [b"RIGOL TECHNOLOGIES,DHO814,TEST,00.00\n", b"#2100123456789\n"]
        try:
            for reply in replies:
                conn, _ = listener.accept()
                with conn:
                    received = bytearray()
                    while not received.endswith(b"\n"):
                        part = conn.recv(1024)
                        if not part:
                            break
                        received.extend(part)
                    conn.sendall(reply)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(str(exc))
        finally:
            listener.close()

    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    with Scope("127.0.0.1", port, 2.0) as scope:
        text_reply = scope.query("*IDN?")
    with Scope("127.0.0.1", port, 2.0) as scope:
        binary_reply = scope.query(":WAVeform:DATA?")
    worker.join(timeout=2.0)
    assert text_reply == "RIGOL TECHNOLOGIES,DHO814,TEST,00.00"
    assert binary_reply == b"0123456789"
    assert is_query_only(":CHAN1:SCAL?")
    assert not is_query_only(":CHAN1:SCAL 1;:CHAN1:SCAL?")
    if failures:
        raise ScpiError("self-test server failed: " + "; ".join(failures))
    print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.getenv("RIGOL_DHO_HOST"),
        help="oscilloscope IP or hostname; may also be set with RIGOL_DHO_HOST",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("RIGOL_DHO_PORT", str(DEFAULT_PORT)))
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("probe", help="test TCP reachability without sending SCPI").set_defaults(
        func=cmd_probe
    )
    sub.add_parser("idn", help="query *IDN?").set_defaults(func=cmd_idn)
    sub.add_parser("status", help="query basic acquisition state as JSON").set_defaults(
        func=cmd_status
    )
    sub.add_parser("resource", help="print the equivalent PyVISA resource").set_defaults(
        func=cmd_resource
    )
    sub.add_parser(
        "self-test", help="test the client locally without contacting an instrument"
    ).set_defaults(func=cmd_self_test)

    query_parser = sub.add_parser("query", help="send a read-only SCPI query")
    query_parser.add_argument("command")
    query_parser.add_argument("--output", help="write a text or binary response to a file")
    query_parser.set_defaults(func=cmd_query)

    write_parser = sub.add_parser("write", help="send a state-changing SCPI command")
    write_parser.add_argument("command")
    write_parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="acknowledge that the command can change instrument state",
    )
    write_parser.add_argument(
        "--check-error", action="store_true", help="query :SYSTem:ERRor? after sending"
    )
    write_parser.set_defaults(func=cmd_write)

    measure_parser = sub.add_parser(
        "measure", help="run :MEASure:ITEM? <item>,<source>"
    )
    measure_parser.add_argument(
        "item", help="for example VPP, VRMS, FREQuency, or PERiod"
    )
    measure_parser.add_argument("source", help="for example CHAN1")
    measure_parser.set_defaults(func=cmd_measure)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.action != "self-test" and not args.host:
        parser.error("--host is required unless RIGOL_DHO_HOST is set")
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        return int(args.func(args))
    except ScpiTimeout as exc:
        print(f"SCPI timeout: {exc}", file=sys.stderr)
        return 3
    except (ScpiError, OSError) as exc:
        print(f"SCPI error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
