#!/usr/bin/env bash
#
# ccdb.sh — direct psql access to the production 1PDB databases from a workstation.
#
# The CC PostgreSQL instance listens only on the production host's localhost, so
# there is no direct TCP path from a laptop. This wrapper SSHes to the CC host
# and runs `psql` as the postgres superuser, streaming SQL over stdin so we never
# fight nested shell quoting.
#
# Usage:
#   scripts/ops/ccdb.sh -c "SELECT count(*) FROM transactions;"          # LS (default)
#   scripts/ops/ccdb.sh --bn -c "SELECT count(*) FROM transactions;"     # Benin
#   scripts/ops/ccdb.sh -f path/to/query.sql                             # run a local .sql file
#   echo "SELECT now();" | scripts/ops/ccdb.sh                           # pipe SQL via stdin
#   scripts/ops/ccdb.sh --psql-args "-A -F, " -c "..."                   # extra raw psql flags
#
# Environment overrides:
#   CC_SSH_KEY   default: /Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem
#   CC_SSH_HOST  default: ubuntu@cc.1pwrafrica.com
#   CC_DB        default: onepower_cc  (use --bn or CC_DB=onepower_bj for Benin)
#
# Notes:
#   * Read-only by default in spirit; this tool will run whatever SQL you pass,
#     so treat writes/migrations with the same care as any production change.
#   * ON_ERROR_STOP is enabled so multi-statement scripts fail fast (RCA-first).
set -euo pipefail

CC_SSH_KEY="${CC_SSH_KEY:-/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem}"
CC_SSH_HOST="${CC_SSH_HOST:-ubuntu@cc.1pwrafrica.com}"
CC_DB="${CC_DB:-onepower_cc}"

SQL=""
SQL_FILE=""
PSQL_EXTRA="-tA"

usage() {
  sed -n '2,30p' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bn) CC_DB="onepower_bj"; shift ;;
    --ls) CC_DB="onepower_cc"; shift ;;
    --db) CC_DB="$2"; shift 2 ;;
    -c|--command) SQL="$2"; shift 2 ;;
    -f|--file) SQL_FILE="$2"; shift 2 ;;
    --psql-args) PSQL_EXTRA="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 1 ;;
  esac
done

if [[ ! -r "${CC_SSH_KEY}" ]]; then
  echo "SSH key not readable: ${CC_SSH_KEY}" >&2
  exit 1
fi

# Resolve the SQL payload precedence: -c, then -f, then stdin.
if [[ -n "${SQL}" ]]; then
  SQL_PAYLOAD="${SQL}"
elif [[ -n "${SQL_FILE}" ]]; then
  if [[ ! -r "${SQL_FILE}" ]]; then
    echo "SQL file not readable: ${SQL_FILE}" >&2
    exit 1
  fi
  SQL_PAYLOAD="$(cat "${SQL_FILE}")"
else
  SQL_PAYLOAD="$(cat)"
fi

if [[ -z "${SQL_PAYLOAD// /}" ]]; then
  echo "No SQL provided (use -c, -f, or pipe via stdin)." >&2
  exit 1
fi

# Stream SQL over stdin into psql on the host. The remote command reads stdin,
# so the SQL body never has to survive an extra layer of shell quoting.
printf '%s\n' "${SQL_PAYLOAD}" | ssh \
  -i "${CC_SSH_KEY}" \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=20 \
  "${CC_SSH_HOST}" \
  "sudo -u postgres psql -d $(printf '%q' "${CC_DB}") -v ON_ERROR_STOP=1 ${PSQL_EXTRA} -f -"
