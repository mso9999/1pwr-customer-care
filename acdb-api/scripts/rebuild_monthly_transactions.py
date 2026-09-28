#!/usr/bin/env python3
"""Rebuild monthly_transactions from the live transactions ledger.

Used by the periodic importer so Financial / Analytics stay current even when
hourly import runs with --no-aggregate.

  DATABASE_URL=... python3 scripts/rebuild_monthly_transactions.py
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rebuild-monthly-tx")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is required")
        return 2
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import psycopg2
    from monthly_aggregates import rebuild_monthly_transactions

    conn = psycopg2.connect(dsn)
    try:
        result = rebuild_monthly_transactions(conn)
        log.info(
            "Rebuilt monthly_transactions: %s rows, %s .. %s, total %s",
            result["after_count"],
            result["after_min"],
            result["after_max"],
            result["total_amount"],
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
