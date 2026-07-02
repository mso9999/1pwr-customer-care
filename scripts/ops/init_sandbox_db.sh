#!/bin/sh
# init_sandbox_db.sh — create + migrate a ring-fenced CC sandbox database.
#
# Strategy B ("separate DB, same image"): the sandbox is the EXACT same CC
# codebase pointed at a different database on the same Postgres cluster.
# This script creates that database (idempotent) and applies the same SQL
# migrations production uses, so the sandbox schema matches production.
#
# No code fork, no second image build — only the data is ring-fenced.
#
# Required env:
#   SANDBOX_DATABASE_URL   DSN to the sandbox DB to create + migrate
#                          e.g. postgresql://cc_api@localhost:5432/onepower_cc_sandbox
# Optional env:
#   ADMIN_DATABASE_URL     DSN with CREATEDB privilege, used to issue CREATE DATABASE.
#                          Defaults to the SANDBOX host with dbname=postgres.
#                          e.g. postgresql://postgres@localhost:5432/postgres
#
# After this runs, start a second CC instance with:
#   DATABASE_URL=$SANDBOX_DATABASE_URL  APP_SANDBOX=1  APP_ENV=sandbox  ...
# and point the app at it via --dart-define=SANDBOX_API_BASE=https://<sandbox-host>/api
#
set -eu

if [ -z "${SANDBOX_DATABASE_URL:-}" ]; then
  echo "init_sandbox_db.sh: SANDBOX_DATABASE_URL is required" >&2
  echo "  e.g. SANDBOX_DATABASE_URL=postgresql://cc_api@localhost:5432/onepower_cc_sandbox \\$0" >&2
  exit 1
fi

# Derive (host, port, user) from the sandbox DSN for the admin connection.
SBX_HOST=$(printf '%s' "$SANDBOX_DATABASE_URL" | sed -nE 's@^[^@]*@([^@]+)@.*@\1@p' | sed -nE 's@:([0-9]+).*@@p')
SBX_PORT=$(printf '%s' "$SANDBOX_DATABASE_URL" | sed -nE 's@.*:([0-9]+)/.*@\1@p')
SBX_DBNAME=$(printf '%s' "$SANDBOX_DATABASE_URL" | sed -nE 's@.*/([^/?]+).*@\1@p')
SBX_USER=$(printf '%s' "$SANDBOX_DATABASE_URL" | sed -nE 's@//([^@]+)@.*@\1@p')

: "${SBX_HOST:=localhost}"
: "${SBX_PORT:=5432}"
: "${SBX_DBNAME:=onepower_cc_sandbox}"
: "${SBX_USER:=cc_api}"

if [ -z "${ADMIN_DATABASE_URL:-}" ]; then
  ADMIN_DATABASE_URL="postgresql://${SBX_USER}@${SBX_HOST}:${SBX_PORT}/postgres"
fi

echo "Sandbox DB : ${SBX_DBNAME} on ${SBX_HOST}:${SBX_PORT}"
echo "Admin DSN  : ${ADMIN_DATABASE_URL}"

# CREATE DATABASE has no IF NOT EXISTS; check pg_database first.
EXISTS=$(psql "$ADMIN_DATABASE_URL" -tAc "SELECT 1 FROM pg_database WHERE datname = '${SBX_DBNAME}'")
if [ "$EXISTS" = "1" ]; then
  echo "Database '${SBX_DBNAME}' already exists — skipping CREATE."
else
  echo "Creating database '${SBX_DBNAME}'..."
  createdb "$ADMIN_DATABASE_URL" "$SBX_DBNAME"
fi

echo "Applying CC SQL migrations to '${SBX_DBNAME}'..."
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATABASE_URL="$SANDBOX_DATABASE_URL" sh "$ROOT/acdb-api/migrations/apply_migrations.sh"

echo
echo "Done. Sandbox DB '${SBX_DBNAME}' is ready."
echo "Seed it via:  curl -X POST ${SANDBOX_API_BASE:-<sandbox-host>/api}/app/sandbox/seed"
echo "  (requires the sandbox CC instance running with APP_SANDBOX=1 against this DSN)"
