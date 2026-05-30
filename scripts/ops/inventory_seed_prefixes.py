#!/usr/bin/env python3
"""Inventory balance_seed rows grouped by payment_reference prefix."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2


def _parse_env_file(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "DATABASE_URL":
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        return value
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--env-file", default="", help="Path to env file that contains DATABASE_URL")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    if not args.database_url and args.env_file:
        args.database_url = _parse_env_file(args.env_file)

    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")

    conn = psycopg2.connect(args.database_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT split_part(COALESCE(payment_reference, ''), ':', 1) AS prefix,
               COUNT(1) AS rows,
               MIN(transaction_date) AS min_ts,
               MAX(transaction_date) AS max_ts
        FROM transactions
        WHERE source = 'balance_seed'
        GROUP BY 1
        ORDER BY rows DESC
        LIMIT %s
        """,
        (args.limit,),
    )
    print("PREFIX,ROWS,MIN_TS,MAX_TS")
    for prefix, rows, min_ts, max_ts in cur.fetchall():
        print(f"{prefix},{rows},{min_ts},{max_ts}")

    cur.execute("SELECT COUNT(1) FROM transactions WHERE source='balance_seed'")
    print("TOTAL_BALANCE_SEED_ROWS", cur.fetchone()[0])
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

