#!/usr/bin/env python3
"""Rollback historical repair payments by transaction id range."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import psycopg2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--min-id", type=int, required=True)
    ap.add_argument("--max-id", type=int, required=True)
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")

    conn = psycopg2.connect(args.database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number, transaction_amount, rate_used, kwh_value
        FROM transactions
        WHERE id BETWEEN %s AND %s
          AND split_part(COALESCE(payment_reference, ''), ':', 1) = 'hist_repair'
        ORDER BY id
        """,
        (args.min_id, args.max_id),
    )
    rows = cur.fetchall()
    now = datetime.now(timezone.utc)
    inserted = 0
    for txn_id, account, amount, rate, kwh in rows:
        rollback_ref = f"hist_repair_rollback:{txn_id}"
        cur.execute("SELECT 1 FROM transactions WHERE payment_reference = %s LIMIT 1", (rollback_ref,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO transactions
              (account_number, meter_id, transaction_date, transaction_amount,
               rate_used, kwh_value, is_payment, current_balance, source, payment_reference)
            VALUES (%s, '', %s, %s, %s, %s, true, 0, 'balance_seed', %s)
            """,
            (
                account,
                now,
                -float(amount or 0),
                float(rate or 0),
                -float(kwh or 0),
                rollback_ref,
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(
        {
            "range": [args.min_id, args.max_id],
            "rows_found": len(rows),
            "rollback_rows_inserted": inserted,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

