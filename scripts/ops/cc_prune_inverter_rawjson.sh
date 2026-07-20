#!/usr/bin/env bash
#
# Nightly prune of inverter_readings.raw_json older than RETAIN_DAYS.
#
# Why: every gensite adapter (alphaess, sinosoar, solarman, victron, sma)
# stores the full raw inverter payload in inverter_readings.raw_json "for
# forensic inspection", but nothing in the app ever reads it back. It grew
# unbounded to 24 GB of TOAST (~1 GB/day) and nearly filled the CC host disk
# on 2026-06-17. We keep a short rolling window for debugging and drop the rest.
#
# A plain VACUUM (not FULL) is enough for the ongoing job: it marks the freed
# TOAST space reusable so the file stabilises around the working set. The
# one-time bulk reclaim (VACUUM FULL) was done manually during the incident.
#
# Runs as the postgres OS user via systemd (peer auth -> superuser), so it can
# both UPDATE and VACUUM the postgres-owned table.
set -euo pipefail

RETAIN_DAYS="${RETAIN_DAYS:-7}"
DB="${DB:-onepower_cc}"
PSQL=(psql -v ON_ERROR_STOP=1 -d "$DB" -X -q)

echo "[$(date -u +%FT%TZ)] pruning inverter_readings.raw_json older than ${RETAIN_DAYS}d in ${DB}"

"${PSQL[@]}" -c "UPDATE inverter_readings
                 SET raw_json = NULL
                 WHERE raw_json IS NOT NULL
                   AND ts_utc < now() - (INTERVAL '1 day' * ${RETAIN_DAYS});"

"${PSQL[@]}" -c "VACUUM (ANALYZE) inverter_readings;"

echo "[$(date -u +%FT%TZ)] prune complete"
