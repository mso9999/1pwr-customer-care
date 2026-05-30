#!/usr/bin/env python3
"""Verify live balance_seed rows and archive counts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2


def _database_url_from_env_file(path: str) -> str:
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
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


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--env-file", default="")
    ap.add_argument("--reason", action="append", default=[])
    args = ap.parse_args()

    if not args.database_url and args.env_file:
        args.database_url = _database_url_from_env_file(args.env_file)
    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url/--env-file) is required")

    conn = psycopg2.connect(args.database_url)
    cur = conn.cursor()
    print("REMAINING_BALANCE_SEED", _count(cur, "SELECT COUNT(1) FROM transactions WHERE source='balance_seed'"))
    print("ARCHIVE_TOTAL", _count(cur, "SELECT COUNT(1) FROM transactions_seed_archive"))
    for reason in args.reason:
        print(
            f"ARCHIVE_REASON[{reason}]",
            _count(cur, "SELECT COUNT(1) FROM transactions_seed_archive WHERE archive_reason=%s", (reason,)),
        )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

