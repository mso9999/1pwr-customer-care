# Solarman Historical Archive Runbook (Hourly First)

Use this before any Solarman unbind/rebind or DeyeCloud recreation.

Goal: preserve the highest-resolution historical telemetry available, ideally hourly.

## 1) Export scope to capture

For each plant and each device serial:

- Device metadata (SN, model, logger type, timezone, site name)
- Energy history (daily + monthly aggregates)
- Power history at the smallest interval Solarman allows
- Alarm/fault history

If Solarman offers interval choices, choose in this order:

1. **Hourly** (preferred)
2. 15-minute or 5-minute (better than hourly)
3. Daily (last resort)

## 2) Export window strategy

Portal exports may cap rows. Use chunked windows to guarantee full retention.

Recommended sequence:

- Last 30 days at highest resolution
- Then month-by-month backfill for full history

For each chunk, export:

- Interval power CSV
- Daily totals CSV
- Alarm/event CSV (same time window)

## 3) Archive folder layout

Store files locally under:

`archives/solarman/<YYYY-MM-DD>/<site>/<device_sn>/`

Suggested file names:

- `identity.json`
- `interval_<YYYY-MM>.csv`
- `daily_<YYYY-MM>.csv`
- `alarms_<YYYY-MM>.csv`
- `portal_screenshots/<...>.png` (optional evidence)
- `manifest.json` (generated)

## 4) Validate hourly granularity

Run:

```bash
python3 scripts/ops/build_solarman_archive_manifest.py \
  --archive-root "archives/solarman/$(date +%F)" \
  --output "archives/solarman/$(date +%F)/manifest.json"
```

The script reports per-CSV inferred cadence:

- `hourly` (good)
- `sub_hourly` (good, higher resolution)
- `daily_or_coarser` (warning for interval objective)
- `unknown` (timestamp parsing failed)

## 5) Cutover gate (do not proceed until true)

Before unbind/rebind, confirm:

- Every target device has at least one interval CSV
- Interval CSV cadence is hourly/sub-hourly for the required period
- Manifest generated and saved
- Archive copied to durable storage (shared drive + backup)

## 6) If hourly export is unavailable in portal

Fallback order:

1. Export the smallest interval available (even if suboptimal)
2. Capture XHR responses from browser network tab for interval endpoints
3. Save raw JSON payloads into `raw_api/` under same device folder

Note: preserving **any** raw interval history before unbind is more important than waiting for a perfect export.

