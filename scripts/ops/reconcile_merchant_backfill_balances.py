#!/usr/bin/env python3
"""Remove kWh balance credit from merchant-export backfill rows.

The merchant export backfill books payment history in ``transactions`` with
``source_table`` tags like ``mm:{receipt}:r{row}``. Those rows must not
change the canonical 1PDB balance when Koios / ThunderCloud already held the
correct customer credit.

This script sets ``kwh_value`` to NULL on matching payment rows so
``get_balance_kwh()`` ignores them while preserving amount, date, and metadata.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

from balance_engine import get_balance_kwh  # noqa: E402

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(format=LOG_FMT, level=logging.INFO)
logger = logging.getLogger("cc-ops.mm-backfill-reconcile")

TAG_PREFIX = "mm:%"


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def _summary(conn) -> dict[str, float | int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*),
               COUNT(DISTINCT account_number),
               COALESCE(SUM(kwh_value), 0),
               COALESCE(SUM(transaction_amount), 0)
        FROM transactions
        WHERE source_table LIKE %s
          AND is_payment = TRUE
        """,
        (TAG_PREFIX,),
    )
    rows, accounts, kwh, lsl = cur.fetchone()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM transactions
        WHERE source_table LIKE %s
          AND is_payment = TRUE
          AND kwh_value IS NOT NULL
        """,
        (TAG_PREFIX,),
    )
    credited = int(cur.fetchone()[0])
    return {
        "rows": int(rows),
        "accounts": int(accounts),
        "kwh_credited": float(kwh),
        "lsl": float(lsl),
        "rows_with_kwh": credited,
    }


def _sample_accounts(conn, limit: int = 5) -> list[tuple[str, float, float]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT account_number, COALESCE(SUM(kwh_value), 0) AS mm_kwh
        FROM transactions
        WHERE source_table LIKE %s
          AND is_payment = TRUE
          AND kwh_value IS NOT NULL
        GROUP BY account_number
        ORDER BY mm_kwh DESC
        LIMIT %s
        """,
        (TAG_PREFIX, limit),
    )
    samples: list[tuple[str, float, float]] = []
    for account_number, mm_kwh in cur.fetchall():
        balance, _ = get_balance_kwh(conn, account_number)
        samples.append((account_number, float(mm_kwh), balance))
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write NULL kwh_value")
    parser.add_argument("--dry-run", action="store_true", help="Report only")
    args = parser.parse_args()
    if not args.apply:
        args.dry_run = True

    conn = _connect()
    try:
        before = _summary(conn)
        logger.info("Before: %s", before)
        if before["rows_with_kwh"]:
            for account, mm_kwh, balance in _sample_accounts(conn):
                logger.info(
                    "Sample %s: mm_kwh=%.4f current_balance=%.4f kWh",
                    account,
                    mm_kwh,
                    balance,
                )

        if args.dry_run:
            logger.info(
                "Would clear kwh_value on %d merchant-export payment rows (%.4f kWh)",
                before["rows_with_kwh"],
                before["kwh_credited"],
            )
            return 0

        cur = conn.cursor()
        cur.execute(
            """
            UPDATE transactions
            SET kwh_value = NULL
            WHERE source_table LIKE %s
              AND is_payment = TRUE
              AND kwh_value IS NOT NULL
            """,
            (TAG_PREFIX,),
        )
        updated = cur.rowcount
        conn.commit()
        after = _summary(conn)
        logger.info("Updated %d rows", updated)
        logger.info("After: %s", after)
        if after["rows_with_kwh"]:
            for account, mm_kwh, balance in _sample_accounts(conn):
                logger.info(
                    "Sample %s: mm_kwh=%.4f current_balance=%.4f kWh",
                    account,
                    mm_kwh,
                    balance,
                )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
