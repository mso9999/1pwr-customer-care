#!/usr/bin/env python3
"""
set_meter_address.py — DDS8888 Modbus address assignment tool

Connect one meter at a time via USB-to-RS485, read its serial number,
assign a unique Modbus ID, and print a label for physical tagging.

Usage:
  python set_meter_address.py                  # interactive: scan, prompt for address
  python set_meter_address.py --address 5      # assign address 5 non-interactively
  python set_meter_address.py --port COM4      # specify COM port
  python set_meter_address.py --scan           # scan all IDs 1-11, list what's present

Requires: pyserial  (pip install pyserial)
Hardware: USB-to-RS485 adapter, DDS8888 meter wired to A/B/GND
Default: 2400-8E1, factory Modbus ID = 1
"""

import argparse
import sys
import time

# ── Modbus RTU helpers ────────────────────────────────────────────────

def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            lsb = crc & 1
            crc >>= 1
            if lsb:
                crc ^= 0xA001
    return crc


def build_read_frame(dev_id: int, reg: int, count: int) -> bytes:
    """FC 0x03: read holding registers."""
    payload = bytes([dev_id, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF])
    crc = modbus_crc(payload)
    return payload + bytes([crc & 0xFF, crc >> 8])


def build_write_frame(dev_id: int, reg: int, data: bytes) -> bytes:
    """FC 0x10: write registers — DDS8888 variant.

    The DDS8888 ignores the register-count field but validates the byte-count.
    We declare 1 register in the header (0x00 0x01) but send the full payload.

    WARNING: declaring the true register count causes the meter to write all
    consecutive registers, corrupting downstream settings (parity, etc.).
    """
    byte_count = len(data)
    header = bytes([dev_id, 0x10, reg >> 8, reg & 0xFF, 0x00, 0x01, byte_count])
    payload = header + data
    crc = modbus_crc(payload)
    return payload + bytes([crc & 0xFF, crc >> 8])


def parse_read_response(resp: bytes, expected_bytes: int) -> bytes | None:
    """Validate a FC 0x03 response and extract data bytes."""
    if len(resp) < 5:
        return None
    if resp[1] != 0x03:          # function code
        return None
    if resp[1] & 0x80:           # exception
        return None
    byte_count = resp[2]
    if byte_count != expected_bytes:
        return None
    expected_len = 3 + byte_count + 2  # ID + FC + BC + data + CRC
    if len(resp) < expected_len:
        return None
    data = resp[3:3 + byte_count]
    # verify CRC
    crc_rx = (resp[3 + byte_count + 1] << 8) | resp[3 + byte_count]
    crc_calc = modbus_crc(resp[:3 + byte_count])
    if crc_rx != crc_calc:
        return None
    return data


def parse_write_response(resp: bytes) -> bool:
    """Validate a FC 0x10 response (8 bytes)."""
    if len(resp) < 8:
        return False
    if resp[1] != 0x10:
        return False
    crc_rx = (resp[7] << 8) | resp[6]
    crc_calc = modbus_crc(resp[:6])
    return crc_rx == crc_calc


# ── Serial helpers ────────────────────────────────────────────────────

def open_serial(port: str, baud: int = 2400):
    import serial
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=1.0,
    )


def send_frame(ser, frame: bytes) -> None:
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()


def recv_response(ser, expected_len: int, timeout: float = 1.5) -> bytes:
    ser.timeout = timeout
    resp = ser.read(expected_len)
    # drain any trailing bytes
    time.sleep(0.05)
    ser.reset_input_buffer()
    return resp


# ── Meter operations ──────────────────────────────────────────────────

def read_serial_number(ser, dev_id: int = 1) -> str | None:
    """Read 12-digit serial number from register 0x0010 (3 regs, 6 BCD bytes)."""
    frame = build_read_frame(dev_id, 0x0010, 3)
    send_frame(ser, frame)
    resp = recv_response(ser, 3 + 3 + 2 + 5)  # generous buffer
    data = parse_read_response(resp, 6)
    if data is None:
        return None
    sn = ""
    for byte in data:
        sn += f"{(byte >> 4) & 0xF:X}{(byte & 0xF):X}"
    return sn


def read_relay_state(ser, dev_id: int) -> int | None:
    """Read relay state from register 0x0027 (1 register)."""
    frame = build_read_frame(dev_id, 0x0027, 1)
    send_frame(ser, frame)
    resp = recv_response(ser, 3 + 2 + 2 + 5)
    data = parse_read_response(resp, 2)
    if data is None:
        return None
    return (data[0] << 8) | data[1]


def write_meter_address(ser, current_id: int, serial_number: str, new_id: int) -> bool:
    """Assign a new Modbus address to the meter.

    The DDS8888 requires the 12-digit serial number as authentication.
    Register 0x000F, 4 regs of payload (8 bytes = new_id + 6-byte SN BCD).
    """
    payload = bytearray(8)
    payload[0] = 0x00
    payload[1] = new_id
    # pack serial number as BCD bytes
    for i in range(6):
        hi = int(serial_number[i * 2], 16)
        lo = int(serial_number[i * 2 + 1], 16)
        payload[2 + i] = (hi << 4) | lo

    frame = build_write_frame(current_id, 0x000F, bytes(payload))
    send_frame(ser, frame)
    resp = recv_response(ser, 8)
    if not parse_write_response(resp):
        return False
    time.sleep(0.1)
    return True


def ping_meter(ser, dev_id: int) -> bool:
    """Quick check: can we read register 0x0027 from this ID?"""
    return read_relay_state(ser, dev_id) is not None


def list_ports():
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports detected.")
        return []
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device}  —  {p.description}")
    return [p.device for p in ports]


def detect_port():
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    # prefer USB-serial adapters
    for p in ports:
        desc = p.description.lower()
        if "usb" in desc or "serial" in desc or "ftdi" in desc or "ch340" in desc or "cp210" in desc:
            return p.device
    # fallback: first available port
    if ports:
        return ports[0].device
    return None


# ── Interactive commands ──────────────────────────────────────────────

def cmd_assign(args, ser) -> None:
    """Assign a new Modbus address to the meter currently at factory ID 1."""
    factory_id = args.factory_id

    print(f"\nLooking for meter at factory Modbus ID {factory_id} (2400-8E1)...")
    sn = read_serial_number(ser, factory_id)

    if sn is None:
        print(f"\n  No response from Modbus ID {factory_id}.")
        print(f"  Check: USB-to-RS485 connected? Meter powered? A/B not swapped?")
        sys.exit(1)

    print(f"  Meter found!")
    print(f"  Serial number: {sn}")

    # pick address
    if args.address is not None:
        new_id = args.address
    else:
        # scan for already-assigned meters so we can suggest available IDs
        print("\n  Scanning for already-assigned meters (IDs 2–11)...")
        occupied = []
        for mid in range(2, 12):
            if ping_meter(ser, mid):
                occupied.append(mid)
        if occupied:
            print(f"  Occupied IDs: {', '.join(str(x) for x in occupied)}")
        else:
            print("  No occupied IDs found.")

        available = [i for i in range(2, 12) if i not in occupied]
        if not available:
            print("\n  ERROR: All IDs 2–11 are occupied. Cannot assign.")
            sys.exit(1)
        print(f"  Available IDs: {', '.join(str(x) for x in available)}")

        while True:
            try:
                choice = input(f"\n  Enter new Modbus ID [{available[0]}]: ").strip()
                if not choice:
                    new_id = available[0]
                else:
                    new_id = int(choice)
                if new_id not in available:
                    print(f"  ID {new_id} is not in the available range. Try again.")
                    continue
                break
            except ValueError:
                print("  Please enter a number.")
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(0)

    # confirm
    if not args.yes:
        try:
            confirm = input(f"\n  Assign ID {factory_id} → {new_id} for SN {sn}? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if confirm and confirm not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    # write
    print(f"\n  Writing new Modbus ID {new_id}...")
    ok = write_meter_address(ser, factory_id, sn, new_id)
    if not ok:
        print(f"  ERROR: Write failed. Meter may still be at ID {factory_id}.")
        sys.exit(1)

    # verify
    time.sleep(0.3)
    print(f"  Verifying at new address {new_id}...")
    sn2 = read_serial_number(ser, new_id)
    if sn2 == sn:
        print(f"  SUCCESS: Meter {sn} is now at Modbus ID {new_id}.")
    else:
        print(f"  WARNING: Verification read returned {sn2} (expected {sn}).")
        print(f"  The write may have succeeded — try scanning ID {new_id} manually.")
        sys.exit(1)

    # print label
    print()
    print("  ╔══════════════════════════════╗")
    print(f"  ║  METER ADDRESS: {new_id:<13} ║")
    print(f"  ║  SN: {sn:<23} ║")
    print("  ╠══════════════════════════════╣")
    print("  ║  Write this number on the   ║")
    print("  ║  meter housing with a       ║")
    print("  ║  permanent marker or label. ║")
    print("  ╚══════════════════════════════╝")
    print()
    print(f"  Label:  [ ADDR {new_id} ]    (attach to meter housing)")


def cmd_scan(args, ser) -> None:
    """Scan all Modbus IDs and report what's present."""
    print("\nScanning Modbus IDs 1–11 at 2400-8E1...\n")
    print("  ID    Serial Number     Relay")
    print("  ----  ----------------  -----")
    for mid in range(1, 12):
        sn = read_serial_number(ser, mid)
        if sn is not None:
            relay = read_relay_state(ser, mid)
            relay_str = "CLOSED" if relay == 1 else ("OPEN" if relay == 0 else "?")
            status = "FACTORY" if mid == 1 else ""
            print(f"  {mid:>2}    {sn}     {relay_str}  {status}")
    print()


def cmd_identify(_args, ser) -> None:
    """Read and display serial number only (no write)."""
    for mid in [1, 2]:
        sn = read_serial_number(ser, mid)
        if sn:
            print(f"\n  Meter at ID {mid}: SN = {sn}")
            return
    print("\n  No meter found at ID 1 or 2.")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DDS8888 Modbus address assignment tool"
    )
    parser.add_argument(
        "--port", "-p",
        help="Serial port (e.g. COM3, /dev/ttyUSB0). Auto-detected if omitted."
    )
    parser.add_argument(
        "--baud", type=int, default=2400,
        help="Baud rate (default: 2400)"
    )
    parser.add_argument(
        "--factory-id", type=int, default=1,
        help="Factory-default Modbus ID to look for (default: 1)"
    )
    parser.add_argument(
        "--address", "-a", type=int,
        help="New Modbus ID to assign (non-interactive)."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt."
    )
    parser.add_argument(
        "--list-ports", action="store_true",
        help="List available serial ports and exit."
    )

    sub = parser.add_subparsers(dest="command", help="Operation")

    sp_assign = sub.add_parser("assign", help="Assign a new address (default)")
    sp_assign.set_defaults(func=cmd_assign)

    sp_scan = sub.add_parser("scan", help="Scan all IDs and list meters")
    sp_scan.set_defaults(func=cmd_scan)

    sp_id = sub.add_parser("identify", help="Read serial number only")
    sp_id.set_defaults(func=cmd_identify)

    args = parser.parse_args()

    if args.list_ports:
        list_ports()
        sys.exit(0)

    # resolve port
    port = args.port or detect_port()
    if not port:
        print("ERROR: No serial port found. Use --list-ports to see available ports.")
        sys.exit(1)

    # open port
    try:
        ser = open_serial(port, args.baud)
    except Exception as e:
        print(f"ERROR: Could not open {port}: {e}")
        sys.exit(1)

    print(f"Connected: {port} @ {args.baud}-8E1")

    try:
        if args.command == "scan":
            cmd_scan(args, ser)
        elif args.command == "identify":
            cmd_identify(args, ser)
        else:
            # default: assign
            cmd_assign(args, ser)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
