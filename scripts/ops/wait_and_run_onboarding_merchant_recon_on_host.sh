#!/usr/bin/env bash
set -euo pipefail

BASE="/home/ubuntu/onboarding-recon"
DATA_DIR="${MERCHANT_ROOT:-/home/ubuntu/mm-backfill-data/mobile-money-records}"
WAIT_LOG="${BASE}/logs/wait_sync.log"
MIN_FILES="${MIN_MERCHANT_FILES:-500}"

mkdir -p "${BASE}/logs"
echo "$(date -Is) waiting for merchant export sync in ${DATA_DIR}" >> "${WAIT_LOG}"

while true; do
  file_count="$(find "${DATA_DIR}" -type f 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${file_count}" -ge "${MIN_FILES}" ]]; then
    echo "$(date -Is) sync ready (${file_count} files)" >> "${WAIT_LOG}"
    break
  fi
  echo "$(date -Is) sync pending (${file_count}/${MIN_FILES} files)" >> "${WAIT_LOG}"
  sleep 60
done

exec "${BASE}/scripts/run_onboarding_merchant_recon_on_host.sh"
