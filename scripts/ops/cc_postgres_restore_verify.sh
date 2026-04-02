#!/usr/bin/env bash
set -euo pipefail

umask 077
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/1pwr-cc}"
S3_BUCKET="${S3_BUCKET:-}"
AWS_REGION="${AWS_REGION:-af-south-1}"
S3_PREFIX="${S3_PREFIX:-customer-care}"
HOST_NAME="${HOST_NAME:-$(hostname -s)}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

for cmd in pg_restore psql python3 sqlite3 sudo; do
  require_cmd "$cmd"
done

resolve_backup_dir() {
  if [[ $# -gt 0 && -n "${1:-}" ]]; then
    echo "$BACKUP_ROOT/$1"
    return
  fi

  python3 - "$BACKUP_ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
candidates = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
if not candidates:
    raise SystemExit(1)
print(candidates[0])
PY
}

verify_sqlite_backup() {
  local sqlite_backup="$1"
  sqlite3 "$sqlite_backup" 'PRAGMA integrity_check;'
}

table_count() {
  local database="$1"
  local table_name="$2"
  sudo -u postgres psql -d "$database" -tAc "SELECT COUNT(*) FROM $table_name;"
}

build_report() {
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12}" "${13}" <<'PY'
from pathlib import Path
import json
import sys

report_path = Path(sys.argv[1])
backup_dir = Path(sys.argv[2])
sqlite_status = sys.argv[3]
ls_db = sys.argv[4]
ls_customers = int(sys.argv[5])
ls_accounts = int(sys.argv[6])
ls_meters = int(sys.argv[7])
ls_mutations = int(sys.argv[8])
bn_db = sys.argv[9]
bn_customers = int(sys.argv[10])
bn_accounts = int(sys.argv[11])
bn_meters = int(sys.argv[12])
bn_mutations = int(sys.argv[13])

report = {
    "backup_dir": str(backup_dir),
    "sqlite_integrity_check": sqlite_status,
    "restore_checks": {
        ls_db: {
            "customers": ls_customers,
            "accounts": ls_accounts,
            "meters": ls_meters,
            "cc_mutations": ls_mutations,
        },
        bn_db: {
            "customers": bn_customers,
            "accounts": bn_accounts,
            "meters": bn_meters,
            "cc_mutations": bn_mutations,
        },
    },
}
report_path.write_text(json.dumps(report, indent=2))
print(report_path)
PY
}

backup_dir="$(resolve_backup_dir "${1:-}")"
if [[ ! -d "$backup_dir" ]]; then
  if [[ -n "${1:-}" && -n "$S3_BUCKET" ]]; then
    mkdir -p "$backup_dir"
    aws s3 cp \
      "s3://$S3_BUCKET/$S3_PREFIX/$HOST_NAME/$1/" \
      "$backup_dir/" \
      --recursive \
      --region "$AWS_REGION" \
      --only-show-errors
  else
    echo "Backup directory not found: $backup_dir" >&2
    exit 1
  fi
fi

ls_dump="$backup_dir/onepower_cc.dump"
bn_dump="$backup_dir/onepower_bj.dump"
sqlite_backup="$backup_dir/cc_auth.db.backup"

for file_path in "$ls_dump" "$bn_dump" "$sqlite_backup"; do
  if [[ ! -f "$file_path" ]]; then
    echo "Missing backup artifact: $file_path" >&2
    exit 1
  fi
done

restore_tag="$(date -u +%Y%m%d%H%M%S)"
ls_restore_db="cc_restore_ls_${restore_tag}"
bn_restore_db="cc_restore_bn_${restore_tag}"

cleanup() {
  sudo -u postgres dropdb --if-exists "$ls_restore_db" >/dev/null 2>&1 || true
  sudo -u postgres dropdb --if-exists "$bn_restore_db" >/dev/null 2>&1 || true
}
trap cleanup EXIT

chgrp postgres "$BACKUP_ROOT" "$backup_dir" "$ls_dump" "$bn_dump"
chmod 750 "$BACKUP_ROOT" "$backup_dir"
chmod 640 "$ls_dump" "$bn_dump"

sudo -u postgres createdb "$ls_restore_db"
sudo -u postgres createdb "$bn_restore_db"

sudo -u postgres pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$ls_restore_db" "$ls_dump" >/dev/null
sudo -u postgres pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$bn_restore_db" "$bn_dump" >/dev/null

sqlite_status="$(verify_sqlite_backup "$sqlite_backup")"
if [[ "$sqlite_status" != "ok" ]]; then
  echo "SQLite integrity check failed: $sqlite_status" >&2
  exit 1
fi

report_path="$backup_dir/restore-verify-${restore_tag}.json"
build_report \
  "$report_path" \
  "$backup_dir" \
  "$sqlite_status" \
  "$ls_restore_db" \
  "$(table_count "$ls_restore_db" customers)" \
  "$(table_count "$ls_restore_db" accounts)" \
  "$(table_count "$ls_restore_db" meters)" \
  "$(table_count "$ls_restore_db" cc_mutations)" \
  "$bn_restore_db" \
  "$(table_count "$bn_restore_db" customers)" \
  "$(table_count "$bn_restore_db" accounts)" \
  "$(table_count "$bn_restore_db" meters)" \
  "$(table_count "$bn_restore_db" cc_mutations)"

if [[ -n "$S3_BUCKET" ]]; then
  aws s3 cp \
    "$report_path" \
    "s3://$S3_BUCKET/$S3_PREFIX/$HOST_NAME/$(basename "$backup_dir")/$(basename "$report_path")" \
    --region "$AWS_REGION" \
    --only-show-errors
fi

echo "Restore verification complete: $report_path"
