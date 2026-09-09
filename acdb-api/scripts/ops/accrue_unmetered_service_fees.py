#!/usr/bin/env python3
"""
Monthly service-fee accrual for unmetered (connected, no meter) accounts.

For every ``unmetered_service`` row with ``status = 'active'``, this script
adds the enrollment's ``monthly_fee`` snapshot to ``outstanding`` and writes an
``accrual`` row to ``unmetered_service_ledger``. The ledger has a partial
unique index on ``(service_id, accrual_period)`` for ``entry_type = 'accrual'``
so the job is safe to re-run within the same month.

Enrollments whose account now has an active meter are auto-ended
(``meter_assigned``) instead of accruing — the safety net behind the
meter-assignment / commissioning exit hooks.

Run via the ``cc-unmetered-accrual.timer`` systemd timer (1st of the month).
Manual invocation:

    sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \\
        /opt/cc-portal/backend/scripts/ops/accrue_unmetered_service_fees.py \\
        --period 2026-09

Exit codes:
  0  OK (zero or more accruals applied)
  1  database error
  2  CLI argument error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from typing import Optional

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger("cc-ops.accrue-unmetered-service-fees")


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.stderr.write("DATABASE_URL is not set\n")
        sys.exit(2)
    return url


def _validate_period(period: str) -> str:
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        sys.stderr.write(f"--period must be YYYY-MM, got {period!r}\n")
        sys.exit(2)
    return period


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--period",
        default=date.today().strftime("%Y-%m"),
        help="Accrual period in YYYY-MM (default: current month UTC)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute and log but do not commit any changes",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress INFO logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    period = _validate_period(args.period)
    database_url = (args.database_url or _resolve_database_url()).strip()

    from unmetered_service import accrue_period

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        try:
            summary = accrue_period(conn, period, dry_run=args.dry_run)
        except psycopg2.Error as exc:
            conn.rollback()
            logger.error("DB error during unmetered service accrual: %s", exc)
            return 1
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    logger.info(
        "Unmetered accrual %s done: considered=%d applied=%d skipped_duplicate=%d "
        "auto_ended_metered=%d total_fees=%.2f%s",
        period, summary["enrollments_considered"], summary["accruals_applied"],
        summary["accruals_skipped_duplicate"], summary["auto_ended_metered"],
        summary["total_fee_amount"],
        " (dry-run)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
