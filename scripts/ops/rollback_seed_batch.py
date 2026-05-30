#!/usr/bin/env python3
"""Insert compensating balance_seed rows for one batch-tag prefix."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import psycopg2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--batch-prefix", required=True, help="split_part(payment_reference, ':', 1) value")
    ap.add_argument("--rollback-prefix", default="seed_rollback_batch")
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")

    conn = psycopg2.connect(args.database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number, transaction_amount, rate_used, kwh_value
        FROM transactions
        WHERE split_part(COALESCE(payment_reference, ''), ':', 1) = %s
        ORDER BY id
        """,
        (args.batch_prefix,),
    )
    rows = cur.fetchall()

    inserted = 0
    now = datetime.now(timezone.utc)
    for txn_id, account, amount, rate, kwh in rows:
        rollback_ref = f"{args.rollback_prefix}:{txn_id}"
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
    print({"batch_prefix": args.batch_prefix, "rows_found": len(rows), "rollback_rows_inserted": inserted})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

