#!/usr/bin/env bash
#
# refresh_merchant_unmatched.sh — sync M-Pesa/EcoCash merchant statements from
# the local Dropbox finance folder to the CC production host and run the
# merchant-payment backfill (book matched payments, park unmatched ones).
#
# WHY THIS EXISTS
#   Merchant-line (PayMerchant) payments never reach the SMS pipeline. They only
#   enter CC when the monthly M-Pesa/EcoCash statement files are imported. Those
#   files are downloaded by finance and synced into Dropbox; the production host
#   has no Dropbox access. This script bridges the two: Dropbox (on this Mac) ->
#   server -> backfill. Run it on a schedule (see the launchd plist) so the
#   "import hasn't run since <date>" gap (2026-06 incident) cannot recur.
#
# USAGE
#   scripts/ops/refresh_merchant_unmatched.sh            # dry-run (default, no DB writes)
#   scripts/ops/refresh_merchant_unmatched.sh apply      # apply: book + park to 1PDB
#
# SAFETY
#   - Default is DRY-RUN. Pass "apply" to write.
#   - --no-repair-credit is always set: never adds kWh / changes balances.
#   - Booking is idempotent: dedup on payment_reference + fuzzy + inbound log;
#     parked rows dedup on a unique receipt index. Re-running is safe.
#   - A timestamped report CSV is pulled back to LOG_DIR every run for review.
#
set -euo pipefail

# ---- Config (edit paths here if the environment changes) --------------------
SSH_KEY="${CC_SSH_KEY:-/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem}"
SRC_DIR="${CC_MM_SRC:-/Users/mattmso/Dropbox/1PWR/1PWR Financial Records/mobile money records}"
# Scope to the MPESA subtree only. The EcoCash statements use a different date
# format the parser does not yet read correctly (dates default to "now", which
# would park thousands of mis-dated junk rows). This matches the proven 2026-06-11
# baseline (every parked row's source_file was under .../MPESA/...). Re-include
# EcoCash only after the parser's EcoCash date handling is fixed. Set to "" to
# sync the whole folder.
MERCHANT_SUBDIR="${CC_MM_SUBDIR-MPESA}"
HOST="${CC_HOST:-ubuntu@cc.1pwrafrica.com}"
REMOTE_DIR="${CC_MM_REMOTE:-/opt/cc-portal/merchant_exports}"
BACKEND="${CC_BACKEND:-/opt/cc-portal/backend}"
ENV_FILE="${CC_ENV_FILE:-/opt/1pdb/.env}"
LOG_DIR="${CC_MM_LOGDIR:-/Users/mattmso/Dropbox/AI Projects/1PWR CC/docs/ops/merchant-refresh-logs}"
# Only consider payments paid on/after this date (keeps each run fast). Default:
# first day of the previous calendar month, so a freshly-dropped month is caught.
SINCE="${CC_MM_SINCE:-$(date -v-1m +%Y-%m-01 2>/dev/null || date -d 'first day of last month' +%Y-%m-01)}"

MODE="${1:-dry-run}"
APPLY_FLAG=""
if [[ "${MODE}" == "apply" ]]; then
  APPLY_FLAG="--apply"
fi

TS="$(date +%Y%m%dT%H%M%S)"
mkdir -p "${LOG_DIR}"
REMOTE_REPORT="/tmp/mm_refresh_${TS}.csv"
LOCAL_REPORT="${LOG_DIR}/mm_refresh_${MODE}_${TS}.csv"
RUN_LOG="${LOG_DIR}/refresh.log"

log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "${RUN_LOG}"; }

log "=== merchant refresh start (mode=${MODE}, since=${SINCE}) ==="

if [[ ! -f "${SSH_KEY}" ]]; then log "FATAL: ssh key not found: ${SSH_KEY}"; exit 1; fi
if [[ ! -d "${SRC_DIR}" ]]; then log "FATAL: source folder not found: ${SRC_DIR}"; exit 1; fi

# ---- 1) Sync statement files (data files only) ------------------------------
SYNC_SRC="${SRC_DIR}"
if [[ -n "${MERCHANT_SUBDIR}" ]]; then SYNC_SRC="${SRC_DIR}/${MERCHANT_SUBDIR}"; fi
if [[ ! -d "${SYNC_SRC}" ]]; then log "FATAL: sync source not found: ${SYNC_SRC}"; exit 1; fi
log "rsync ${SYNC_SRC} -> ${HOST}:${REMOTE_DIR}"
rsync -az --delete --prune-empty-dirs \
  --include='*/' \
  --include='*.csv' --include='*.xlsx' --include='*.xls' --include='*.txt' \
  --exclude='*' \
  -e "ssh -i '${SSH_KEY}' -o StrictHostKeyChecking=accept-new" \
  "${SYNC_SRC}/" "${HOST}:${REMOTE_DIR}/" 2>&1 | tee -a "${RUN_LOG}" | tail -3

# ---- 2) Run the backfill on the server --------------------------------------
log "running backfill (${MODE:-dry-run}) on ${HOST}"
ssh -i "${SSH_KEY}" "${HOST}" "sudo bash -c '
  set -a; source ${ENV_FILE}; set +a
  export DATABASE_URL
  cd ${BACKEND}
  PYTHONPATH=${BACKEND} ${BACKEND}/venv/bin/python scripts/ops/backfill_merchant_payments_from_exports.py \
    --root ${REMOTE_DIR} \
    --since ${SINCE} \
    --no-repair-credit --park-unmatched ${APPLY_FLAG} \
    --report-csv ${REMOTE_REPORT} 2>&1 | tail -12
'" 2>&1 | tee -a "${RUN_LOG}"

# ---- 3) Pull the report back for review -------------------------------------
if scp -i "${SSH_KEY}" "${HOST}:${REMOTE_REPORT}" "${LOCAL_REPORT}" 2>/dev/null; then
  log "report saved: ${LOCAL_REPORT}"
  ssh -i "${SSH_KEY}" "${HOST}" "rm -f ${REMOTE_REPORT}" 2>/dev/null || true
else
  log "WARN: no report produced (nothing parsed?)"
fi

log "=== merchant refresh done (mode=${MODE}) ==="
