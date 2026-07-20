#!/usr/bin/env bash
#
# One-shot disk remediation for the 2026-06-17 CC host disk emergency.
# Waits for the nightly logical backup to release its table locks, then:
#   1. DROP TABLE hourly_consumption_old      (orphaned migration-044 table, ~8.7 GB)
#   2. NULL inverter_readings.raw_json > 7d   (~16 GB of write-only forensic JSON)
#   3. VACUUM FULL inverter_readings          (return the space to the OS)
#   4. Lower pg_dump compression 9 -> 6 and LOCAL_RETENTION_DAYS 3 -> 2
#
# Safe to re-run: every step is idempotent. Logs to stdout (capture with tee).
set -uo pipefail

log() { echo "[$(date -u +%FT%TZ)] $*"; }
PSQL=(sudo -u postgres psql -v ON_ERROR_STOP=1 -d onepower_cc -X -q)

log "waiting for cc-postgres-backup.service to finish (max 3h)..."
for _ in $(seq 1 180); do
  state=$(systemctl is-active cc-postgres-backup.service 2>/dev/null || true)
  if [[ "$state" != "active" && "$state" != "activating" && "$state" != "reloading" ]]; then
    log "backup service state=$state -> proceeding"
    break
  fi
  sleep 60
done
# Extra guard: make sure no pg_dump is still running.
while pgrep -f 'pg_dump.*onepower_cc' >/dev/null 2>&1; do
  log "pg_dump still running, waiting 60s..."
  sleep 60
done

log "df before:"; df -h / | tail -1

log "STEP 1: drop orphaned hourly_consumption_old"
"${PSQL[@]}" -c "DROP TABLE IF EXISTS hourly_consumption_old;" && log "  dropped" || log "  DROP failed"

log "STEP 2: null inverter_readings.raw_json older than 7 days"
"${PSQL[@]}" -c "UPDATE inverter_readings SET raw_json = NULL
                 WHERE raw_json IS NOT NULL
                   AND ts_utc < now() - interval '7 days';" || log "  UPDATE failed"

log "STEP 3: VACUUM FULL inverter_readings (returns space to OS)"
"${PSQL[@]}" -c "VACUUM (FULL, ANALYZE) inverter_readings;" && log "  vacuum full done" || log "  VACUUM FULL failed"

log "STEP 4: tune backup config"
ENVF=/etc/default/cc-postgres-backup
if grep -q '^LOCAL_RETENTION_DAYS=' "$ENVF" 2>/dev/null; then
  sudo sed -i 's/^LOCAL_RETENTION_DAYS=.*/LOCAL_RETENTION_DAYS=2/' "$ENVF" && log "  retention -> 2"
fi
BK=/usr/local/bin/cc_postgres_backup.sh
if grep -q -- '--compress=9' "$BK" 2>/dev/null; then
  sudo sed -i 's/--compress=9/--compress=6/g' "$BK" && log "  pg_dump compress -> 6"
fi

log "df after:"; df -h / | tail -1
log "db sizes:"
"${PSQL[@]}" -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database WHERE datname LIKE 'onepower%' ORDER BY pg_database_size(datname) DESC;"
log "inverter_readings size:"
"${PSQL[@]}" -c "SELECT pg_size_pretty(pg_total_relation_size('inverter_readings')) AS total;"
log "REMEDIATION COMPLETE"
