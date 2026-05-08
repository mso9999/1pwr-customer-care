#!/usr/bin/env python3
"""
Monthly fee accrual for connection / readyboard advances.

For every ``account_advances`` row with ``status = 'active'`` and
``monthly_fee_pct > 0``, this script adds ``round(outstanding * pct, 2)``
to the outstanding balance and writes a ``monthly_fee`` row to
``account_advance_ledger``. The ledger has a unique index on
``(advance_id, accrual_period)`` for ``entry_type = 'monthly_fee'`` so the
job is safe to re-run within the same month: duplicates are skipped via
``ON CONFLICT DO NOTHING``.

Run via the ``cc-advance-accrual.timer`` systemd timer (see
``scripts/ops/cc-advance-accrual.timer``). Manual invocation:

    sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \\
        /opt/cc-portal/backend/scripts/ops/accrue_advance_fees.py \\
        --period 2026-05

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
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2

logger = logging.getLogger("cc-ops.accrue-advance-fees")


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


def accrue(period: str, *, dry_run: bool, database_url: str) -> dict:
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    summary = {
        "period": period,
        "advances_considered": 0,
        "accruals_applied": 0,
        "accruals_skipped_duplicate": 0,
        "total_fee_amount": 0.0,
    }
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, account_number, advance_type, outstanding,
                   monthly_fee_pct, currency
            FROM account_advances
            WHERE status = 'active' AND monthly_fee_pct > 0 AND outstanding > 0
            ORDER BY id ASC
            FOR UPDATE
            """
        )
        rows = cur.fetchall()
        summary["advances_considered"] = len(rows)

        for advance_id, account, atype, outstanding, pct, currency in rows:
            outstanding = float(outstanding)
            pct = float(pct)
            fee = round(outstanding * pct, 2)
            if fee <= 0:
                continue
            new_outstanding = round(outstanding + fee, 2)

            cur.execute(
                """
                SELECT 1 FROM account_advance_ledger
                WHERE advance_id = %s
                  AND entry_type = 'monthly_fee'
                  AND accrual_period = %s
                LIMIT 1
                """,
                (advance_id, period),
            )
            if cur.fetchone():
                summary["accruals_skipped_duplicate"] += 1
                continue

            if dry_run:
                logger.info(
                    "[dry-run] advance=%d acct=%s outstanding=%.2f → +%.2f (%.4f) = %.2f %s",
                    advance_id, account, outstanding, fee, pct,
                    new_outstanding, currency,
                )
                summary["accruals_applied"] += 1
                summary["total_fee_amount"] += fee
                continue

            cur.execute("SAVEPOINT advance_accrual")
            try:
                cur.execute(
                    "UPDATE account_advances SET outstanding = %s, last_accrual_at = NOW() WHERE id = %s",
                    (new_outstanding, advance_id),
                )
                cur.execute(
                    """
                    INSERT INTO account_advance_ledger
                        (advance_id, entry_type, amount, balance_after,
                         accrual_period, created_by, note)
                    VALUES (%s, 'monthly_fee', %s, %s, %s, 'system:accrual',
                            %s)
                    """,
                    (
                        advance_id, fee, new_outstanding, period,
                        f"Monthly fee {pct * 100:.4f}% on {outstanding:.2f} {currency} ({period})",
                    ),
                )
            except psycopg2.errors.UniqueViolation:
                # Race: another worker inserted the same (advance, period)
                # ledger row. Roll back just this advance and skip.
                cur.execute("ROLLBACK TO SAVEPOINT advance_accrual")
                summary["accruals_skipped_duplicate"] += 1
                continue
            cur.execute("RELEASE SAVEPOINT advance_accrual")
            summary["accruals_applied"] += 1
            summary["total_fee_amount"] += fee
            logger.info(
                "Accrued advance=%d acct=%s outstanding=%.2f → +%.2f = %.2f %s",
                advance_id, account, outstanding, fee, new_outstanding, currency,
            )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        logger.error("DB error during accrual: %s", exc)
        raise
    finally:
        conn.close()
    return summary


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

    try:
        summary = accrue(period, dry_run=args.dry_run, database_url=database_url)
    except psycopg2.Error:
        return 1

    logger.info(
        "Accrual %s done: considered=%d applied=%d skipped_duplicate=%d total_fees=%.2f%s",
        period, summary["advances_considered"], summary["accruals_applied"],
        summary["accruals_skipped_duplicate"], summary["total_fee_amount"],
        " (dry-run)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
