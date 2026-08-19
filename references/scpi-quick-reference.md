# DHO800/DHO900 SCPI quick reference

Use long command spellings when diagnosing compatibility; SCPI keywords are case-insensitive. The default raw-socket endpoint for this skill is TCP port 5555 with newline read/write termination.

## Connection

```text
Raw TCP: <scope-ip>:5555
PyVISA:  TCPIP0::<scope-ip>::5555::SOCKET
Read termination:  \n
Write termination: \n
```

An open TCP port is not proof of a responsive SCPI session. `*IDN?` is the protocol-level check.

## USBTMC/VISA connection

USB SCPI uses VISA resource strings supplied by the installed VISA backend. Do not hardcode a DHO product ID because it can vary by model or firmware; discover resources and match RIGOL vendor ID `0x1AB1`, then verify with `*IDN?`.

```text
Typical shape: USB0::0x1AB1::<product-id>::<serial>::INSTR
Enumerate:     python scripts/dho_usb_scpi.py list
Identify:      python scripts/dho_usb_scpi.py discover
Select:        --resource "<exact-resource-from-list>"
```

The USB client sets a newline write termination and relies on the USBTMC/VISA end-of-message indication for reads so IEEE-488.2 binary payloads are not truncated at an embedded newline. PyVISA is required, together with either a system VISA runtime or a configured PyVISA-py/PyUSB/libusb backend.

## Web screen capture

The instrument web UI uses a separate WebSocket service rather than SCPI:

```text
Endpoint: ws://<scope-ip>:9003
Request:  take_screenshot   (WebSocket text message)
Reply:    JPEG bytes        (WebSocket binary message)
```

Use `scripts/dho_screen.py --output <screen.jpg>` to perform the WebSocket upgrade, request one frame, validate the JPEG and dimensions, and save it. Use a screen capture for visual inspection or documentation; use SCPI waveform transfer for calibrated sample data and numeric processing.

## Read-only queries

These commands are commonly available on DHO800/DHO900 firmware. Verify against the programming guide if a query returns an error.

```text
*IDN?
*OPC?
:SYSTem:ERRor?
:TRIGger:STATus?
:ACQuire:SRATe?
:ACQuire:MDEPth?
:TIMebase:SCALe?
:TIMebase:OFFSet?
:CHANnel1:DISPlay?
:CHANnel1:SCALe?
:CHANnel1:OFFSet?
:CHANnel1:COUPling?
:CHANnel1:PROBe?
```

Do not query nonexistent channels. Identify the exact model first.

## Immediate measurements

The DHO programming guide documents the `:MEASure:ITEM? <item>,<source>` form. Common examples:

```text
:MEASure:ITEM? VPP,CHANnel1
:MEASure:ITEM? VRMS,CHANnel1
:MEASure:ITEM? FREQuency,CHANnel1
:MEASure:ITEM? PERiod,CHANnel1
```

The CLI accepts abbreviated source names such as `CHAN1` and prints the instrument's raw reply. A sentinel/overflow value is not a valid physical measurement; inspect signal presence, trigger, scale, and acquisition state.

## Waveforms and binary blocks

Waveform transfer usually requires transient configuration writes followed by a query:

```text
:WAVeform:SOURce CHANnel1
:WAVeform:MODE NORMal
:WAVeform:FORMat BYTE
:WAVeform:DATA?
```

For raw/deep-memory capture, stop acquisition if the programming guide requires it, select the requested start/stop range, and restore acquisition only when the user requested that behavior. Scaling queries commonly include:

```text
:WAVeform:XINCrement?
:WAVeform:XORigin?
:WAVeform:XREFerence?
:WAVeform:YINCrement?
:WAVeform:YORigin?
:WAVeform:YREFerence?
```

Use `query ":WAVeform:DATA?" --output <file>` after the user has authorized any required setup writes. IEEE-488.2 definite-length data begins with `#`, then one digit giving the byte-count field width, then the payload length and payload.

## Setting examples

These mutate instrument state and require an explicit user request plus the CLI's `--confirm-write` guard:

```text
:CHANnel1:DISPlay ON
:CHANnel1:SCALe 0.5
:TIMebase:SCALe 1e-3
:TRIGger:EDGE:SOURce CHANnel1
:RUN
:STOP
:SINGle
```

Query the corresponding value before and after when feasible. `:RUN`, `:STOP`, and `:SINGle` change acquisition state and do not have simple symmetric query forms.

## Sources

- RIGOL, *DHO800/DHO900 Series Digital Oscilloscope Programming Guide* (model/firmware authority).
- RIGOL DHO800/DHO900 community PyVISA examples confirm `TCPIP0::<ip>::5555::SOCKET` and newline termination. Treat community examples as transport evidence, not as authority for every SCPI command.
