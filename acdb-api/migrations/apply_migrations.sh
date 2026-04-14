#!/usr/bin/env bash
# Apply all *.sql migrations in lexical order (001, 002, … 010).
# Idempotent migrations only (IF NOT EXISTS / safe re-run).
#
# Usage:
#   DATABASE_URL=postgresql://... ./apply_migrations.sh
# Or on the CC host (peer auth as postgres):
#   sudo -u postgres env DATABASE_URL=... ./apply_migrations.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "apply_migrations.sh: DATABASE_URL is required" >&2
  exit 1
fi

shopt -s nullglob
files=( "${ROOT}"/*.sql )
IFS=$'\n' sorted=( $(printf '%s\n' "${files[@]}" | sort -V) )

if [[ ${#sorted[@]} -eq 0 ]]; then
  echo "No .sql files in ${ROOT}" >&2
  exit 1
fi

for f in "${sorted[@]}"; do
  echo "Applying $(basename "$f")..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

echo "All migrations applied successfully."
