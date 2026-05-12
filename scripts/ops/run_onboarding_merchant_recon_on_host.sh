#!/usr/bin/env bash
set -euo pipefail

BASE="/home/ubuntu/onboarding-recon"
DATA_DIR="${MERCHANT_ROOT:-/home/ubuntu/mm-backfill-data/mobile-money-records}"
LOG_DIR="${BASE}/logs"
SCRIPT_DIR="${BASE}/scripts"
WORKBOOK="${BASE}/data/onboarding_workbook.xlsx"
REPORT_CSV="${BASE}/reports/onboarding_merchant_recon.csv"
PROGRESS_JSONL="${LOG_DIR}/merchant_recon.progress.jsonl"
RUN_LOG="${LOG_DIR}/merchant_recon.log"
PID_FILE="${LOG_DIR}/merchant_recon.pid"
MIN_FILES="${MIN_MERCHANT_FILES:-500}"

mkdir -p "${DATA_DIR}" "${LOG_DIR}" "${SCRIPT_DIR}" "${BASE}/reports"

if [[ ! -f "${WORKBOOK}" ]]; then
  echo "Missing workbook: ${WORKBOOK}" >&2
  exit 1
fi

if [[ ! -d "${SCRIPT_DIR}/venv" ]]; then
  python3 -m venv "${SCRIPT_DIR}/venv"
  "${SCRIPT_DIR}/venv/bin/pip" install -q openpyxl psycopg2-binary python-calamine
fi

if [[ ! -f "${SCRIPT_DIR}/reconcile_onboarding_workbook_merchant_exports.py" ]]; then
  echo "Missing reconcile script in ${SCRIPT_DIR}" >&2
  exit 1
fi

file_count="$(find "${DATA_DIR}" -type f 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${file_count}" -lt "${MIN_FILES}" ]]; then
  echo "Merchant export sync not ready (${file_count} files in ${DATA_DIR}; need >= ${MIN_FILES})." >&2
  exit 2
fi

if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "Reconciliation already running (pid $(cat "${PID_FILE}"))." >&2
  exit 3
fi

set -a
if [[ -r /opt/1pdb/.env ]]; then
  # shellcheck disable=SC1091
  source /opt/1pdb/.env
else
  DATABASE_URL="$(sudo grep -m1 '^DATABASE_URL=' /opt/1pdb/.env | cut -d= -f2- | tr -d '"')"
  export DATABASE_URL
fi
set +a
export PYTHONPATH=/opt/cc-portal/backend
export ACDB_API=/opt/cc-portal/backend

: > "${PROGRESS_JSONL}"
nohup "${SCRIPT_DIR}/venv/bin/python" \
  "${SCRIPT_DIR}/reconcile_onboarding_workbook_merchant_exports.py" \
  --workbook "${WORKBOOK}" \
  --merchant-root "${DATA_DIR}" \
  --report-csv "${REPORT_CSV}" \
  --progress-file "${PROGRESS_JSONL}" \
  >> "${RUN_LOG}" 2>&1 &
echo $! > "${PID_FILE}"
echo "Started merchant reconciliation pid $(cat "${PID_FILE}")"
echo "Log: ${RUN_LOG}"
echo "Progress: ${PROGRESS_JSONL}"
echo "Report: ${REPORT_CSV}"
