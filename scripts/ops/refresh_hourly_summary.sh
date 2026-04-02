#!/usr/bin/env bash
set -euo pipefail

# Refresh the mv_hourly_account_summary materialized view.
# CONCURRENTLY allows reads during refresh (requires unique index).
# Called by systemd timer and optionally by import scripts.

DB="${1:-onepower_cc}"

sudo -u postgres psql -d "$DB" -c \
  "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_hourly_account_summary;"

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') mv_hourly_account_summary refreshed ($DB)"
