# Meter Modbus Address Assignment — Field SOP

**Purpose:** Assign unique Modbus addresses to DDS8888 meters before they are
installed in a multi-meter enclosure. Each meter ships from the factory with
Modbus ID = 1. If two meters at ID 1 are connected to the same PCB RS-485
bus, they will conflict — no data will be read from either.

**Scope:** One technician, one Windows laptop, one USB-to-RS485 adapter, one
meter at a time. Repeat for each meter before installation.

---

## Why this matters

A single PCB talks to up to 8 meters on a shared RS-485 bus. Each meter MUST
have a unique Modbus ID (2–9 in a typical 8-meter box). The PCB firmware
auto-commissions new meters at ID 1, but it can only do this if only ONE
meter is at ID 1 at a time. Pre-addressing meters before they go into the
box guarantees no conflicts.

---

## Required hardware

| Item | Notes |
|------|-------|
| Windows laptop | Any Windows 10/11 machine |
| USB-to-RS485 adapter | FTDI, CH340, or CP210x based |
| 4-wire cable (A, B, GND, +V) | To connect meter to RS-485 adapter |
| DDS8888 meter | Powered on (AC mains or bench PSU) |
| Permanent marker or label maker | To tag each meter after addressing |
| `set_meter_address.py` | From this repo's `scripts/` folder |

---

## One-time laptop setup

### 1. Install Python (if not present)

Open PowerShell or Command Prompt and check:

```
python --version
```

If you see "Python 3.x.x", you're good. If not, download from
`https://www.python.org/downloads/` — check "Add Python to PATH" during install.

### 2. Install pyserial

```
pip install pyserial
```

### 3. Install the USB-to-RS485 driver

Windows usually auto-installs drivers for FTDI/CH340/CP210x chips. If the
adapter doesn't appear as a COM port in Device Manager, install the driver
from the adapter manufacturer's website.

### 4. Copy the tool

Copy `scripts/set_meter_address.py` from this repo to the laptop, or run it
from a USB drive.

---

## Wiring

```
USB-to-RS485        Meter terminal block
─────────────       ───────────────────
   A      ────────►  RS-485 A  (often labeled "A" or "485A")
   B      ────────►  RS-485 B  (often labeled "B" or "485B")
   GND    ────────►  GND        (optional but recommended)
```

- **DO NOT connect more than one meter at a time.**
- The meter needs power: plug it into AC mains or a bench supply.
- If the meter display is on, it's powered.

---

## Step-by-step: assign an address

### 1. Connect ONE meter

Wire the USB-to-RS485 adapter to a single meter. Plug the USB end into the
laptop.

### 2. Find the COM port

```
python set_meter_address.py --list-ports
```

Look for "USB Serial Port" or similar. Example output:

```
Available serial ports:
  COM3  —  USB Serial Port (COM3)
  COM4  —  Silicon Labs CP210x USB to UART Bridge (COM4)
```

### 3. Assign an address

**Interactive mode (recommended):**

```
python set_meter_address.py --port COM4
```

The tool will:
1. Poll factory ID 1 for the meter's serial number
2. Scan for already-assigned meters (IDs 2–11)
3. Suggest the first available ID
4. Ask for confirmation
5. Write the new address
6. Verify by reading back from the new address
7. Print a label to attach to the meter

**Batch mode (non-interactive):**

```
python set_meter_address.py --port COM4 --address 5 --yes
```

This skips all prompts — useful once you know the workflow.

### 4. Label the meter

The tool prints a label block:

```
╔══════════════════════════════╗
║  METER ADDRESS: 5            ║
║  SN: 230218440123            ║
╠══════════════════════════════╣
║  Write this number on the    ║
║  meter housing with a        ║
║  permanent marker or label.  ║
╚══════════════════════════════╝
```

Write **ADDR 5** prominently on the meter housing. Use a silver Sharpie on
dark plastic, or a white label.

### 5. Disconnect and repeat

Remove the addressed meter. Connect the next meter. Run the tool again. The
scan will show the previously-assigned IDs are now "occupied" (you'll see
them in the available-IDs list if you left the previous meters connected —
which is why you should disconnect them).

### 6. Verify the full set (optional)

Before installing meters in the enclosure, connect them ALL to the RS-485 bus
(through a terminal block or daisy chain) and run:

```
python set_meter_address.py --port COM4 scan
```

This scans IDs 1–11 and lists every meter that responds. Confirm you see
exactly the addresses you assigned, and NO meter at factory ID 1.

---

## Addressing scheme

| Box type | Meters | Assigned IDs | Notes |
|----------|--------|-------------|-------|
| 4-meter box | 4 | 2, 3, 4, 5 | Leave 1 for factory default |
| 6-meter box | 6 | 2, 3, 4, 5, 6, 7 | |
| 8-meter box | 8 | 2, 3, 4, 5, 6, 7, 8, 9 | Max for a single PCB |

**Rule:** ID 1 is reserved for factory-fresh meters. First assigned meter gets
ID 2, second gets ID 3, etc. Never assign two meters in the same box the same
ID. ID 10 and 11 are available if a box has more than 8 meters (max 10 per
PCB firmware).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "No response from Modbus ID 1" | Meter not powered | Check AC mains / PSU |
| | A/B wires swapped | Swap A and B at the meter |
| | Wrong COM port | Run `--list-ports` |
| | RS-485 driver missing | Check Device Manager for ⚠ |
| "Write failed" | Meter not at factory ID | Try `identify` to find its ID, then `--factory-id N` |
| Verification returns wrong SN | Bus noise | Add GND wire, shorten cable |
| Multiple meters responding at ID 1 | Two meters still connected | Only ONE meter at a time |

### Recovery: meter already at unknown address

If you don't know a meter's current Modbus ID:

```
python set_meter_address.py --port COM4 scan
```

This tries every ID 1–11 and reports any meter that responds.

### Recovery: multiple meters at factory ID 1

If you accidentally connected two un-addressed meters, they will both try to
respond to queries at ID 1, corrupting the response. Disconnect all but one.

---

## Reference: protocol summary

| Parameter | Value |
|-----------|-------|
| Physical layer | RS-485 half-duplex |
| Baud rate | 2400 |
| Data bits | 8 |
| Parity | Even |
| Stop bits | 1 |
| Factory Modbus ID | 1 |
| Read SN register | 0x0010 (3 registers, 6 BCD bytes) |
| Write address register | 0x000F (FC 0x10, DDS8888 variant) |

---

## Related docs

- `scripts/set_meter_address.py` — this tool
- `Docs/1meter-technical-overview.md` — complete DDS8888 register map
- `HANDOVER.md` — parity corruption root cause and recovery
- `Docs/build/BUILD_LOG.md` — per-device firmware build tracking
