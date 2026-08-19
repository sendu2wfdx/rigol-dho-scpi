#!/usr/bin/env python3
"""PyVISA USBTMC SCPI client for RIGOL DHO800/DHO900 oscilloscopes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence, Union


RIGOL_USB_VID = 0x1AB1
MAX_RESPONSE = 256 * 1024 * 1024
Response = Union[str, bytes]


class UsbScpiError(RuntimeError):
    pass


def load_pyvisa() -> Any:
    try:
        import pyvisa
    except ImportError as exc:
        raise UsbScpiError(
            "PyVISA is not installed. Install pyvisa and a VISA backend. On Windows, "
            "a vendor VISA runtime is preferred; pyvisa-py additionally needs a working "
            "PyUSB/libusb USBTMC setup. Do not replace USB drivers without user approval."
        ) from exc
    return pyvisa


def is_query_only(command: str) -> bool:
    segments = [segment.strip() for segment in command.split(";") if segment.strip()]
    return bool(segments) and all("?" in segment for segment in segments)


def parse_response(raw: bytes) -> Response:
    if not raw:
        raise UsbScpiError("instrument returned an empty response")
    if raw.startswith(b"#"):
        if len(raw) < 2 or not raw[1:2].isdigit():
            raise UsbScpiError("malformed IEEE-488.2 binary block header")
        digits = int(raw[1:2])
        if digits == 0:
            raise UsbScpiError("indefinite-length binary blocks are not supported")
        header_end = 2 + digits
        if len(raw) < header_end or not raw[2:header_end].isdigit():
            raise UsbScpiError("malformed IEEE-488.2 binary block length")
        length = int(raw[2:header_end])
        if length > MAX_RESPONSE:
            raise UsbScpiError(f"binary response exceeds {MAX_RESPONSE} byte safety limit")
        payload_end = header_end + length
        if len(raw) < payload_end:
            raise UsbScpiError(
                f"binary response ended after {len(raw) - header_end} of {length} payload bytes"
            )
        return raw[header_end:payload_end]
    if len(raw) > MAX_RESPONSE:
        raise UsbScpiError(f"text response exceeds {MAX_RESPONSE} byte safety limit")
    return raw.rstrip(b"\r\n").decode("ascii", errors="replace")


def print_response(response: Response, output: Optional[str]) -> None:
    if output:
        mode = "wb" if isinstance(response, bytes) else "w"
        kwargs = {} if isinstance(response, bytes) else {"encoding": "utf-8", "newline": ""}
        with open(output, mode, **kwargs) as handle:
            handle.write(response)
        size = len(response) if isinstance(response, bytes) else len(response.encode("utf-8"))
        print(json.dumps({"output": os.path.abspath(output), "bytes": size}, ensure_ascii=False))
    elif isinstance(response, bytes):
        sys.stdout.buffer.write(response)
    else:
        print(response)


def create_manager(args: argparse.Namespace) -> Any:
    pyvisa = load_pyvisa()
    try:
        return pyvisa.ResourceManager(args.backend) if args.backend else pyvisa.ResourceManager()
    except Exception as exc:
        backend = args.backend or "system default"
        raise UsbScpiError(f"cannot initialize VISA backend {backend!r}: {exc}") from exc


def usb_resources(manager: Any) -> Sequence[str]:
    try:
        return tuple(manager.list_resources("USB?*INSTR"))
    except Exception as exc:
        raise UsbScpiError(f"cannot enumerate USBTMC VISA resources: {exc}") from exc


def looks_like_rigol_resource(resource: str) -> bool:
    upper = resource.upper()
    return "::0X1AB1::" in upper or f"::{RIGOL_USB_VID}::" in upper


def select_resource(manager: Any, requested: Optional[str]) -> str:
    if requested:
        return requested
    resources = usb_resources(manager)
    candidates = [item for item in resources if looks_like_rigol_resource(item)]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise UsbScpiError(
            "multiple RIGOL USB resources found; select one with --resource: "
            + ", ".join(candidates)
        )
    if resources:
        raise UsbScpiError(
            "USB VISA resources exist, but none expose the RIGOL vendor ID 0x1AB1; "
            "run discover or pass the intended resource with --resource"
        )
    raise UsbScpiError(
        "no USBTMC VISA resources found; check the USB cable, instrument USB device mode, "
        "Windows Device Manager, and the installed VISA backend"
    )


@contextmanager
def open_instrument(manager: Any, resource: str, args: argparse.Namespace) -> Iterator[Any]:
    try:
        instrument = manager.open_resource(resource)
        instrument.timeout = max(1, int(args.timeout * 1000))
        instrument.chunk_size = args.chunk_size
        instrument.write_termination = "\n"
        instrument.read_termination = None
    except Exception as exc:
        raise UsbScpiError(f"cannot open VISA resource {resource!r}: {exc}") from exc
    try:
        yield instrument
    finally:
        instrument.close()


def query_raw(instrument: Any, command: str) -> Response:
    try:
        instrument.write(command.rstrip("\r\n"))
        return parse_response(bytes(instrument.read_raw()))
    except UsbScpiError:
        raise
    except Exception as exc:
        raise UsbScpiError(f"USB SCPI query {command!r} failed: {exc}") from exc


def identify(instrument: Any) -> str:
    response = query_raw(instrument, "*IDN?")
    if not isinstance(response, str):
        raise UsbScpiError("*IDN? unexpectedly returned binary data")
    return response


def ensure_supported_dho(idn: str) -> None:
    fields = [field.strip() for field in idn.split(",")]
    manufacturer = fields[0].upper() if fields else ""
    model = fields[1].upper() if len(fields) > 1 else ""
    if "RIGOL" not in manufacturer or not (model.startswith("DHO8") or model.startswith("DHO9")):
        raise UsbScpiError(
            f"selected USB resource is not an identified RIGOL DHO800/DHO900 instrument: {idn!r}"
        )


def cmd_list(args: argparse.Namespace) -> int:
    manager = create_manager(args)
    try:
        resources = usb_resources(manager)
    finally:
        manager.close()
    print(json.dumps({"usb_resources": list(resources)}, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    manager = create_manager(args)
    results = []
    try:
        for resource in usb_resources(manager):
            entry = {"resource": resource}
            try:
                with open_instrument(manager, resource, args) as instrument:
                    idn = identify(instrument)
                entry["idn"] = idn
                fields = [field.strip() for field in idn.split(",")]
                model = fields[1].upper() if len(fields) > 1 else ""
                entry["supported_dho"] = "RIGOL" in idn.upper() and (
                    model.startswith("DHO8") or model.startswith("DHO9")
                )
            except UsbScpiError as exc:
                entry["error"] = str(exc)
            results.append(entry)
    finally:
        manager.close()
    print(json.dumps({"usb_instruments": results}, ensure_ascii=False, indent=2))
    return 0 if any(item.get("supported_dho") for item in results) else 3


def with_selected(args: argparse.Namespace, callback: Any) -> int:
    manager = create_manager(args)
    try:
        resource = select_resource(manager, args.resource)
        with open_instrument(manager, resource, args) as instrument:
            return int(callback(resource, instrument))
    finally:
        manager.close()


def cmd_resource(args: argparse.Namespace) -> int:
    manager = create_manager(args)
    try:
        print(select_resource(manager, args.resource))
    finally:
        manager.close()
    return 0


def cmd_idn(args: argparse.Namespace) -> int:
    return with_selected(args, lambda _resource, instrument: (print(identify(instrument)), 0)[1])


def cmd_status(args: argparse.Namespace) -> int:
    queries = {
        "trigger_status": ":TRIGger:STATus?",
        "sample_rate_sps": ":ACQuire:SRATe?",
        "memory_depth_points": ":ACQuire:MDEPth?",
        "timebase_scale_s": ":TIMebase:SCALe?",
        "timebase_offset_s": ":TIMebase:OFFSet?",
    }

    def run(resource: str, instrument: Any) -> int:
        idn = identify(instrument)
        ensure_supported_dho(idn)
        result = {"transport": "usb", "resource": resource, "idn": idn}
        for key, command in queries.items():
            try:
                value = query_raw(instrument, command)
                result[key] = (
                    value.decode("ascii", errors="replace") if isinstance(value, bytes) else value
                )
            except UsbScpiError as exc:
                result[key] = {"error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return with_selected(args, run)


def cmd_query(args: argparse.Namespace) -> int:
    if not is_query_only(args.command):
        raise UsbScpiError(
            "read-only query mode rejects write or mixed command segments; "
            "use write with explicit authorization"
        )

    def run(_resource: str, instrument: Any) -> int:
        idn = identify(instrument)
        ensure_supported_dho(idn)
        print_response(query_raw(instrument, args.command), args.output)
        return 0

    return with_selected(args, run)


def cmd_write(args: argparse.Namespace) -> int:
    if not args.confirm_write:
        raise UsbScpiError("refusing to change instrument state without --confirm-write")

    def run(_resource: str, instrument: Any) -> int:
        idn = identify(instrument)
        ensure_supported_dho(idn)
        try:
            instrument.write(args.command.rstrip("\r\n"))
        except Exception as exc:
            raise UsbScpiError(f"USB SCPI write failed: {exc}") from exc
        if args.check_error:
            print_response(query_raw(instrument, ":SYSTem:ERRor?"), None)
        else:
            print("SCPI command sent")
        return 0

    return with_selected(args, run)


def cmd_measure(args: argparse.Namespace) -> int:
    item = args.item.upper()
    source = args.source.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", item):
        raise UsbScpiError(f"invalid measurement item: {args.item!r}")
    if not re.fullmatch(r"(?:CHAN(?:NEL)?[1-4]|MATH[1-4]?)", source):
        raise UsbScpiError(f"invalid measurement source: {args.source!r}")

    def run(_resource: str, instrument: Any) -> int:
        idn = identify(instrument)
        ensure_supported_dho(idn)
        print_response(query_raw(instrument, f":MEASure:ITEM? {item},{source}"), None)
        return 0

    return with_selected(args, run)


def cmd_self_test(_: argparse.Namespace) -> int:
    assert is_query_only(":CHAN1:SCAL?")
    assert not is_query_only(":CHAN1:SCAL 1;:CHAN1:SCAL?")
    assert parse_response(b"RIGOL TECHNOLOGIES,DHO924,SERIAL,00.01\n") == (
        "RIGOL TECHNOLOGIES,DHO924,SERIAL,00.01"
    )
    assert parse_response(b"#2100123456789\n") == b"0123456789"
    assert looks_like_rigol_resource("USB0::0x1AB1::0x0610::SERIAL::INSTR")
    ensure_supported_dho("RIGOL TECHNOLOGIES,DHO814,SERIAL,00.01")

    class FakeManager:
        def list_resources(self, pattern: str) -> Sequence[str]:
            assert pattern == "USB?*INSTR"
            return (
                "USB0::0x1234::0x0001::OTHER::INSTR",
                "USB0::0x1AB1::0x0610::DHO9TEST::INSTR",
            )

    assert select_resource(FakeManager(), None) == (
        "USB0::0x1AB1::0x0610::DHO9TEST::INSTR"
    )
    print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource",
        default=os.getenv("RIGOL_DHO_USB_RESOURCE"),
        help="VISA USB resource; auto-selects one RIGOL USB resource when omitted",
    )
    parser.add_argument(
        "--backend",
        default=os.getenv("RIGOL_DHO_VISA_BACKEND"),
        help="PyVISA backend such as @py; defaults to the system VISA runtime",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="list USBTMC VISA resources without sending SCPI").set_defaults(
        func=cmd_list
    )
    sub.add_parser("discover", help="query *IDN? on each USBTMC resource").set_defaults(
        func=cmd_discover
    )
    sub.add_parser("resource", help="print the selected VISA resource").set_defaults(
        func=cmd_resource
    )
    sub.add_parser("idn", help="query *IDN? on the selected resource").set_defaults(func=cmd_idn)
    sub.add_parser("status", help="query basic acquisition state as JSON").set_defaults(
        func=cmd_status
    )
    sub.add_parser("self-test", help="test parsing without PyVISA or hardware").set_defaults(
        func=cmd_self_test
    )

    query_parser = sub.add_parser("query", help="send a read-only SCPI query")
    query_parser.add_argument("command")
    query_parser.add_argument("--output", help="write a text or binary response to a file")
    query_parser.set_defaults(func=cmd_query)

    write_parser = sub.add_parser("write", help="send a state-changing SCPI command")
    write_parser.add_argument("command")
    write_parser.add_argument("--confirm-write", action="store_true")
    write_parser.add_argument("--check-error", action="store_true")
    write_parser.set_defaults(func=cmd_write)

    measure_parser = sub.add_parser(
        "measure", help="run :MEASure:ITEM? <item>,<source>"
    )
    measure_parser.add_argument("item")
    measure_parser.add_argument("source")
    measure_parser.set_defaults(func=cmd_measure)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.chunk_size <= 0 or args.chunk_size > MAX_RESPONSE:
        parser.error(f"--chunk-size must be between 1 and {MAX_RESPONSE}")
    try:
        return int(args.func(args))
    except UsbScpiError as exc:
        print(f"USB SCPI error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
