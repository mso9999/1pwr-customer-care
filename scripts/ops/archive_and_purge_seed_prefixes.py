#!/usr/bin/env python3
"""Archive and purge balance_seed rows by payment_reference prefix."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import psycopg2


def _ensure_archive_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions_seed_archive (
            original_txn_id BIGINT PRIMARY KEY,
            archived_at TIMESTAMPTZ NOT NULL,
            archive_reason TEXT NOT NULL,
            account_number TEXT,
            meter_id TEXT,
            transaction_date TIMESTAMPTZ,
            transaction_amount NUMERIC,
            rate_used NUMERIC,
            kwh_value NUMERIC,
            is_payment BOOLEAN,
            current_balance NUMERIC,
            source TEXT,
            payment_reference TEXT,
            source_table TEXT,
            payment_category TEXT,
            advance_portion NUMERIC,
            electricity_portion NUMERIC,
            financing_portion NUMERIC
        )
        """
    )


def _load_rows(cur, prefixes: list[str]) -> list[tuple]:
    cur.execute(
        """
        SELECT id, account_number, meter_id, transaction_date, transaction_amount,
               rate_used, kwh_value, is_payment, current_balance, source,
               payment_reference, source_table, payment_category,
               advance_portion, electricity_portion, financing_portion
        FROM transactions
        WHERE source = 'balance_seed'
          AND split_part(COALESCE(payment_reference, ''), ':', 1) = ANY(%s)
        ORDER BY id
        """,
        (prefixes,),
    )
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--reason", default="spurious_seed_attempt_cleanup")
    ap.add_argument("--apply", action="store_true", help="Perform archive+delete. Default dry-run.")
    ap.add_argument("--prefix", action="append", default=[], help="Prefix to purge (repeatable)")
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")
    if not args.prefix:
        raise SystemExit("At least one --prefix is required")

    conn = psycopg2.connect(args.database_url)
    cur = conn.cursor()
    _ensure_archive_table(cur)

    rows = _load_rows(cur, args.prefix)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "prefixes": args.prefix,
            "rows_matched": len(rows),
            "min_id": rows[0][0] if rows else None,
            "max_id": rows[-1][0] if rows else None,
        }
    )

    if not args.apply:
        conn.rollback()
        conn.close()
        return 0

    archived_at = datetime.now(timezone.utc)
    archived = 0
    for row in rows:
        original_id = int(row[0])
        cur.execute(
            """
            INSERT INTO transactions_seed_archive
            (
              original_txn_id, archived_at, archive_reason,
              account_number, meter_id, transaction_date, transaction_amount,
              rate_used, kwh_value, is_payment, current_balance, source,
              payment_reference, source_table, payment_category,
              advance_portion, electricity_portion, financing_portion
            )
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (original_txn_id) DO NOTHING
            """,
            (
                original_id,
                archived_at,
                args.reason,
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                row[15],
            ),
        )
        if cur.rowcount:
            archived += 1

    cur.execute(
        """
        DELETE FROM transactions
        WHERE source = 'balance_seed'
          AND split_part(COALESCE(payment_reference, ''), ':', 1) = ANY(%s)
        """,
        (args.prefix,),
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    print({"archived_rows": archived, "deleted_rows": deleted, "archived_at": archived_at.isoformat()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

