#!/usr/bin/env python3
"""
Periodic low-balance SMS from 1PDB (see ``acdb-api/low_balance_alerts.py``).

Env:
  DATABASE_URL                  PostgreSQL (defaults Lesotho local).
  COUNTRY_CODE                  LS | BN (defaults LS).
  LOW_BALANCE_ALERTS_ENABLED    Default ``0``. Set ``1`` on the CC host to send.
  SMS_SERVER_URL                Same as CC API — gateway ``generate_and_send.php``.
  DRY_RUN                       ``1`` logs candidates without SMS or DB writes.

Install: copy ``deploy/systemd/cc-low-balance-alerts.{service,timer}``, enable timer.

Usage (repo dev)::

    cd acdb-api && PYTHONPATH=. LOW_BALANCE_ALERTS_ENABLED=1 DRY_RUN=1 \\
      python3 ../scripts/ops/run_low_balance_alerts.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "acdb-api"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("run-low-balance-alerts")


def main() -> int:
    p = argparse.ArgumentParser(description="Low-balance SMS job (1PDB)")
    p.add_argument("--dry-run", action="store_true", help="No SMS, no DB updates")
    args = p.parse_args()

    dry = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    enabled = os.environ.get("LOW_BALANCE_ALERTS_ENABLED", "0").lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled and not dry:
        log.info("LOW_BALANCE_ALERTS_ENABLED is off — exiting (use DRY_RUN=1 to preview)")
        return 0

    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://cc_api@localhost:5432/onepower_cc",
    )

    from customer_api import get_connection
    from low_balance_alerts import low_balance_tick

    with get_connection() as conn:
        stats = low_balance_tick(conn, dry_run=dry)

    log.info(
        "done dry_run=%s seen=%s cleared=%s sent=%s would_send=%s no_phone=%s skip_dup=%s "
        "threshold=%s clear=%s",
        stats.get("dry_run"),
        stats.get("accounts_seen"),
        stats.get("cleared"),
        stats.get("sent"),
        stats.get("would_send"),
        stats.get("skipped_no_phone"),
        stats.get("skipped_already_warned"),
        stats.get("warn_kwh"),
        stats.get("clear_kwh"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
