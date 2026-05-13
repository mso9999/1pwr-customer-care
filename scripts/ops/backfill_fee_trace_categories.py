#!/usr/bin/env python3
"""Set fee trace category to listed_paid_missing_record where paid flag has no verified fee."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/")
ACDB_API = Path(os.environ.get("ACDB_API", "/opt/cc-portal/backend"))
if not (ACDB_API / "customer_api.py").exists():
    ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_fee_trace")


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM customers c
            WHERE c.connection_fee_paid = true
              AND c.connection_fee_trace_category IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM payment_verifications pv
                JOIN accounts a ON a.account_number = pv.account_number AND a.customer_id = c.id
                WHERE pv.payment_type = 'connection_fee' AND pv.status = 'verified'
              )
            """
        )
        n_cf = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT COUNT(*) FROM customers c
            WHERE c.readyboard_fee_paid = true
              AND c.readyboard_fee_trace_category IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM payment_verifications pv
                JOIN accounts a ON a.account_number = pv.account_number AND a.customer_id = c.id
                WHERE pv.payment_type = 'readyboard_fee' AND pv.status = 'verified'
              )
            """
        )
        n_rb = int(cur.fetchone()[0] or 0)
        if args.apply:
            cur.execute(
                """
                UPDATE customers c
                SET connection_fee_trace_category = 'listed_paid_missing_record',
                    fee_trace_updated_at = NOW(),
                    fee_trace_updated_by = 'backfill_fee_trace_categories'
                WHERE c.connection_fee_paid = true
                  AND c.connection_fee_trace_category IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM payment_verifications pv
                    JOIN accounts a ON a.account_number = pv.account_number AND a.customer_id = c.id
                    WHERE pv.payment_type = 'connection_fee' AND pv.status = 'verified'
                  )
                """
            )
            u_cf = cur.rowcount
            cur.execute(
                """
                UPDATE customers c
                SET readyboard_fee_trace_category = 'listed_paid_missing_record',
                    fee_trace_updated_at = NOW(),
                    fee_trace_updated_by = 'backfill_fee_trace_categories'
                WHERE c.readyboard_fee_paid = true
                  AND c.readyboard_fee_trace_category IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM payment_verifications pv
                    JOIN accounts a ON a.account_number = pv.account_number AND a.customer_id = c.id
                    WHERE pv.payment_type = 'readyboard_fee' AND pv.status = 'verified'
                  )
                """
            )
            u_rb = cur.rowcount
            conn.commit()
            log.info("Updated connection_fee=%d readyboard_fee=%d", u_cf, u_rb)
        else:
            log.info("Would update connection_fee=%d readyboard_fee=%d", n_cf, n_rb)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
