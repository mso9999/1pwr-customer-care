#!/bin/sh
# Apply all *.sql migrations in version order (001, 002, … 010).
# Idempotent migrations only (IF NOT EXISTS / safe re-run).
#
# POSIX sh — works on dash/bash. Requires psql on PATH.
#
# Usage:
#   DATABASE_URL=postgresql://... ./apply_migrations.sh
#
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "apply_migrations.sh: DATABASE_URL is required" >&2
  exit 1
fi

LIST="$(find "$ROOT" -maxdepth 1 -name '*.sql' -type f | sort -V)"
if [ -z "$LIST" ]; then
  echo "No .sql files in $ROOT" >&2
  exit 1
fi

COUNT=0
for f in $LIST; do
  COUNT=$((COUNT + 1))
  echo "Applying $(basename "$f")..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

if [ "$COUNT" -eq 0 ]; then
  echo "No migration files found" >&2
  exit 1
fi

echo "All migrations applied successfully."
