# 1Meter HQ Meter Addressing SOP

Version: 1.1  
Last updated: 2026-05-26  
Audience: HQ bench team assigning DDS8888/RS485 meter addresses (not MAK field flashing team)

## Purpose

Provide a repeatable, low-risk process to discover meters on an RS485 bus and set Modbus addresses before field deployment.

## Scope and boundaries

- This SOP is for the Windows HQ bench process using `set_meter_address.py`.
- It is not an ESP-IDF/firmware flashing workflow.
- `idf.py monitor` is not required for meter address assignment.

## Required setup

- Windows laptop with Python virtual env active.
- USB-to-RS485 adapter (true RS485, not TTL UART).
- Meter powered and wired to adapter A/B (or D+/D-).
- Script bundle from the current HQ file pack (see companion file-pack doc).

## Standard command flow

1) List ports:

```powershell
python set_meter_address.py --list-ports
```

2) Probe factory default:

```powershell
python set_meter_address.py --port COM7
```

3) Scan known range:

```powershell
python set_meter_address.py --port COM7 scan
```

## Known failure mode: empty scan / no response

If logs show:

- `No response from Modbus ID 1`
- scan table with zero rows

then treat as RS485 communication failure, not firmware failure.

### Triage sequence (in order)

1. Confirm meter power (display on).  
2. Confirm adapter type is USB-RS485.  
3. Check A/B wiring; swap A/B once if no response.  
4. If available, tie adapter signal GND/COM.  
5. Bench one meter only with short cable.  
6. Confirm TX LED blinks on scan; RX should blink on replies.  
7. Expand scan range (if script supports) and verify serial mode:
   - 2400-8E1 first (factory default expectation),
   - then alternate known configs used by your stock.

## Exit criteria

- At least one meter responds in scan.
- Meter serial number captured.
- Target Modbus ID assigned and re-read confirmed.
- Bench log + photo evidence saved in handover bundle.

## Handover requirements to field team

- Meter serial number.
- Assigned Modbus ID.
- Bench date/time + operator initials.
- Any wiring anomalies observed during discovery.

## Version history

- v1.1 (2026-05-26): Added explicit "no response / empty scan" comms triage and clarified `idf.py monitor` is out-of-scope.
- v1.0: Initial HQ addressing SOP.
