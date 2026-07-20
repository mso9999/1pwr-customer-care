# inverter_readings.raw_json prune

## Background (2026-06-17 disk emergency RCA)

The CC host root filesystem hit 93% (8.6 GB free of 116 GB). Root cause:

- `inverter_readings.raw_json` (JSONB) held **24 GB** of raw inverter payloads
  in its TOAST table (~12.3M chunks, 0 dead — genuine data, not bloat). Every
  gensite adapter (`alphaess`, `sinosoar`, `solarman`, `victron`, `sma`) writes
  the full raw payload "for forensic inspection", at ~1 GB/day, but **nothing in
  the application ever reads `raw_json` back**.
- This also bloated every nightly `pg_dump` (each `onepower_cc.dump` was ~12 GB
  and growing) and made `--compress=9` backups take 2h40m+.

Secondary contributors handled during the incident:
- `hourly_consumption_old` (8.7 GB) — orphaned legacy table from migration 044
  ("DROP after 24h"), dropped.
- Local backup retention reduced and pg_dump compression lowered.

## What this job does

`cc_prune_inverter_rawjson.sh` nulls `raw_json` for rows older than
`RETAIN_DAYS` (default 7) and runs a plain `VACUUM (ANALYZE)` so the freed TOAST
space is reusable. This keeps a short rolling window for debugging while
bounding the table to its ~working set (~7 GB at 7 days).

The structured columns (`ac_kw`, `battery_soc_pct`, etc.) are never touched —
only the redundant raw payload is dropped.

## One-time bulk reclaim (done manually during the incident)

```sql
UPDATE inverter_readings SET raw_json = NULL
WHERE raw_json IS NOT NULL AND ts_utc < now() - interval '7 days';
VACUUM FULL inverter_readings;   -- returns the ~16 GB to the OS
```

`VACUUM FULL` takes an ACCESS EXCLUSIVE lock and rewrites the table; it needs
free space roughly equal to the final (post-prune) size. Run it only when the
nightly backup is NOT holding locks.

## Install on the host

```bash
sudo install -m 0755 cc_prune_inverter_rawjson.sh /usr/local/bin/
sudo install -m 0644 cc-inverter-rawjson-prune.service /etc/systemd/system/
sudo install -m 0644 cc-inverter-rawjson-prune.timer  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cc-inverter-rawjson-prune.timer
systemctl list-timers cc-inverter-rawjson-prune.timer
```

## Tuning

- `RETAIN_DAYS` is set in the service unit (`Environment=RETAIN_DAYS=7`). At
  ~1 GB/day the within-window footprint is roughly `RETAIN_DAYS` GB; lower it if
  the host gets tight again.
- A deeper fix would be to stop persisting `raw_json` (or store a trimmed
  payload) in the gensite adapters, since it is write-only today.
