#!/usr/bin/env bash
set -euo pipefail

REMOTE="${MM_BACKFILL_DROPBOX_REMOTE:-dropbox:1PWR/1PWR Financial Records/mobile money records}"
DEST="${MM_BACKFILL_DEST:-/home/ubuntu/mm-backfill-data/mobile-money-records}"
LOG="${MM_BACKFILL_RCLONE_LOG:-/home/ubuntu/mm-backfill-data/rclone-sync.log}"
LOCK="${MM_BACKFILL_RCLONE_LOCK:-/home/ubuntu/mm-backfill-data/rclone-sync.lock}"

mkdir -p "$(dirname "$DEST")" "$(dirname "$LOG")"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another mobile-money Dropbox sync is already running (lock: $LOCK)" >&2
  exit 0
fi

exec >>"$LOG" 2>&1
echo "=== $(date -Is) starting rclone sync ==="
echo "remote=$REMOTE dest=$DEST"

rclone sync "$REMOTE" "$DEST" \
  --fast-list \
  --transfers=8 \
  --checkers=16 \
  --log-level INFO \
  --stats 1m \
  --stats-one-line

echo "=== $(date -Is) rclone sync finished ==="
