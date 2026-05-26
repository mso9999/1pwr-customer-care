# 1Meter HQ Meter Addressing File Pack

Pack version: 1.1  
Last updated: 2026-05-26  
Owner: CC/1Meter operations

## Purpose

Define the exact file set HQ uses for meter-address assignment and keep version increments auditable.

## Pack contents (logical)

1. Addressing script:
   - `set_meter_address.py`
2. Dependency lock/requirements for meterenv:
   - `requirements.txt` (or equivalent frozen set)
3. Operator SOP:
   - `1meter-hq-meter-addressing-sop.md`
4. Quick troubleshooting card:
   - "No response from Modbus ID 1 / empty scan" checklist
5. Bench capture template:
   - serial number, assigned ID, operator, date/time, notes

## Release checklist

- Validate script runs on clean Windows venv.
- Validate `--list-ports`, probe, and scan commands.
- Re-run failure-mode simulation notes against current adapter models.
- Update SOP version history when troubleshooting guidance changes.
- Increment pack version in this file.

## Operational notes

- `idf.py monitor` is not part of this file pack or workflow.
- Empty scan output indicates comms-path issue first (wiring/polarity/power/adapter), not meter firmware.
- Keep HQ and field responsibilities separate: HQ assigns IDs, field team flashes and installs.

## Version history

- v1.1 (2026-05-26):
  - Added explicit comms triage for zero-response scans.
  - Clarified ESP-IDF monitor is out-of-scope for HQ addressing.
  - Added release checklist gate to verify failure-mode guidance per adapter model.
- v1.0:
  - Initial file pack definition.
