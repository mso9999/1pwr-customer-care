# DeyeCloud Logger Onboarding Checklist

Use with:
- `docs/ops/deyecloud-logger-onboarding-tracker-2026-05-25.csv`
- `scripts/ops/deye_add_loggers_and_verify.py`

## API-assisted execution

Dry-run verification only (no logger add):

```bash
python3 scripts/ops/deye_add_loggers_and_verify.py \
  --tracker-csv docs/ops/deyecloud-logger-onboarding-tracker-2026-05-25.csv \
  --env-file /opt/1pdb/.env \
  --output-csv /tmp/deye_verify_report.csv \
  --output-json /tmp/deye_verify_report.json
```

Add missing loggers, then verify:

```bash
python3 scripts/ops/deye_add_loggers_and_verify.py \
  --tracker-csv docs/ops/deyecloud-logger-onboarding-tracker-2026-05-25.csv \
  --env-file /opt/1pdb/.env \
  --add-loggers \
  --output-csv /tmp/deye_add_and_verify_report.csv \
  --output-json /tmp/deye_add_and_verify_report.json
```

## Per logger sequence

1. Create/select the target power station in Deye Cloud.
2. Add logger serial number (SN).
3. If prompted, complete Wi-Fi/configuration flow for the logger.
4. Confirm UI shows success (`Successfully adapted`).
5. Wait up to 10 minutes for telemetry ingestion (per Deye app message).
6. Confirm UI shows logger/device as online.
7. Verify API `device/list` includes the SN.
8. Verify API `device/latest` returns non-empty data for at least one associated **inverter** SN.
   (Collector/logger SNs can appear in `device/list` while returning empty `device/latest` rows.)
9. Update tracker row (`status=done` only when steps 4-8 pass).

Important: copy SN directly using UI copy icon where possible.
Manual transcription can cause false API failures (`0` vs `O`, or missing digit).

## Video-confirmed UI path (from `822_1779693946.mp4`)

Observed sequence in Deye app:

1. `Create a Plant`
   - Fill `Plant Name`, `Administrative Area`, `Address`, `Coordinates`, `Time Zone`
   - Fill system info (`System Type`, `Installed Capacity (kWp)`)
2. Confirm plant creation (`The plant was successfully created`)
3. `Add a Logger` by SN
4. Open logger page -> `Wi-Fi configuration`
5. Enter AP/Wi-Fi password as needed, then start configuration
6. Progress states: `Connect to device` -> `Configuring` -> `Restarting` -> `Verified`
7. Success message: `Successfully adapted` and note:
   - `Device data will be displayed in 10 mins`

## Status values (tracker)

- `pending`: not started
- `in_progress`: station/logger work started
- `blocked`: cannot proceed (capture reason in notes)
- `done`: online in UI and visible in API with live payload

## Escalation trigger

Escalate to Deye immediately when:
- UI shows logger online but API cannot see SN, or
- API sees SN but `device/latest` remains empty for >30 minutes (or >20 minutes after `Successfully adapted`).

Before escalating, re-check the exact SN string from UI copy/paste.

Include in escalation payload:
- logger SN
- Deye station ID/name
- companyId/appId
- UTC timestamp of verification attempts
- exact API responses

