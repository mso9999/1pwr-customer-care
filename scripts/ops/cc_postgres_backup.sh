#!/usr/bin/env bash
set -euo pipefail

umask 077
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/1pwr-cc}"
S3_BUCKET="${S3_BUCKET:?S3_BUCKET must be set}"
AWS_REGION="${AWS_REGION:-af-south-1}"
S3_PREFIX="${S3_PREFIX:-customer-care}"
LS_ENV_FILE="${LS_ENV_FILE:-/opt/1pdb/.env}"
BN_ENV_FILE="${BN_ENV_FILE:-/opt/1pdb-bn/.env}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-7}"
LOCK_FILE="${LOCK_FILE:-/var/lock/cc-postgres-backup.lock}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

for cmd in aws flock gzip pg_dump python3 sha256sum sqlite3; do
  require_cmd "$cmd"
done

read_env_value() {
  python3 - "$1" "$2" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]

for raw_line in path.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k.strip() != key:
        continue
    print(v.strip().strip('"').strip("'"))
    break
PY
}

db_name_from_url() {
  python3 - "$1" <<'PY'
from urllib.parse import urlparse
import sys

parsed = urlparse(sys.argv[1])
print((parsed.path or "/").lstrip("/"))
PY
}

build_manifest() {
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
from pathlib import Path
import json
import os
import sys

run_dir = Path(sys.argv[1])
timestamp = sys.argv[2]
host_name = sys.argv[3]
ls_db = sys.argv[4]
bn_db = sys.argv[5]
auth_db_name = sys.argv[6]

files = []
for file_path in sorted(run_dir.iterdir()):
    if file_path.name == "manifest.json" or file_path.name.endswith(".sha256"):
        continue
    checksum_path = Path(f"{file_path}.sha256")
    checksum = checksum_path.read_text().split()[0] if checksum_path.exists() else None
    files.append(
        {
            "name": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "sha256": checksum,
        }
    )

manifest = {
    "timestamp_utc": timestamp,
    "host": host_name,
    "databases": [ls_db, bn_db],
    "auth_db": auth_db_name,
    "files": files,
}
print(json.dumps(manifest, indent=2))
PY
}

prune_local_backups() {
  python3 - "$BACKUP_ROOT" "$LOCAL_RETENTION_DAYS" <<'PY'
from pathlib import Path
from datetime import datetime, timedelta, timezone
import shutil
import sys

root = Path(sys.argv[1])
retention_days = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

for child in root.iterdir():
    if not child.is_dir():
        continue
    try:
        stamped = datetime.strptime(child.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        continue
    if stamped < cutoff:
        shutil.rmtree(child, ignore_errors=True)
PY
}

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Backup already running; exiting." >&2
  exit 1
fi

ls_db_url="$(read_env_value "$LS_ENV_FILE" DATABASE_URL)"
bn_db_url="$(read_env_value "$BN_ENV_FILE" DATABASE_URL)"
auth_db_path="$(read_env_value "$LS_ENV_FILE" CC_AUTH_DB || true)"

if [[ -z "$ls_db_url" || -z "$bn_db_url" ]]; then
  echo "DATABASE_URL missing from one or both CC env files." >&2
  exit 1
fi

if [[ -z "$auth_db_path" ]]; then
  auth_db_path="/opt/cc-portal/backend/cc_auth.db"
fi

host_name="$(hostname -s)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$BACKUP_ROOT/$timestamp"
sqlite_backup_path="$run_dir/cc_auth.db.backup"
ls_dump_path="$run_dir/onepower_cc.dump"
bn_dump_path="$run_dir/onepower_bj.dump"

mkdir -p "$run_dir"

echo "Starting CC backup at $timestamp"

pg_dump --format=custom --compress=9 --no-owner --no-privileges --dbname="$ls_db_url" --file="$ls_dump_path"
sha256sum "$ls_dump_path" > "$ls_dump_path.sha256"

pg_dump --format=custom --compress=9 --no-owner --no-privileges --dbname="$bn_db_url" --file="$bn_dump_path"
sha256sum "$bn_dump_path" > "$bn_dump_path.sha256"

sqlite3 "$auth_db_path" ".backup '$sqlite_backup_path'"
sha256sum "$sqlite_backup_path" > "$sqlite_backup_path.sha256"

build_manifest \
  "$run_dir" \
  "$timestamp" \
  "$host_name" \
  "$(db_name_from_url "$ls_db_url")" \
  "$(db_name_from_url "$bn_db_url")" \
  "$(basename "$auth_db_path")" \
  > "$run_dir/manifest.json"

aws s3 cp \
  "$run_dir" \
  "s3://$S3_BUCKET/$S3_PREFIX/$host_name/$timestamp/" \
  --recursive \
  --region "$AWS_REGION" \
  --only-show-errors

prune_local_backups

echo "Backup complete: s3://$S3_BUCKET/$S3_PREFIX/$host_name/$timestamp/"
