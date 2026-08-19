---
name: rigol-dho-scpi
description: Query, measure, capture waveform data or the front-panel screen, and explicitly configure RIGOL DHO800/DHO900-series oscilloscopes over LAN or USBTMC/VISA. Use for devices such as DHO814 and DHO924; do not assume compatibility with other RIGOL families without checking their programming guide.
---

# RIGOL DHO SCPI

Choose the transport the user requested or that is physically available:

- LAN SCPI: use the dependency-free `scripts/dho_scpi.py`. Supply the oscilloscope address with `--host <scope-ip>` or `RIGOL_DHO_HOST`; the default SCPI port is 5555 and may be overridden with `--port` or `RIGOL_DHO_PORT`.
- USB SCPI: use `scripts/dho_usb_scpi.py` with a USBTMC VISA resource. It requires PyVISA plus a working vendor VISA runtime or PyVISA-py USB backend.

## Start safely

1. Resolve this skill's directory as `<skill-dir>` and select the matching transport script.
2. For a new LAN session, run `probe`, then `idn`. For USB, run `list`, then `discover`; `list` only enumerates VISA resources, while `discover` sends read-only `*IDN?` queries.
3. Confirm the returned model starts with `DHO8` or `DHO9` before using family-specific commands. Never infer channel count or installed options from the example model names.
4. If port 5555 is open but `*IDN?` times out, report the distinction. Ask the user to check the oscilloscope's LAN/remote-control service and whether another controller holds the socket; do not loop indefinitely.

Typical commands:

```text
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> probe
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> idn
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> status
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> measure VPP CHAN1
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> query ":CHANnel1:SCALe?"
```

Use `--timeout <seconds>` for slow acquisitions or large transfers. The equivalent PyVISA resource is `TCPIP0::<host>::5555::SOCKET`, with both read and write termination set to `\n`.

## USBTMC/VISA SCPI

USB uses the same authorization rules and SCPI command set as LAN. Start with enumeration and identification:

```text
python <skill-dir>/scripts/dho_usb_scpi.py list
python <skill-dir>/scripts/dho_usb_scpi.py discover
python <skill-dir>/scripts/dho_usb_scpi.py idn
python <skill-dir>/scripts/dho_usb_scpi.py status
python <skill-dir>/scripts/dho_usb_scpi.py measure VPP CHAN1
python <skill-dir>/scripts/dho_usb_scpi.py query ":CHANnel1:SCALe?"
```

Auto-selection accepts exactly one USB VISA resource exposing RIGOL vendor ID `0x1AB1`. With multiple instruments, copy the exact value returned by `list` or `discover` and pass `--resource "USB...::INSTR"`; it may also be set as `RIGOL_DHO_USB_RESOURCE`. Use `--backend @py` or `RIGOL_DHO_VISA_BACKEND=@py` only when PyVISA-py, PyUSB, and a usable libusb backend are already configured.

Do not install or replace Windows USB drivers merely to make discovery work without explaining the impact and obtaining approval; changing a device to WinUSB/libusb can break vendor VISA software. If PyVISA or a VISA backend is missing, report that separately from "no USB instrument found." Run `self-test` to validate local parsing without PyVISA or hardware.

## Capture the screen

The DHO web interface exposes a read-only screenshot service separately from SCPI. Use the bundled standard-library WebSocket client; it sends `take_screenshot`, validates the returned JPEG, and reports its dimensions and size as JSON.

```text
python <skill-dir>/scripts/dho_screen.py --host <scope-ip> --output <screen.jpg>
python <skill-dir>/scripts/dho_screen.py --host <scope-ip> --timeout 15 --output <screen.jpg>
```

The screenshot endpoint is `ws://<scope-ip>:9003`. Supply the address with `--host` or `RIGOL_DHO_HOST`; override the port with `--port` or `RIGOL_DHO_SCREEN_PORT`. The output path is required. Existing files are preserved unless `--force` is explicitly supplied. Capturing the display does not require SCPI setup and should not be substituted for waveform sample export when numeric analysis is requested.

## Authorization boundary

- Treat queries containing only SCPI query segments as read-only. Identification, state inspection, measurements, waveform reads, and screen captures may proceed when they are within the user's request.
- Run `write` only when the user explicitly asked to change the instrument. Pass `--confirm-write` only then. A broad request to inspect or diagnose is not permission to change acquisition, trigger, channel, math, decoder, storage, or display state.
- For an ordinary requested setting change, read the relevant current value when feasible, apply the narrowest command, query it back, and report the before/after state.
- Require a separate, exact confirmation before reset, calibration, deleting/overwriting instrument files, firmware actions, licenses/options, or undocumented service commands. Do not use hacking, unlock, or model-conversion commands as part of normal operation.
- Avoid `*RST` as a troubleshooting step. Prefer reconnecting and inspecting `:SYSTem:ERRor?`.

Example of an authorized setting change:

```text
python <skill-dir>/scripts/dho_scpi.py --host <scope-ip> write ":CHANnel1:SCALe 0.5" --confirm-write --check-error
python <skill-dir>/scripts/dho_usb_scpi.py write ":CHANnel1:SCALe 0.5" --confirm-write --check-error
```

## SCPI use

Use `query` for a single text or IEEE-488.2 definite-length binary response. Add `--output <file>` for binary blocks or large responses. The client rejects mixed write/query command chains in read-only mode.

For common DHO commands, measurement tokens, waveform-transfer setup, and model caveats, read [references/scpi-quick-reference.md](references/scpi-quick-reference.md). Consult the matching official DHO800/DHO900 Programming Guide before relying on a command not listed there or on firmware-specific behavior.

When returning measurements, preserve the raw value and unit/context, including channel, probe ratio, coupling, bandwidth limit, timebase, and acquisition state when those affect interpretation. Do not claim electrical validity from SCPI success alone.

## Troubleshooting

- LAN `probe` succeeds, query times out: the TCP listener is reachable but the SCPI service did not answer. Check the instrument's remote/LAN service, port setting, and competing client; then retry once.
- Connection refused: verify IP/subnet, the instrument's socket server, and port 5555.
- USB `list` fails before enumeration: PyVISA or its VISA backend is unavailable; this is not evidence about the cable or oscilloscope.
- USB `list` is empty: check the cable, oscilloscope USB device mode, Windows Device Manager, and VISA driver binding.
- Multiple RIGOL USB resources: do not guess; select the exact resource using `--resource`.
- Empty or malformed binary block: increase timeout, reduce requested point count, and confirm waveform format/mode against the programming guide.
- `Undefined header` or another instrument error: use the long command spelling and verify the command applies to the identified model and firmware.
- Run `self-test` to validate the local client parser without contacting the oscilloscope.
