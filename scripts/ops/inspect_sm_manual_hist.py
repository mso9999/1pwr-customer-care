#!/usr/bin/env python3
"""Inspect SM manual-history imported rows in transactions."""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg2


def _db_url_from_env(path: str) -> str:
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() != "DATABASE_URL":
            continue
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        return v
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default="")
    ap.add_argument("--env-file", default="")
    args = ap.parse_args()

    db_url = args.database_url or (_db_url_from_env(args.env_file) if args.env_file else "")
    if not db_url:
        raise SystemExit("DATABASE_URL or --env-file required")

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM transactions WHERE payment_reference LIKE 'sm_manual_hist:%'")
    total = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT COUNT(1)
        FROM (
          SELECT payment_reference
          FROM transactions
          WHERE payment_reference LIKE 'sm_manual_hist:%'
          GROUP BY payment_reference
          HAVING COUNT(1) > 1
        ) d
        """
    )
    dup_refs = int(cur.fetchone()[0])
    cur.execute("SELECT MIN(id), MAX(id) FROM transactions WHERE payment_reference LIKE 'sm_manual_hist:%'")
    id_range = cur.fetchone()
    print(
        {
            "total_sm_manual_hist_rows": total,
            "duplicate_payment_reference_keys": dup_refs,
            "id_min": id_range[0],
            "id_max": id_range[1],
        }
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

