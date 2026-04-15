"""
Find potential duplicate *payment* rows in Benin (`onepower_bj.transactions`).

Does not connect to Koios. Use `audit_bn_balances.py` for Koios vs 1PDB drift.

Run on a host that can reach PostgreSQL (e.g. CC EC2):

  export DATABASE_URL='postgresql://cc_api:...@localhost:5432/onepower_bj'
  python3 scripts/ops/find_bn_duplicate_transactions.py

Exit code 1 if any duplicate class has rows (for CI/monitoring).
"""

from __future__ import annotations

import os
import sys

import psycopg2


def main() -> None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print(
            "Set DATABASE_URL to the Benin database, e.g.\n"
            "  export DATABASE_URL='postgresql://USER:PASS@localhost:5432/onepower_bj'",
            file=sys.stderr,
        )
        sys.exit(2)

    conn = psycopg2.connect(url)
    cur = conn.cursor()

    print("=== BN duplicate / near-duplicate payment scan (onepower_bj) ===\n")

    # 1) Same external ref (should be impossible if unique index is present and populated)
    cur.execute(
        """
        SELECT lower(trim(payment_reference)) AS ref,
               COUNT(*) AS n,
               array_agg(id ORDER BY id) AS ids,
               array_agg(source ORDER BY id) AS sources
        FROM transactions
        WHERE payment_reference IS NOT NULL AND trim(payment_reference) <> ''
        GROUP BY lower(trim(payment_reference))
        HAVING COUNT(*) > 1
        ORDER BY n DESC, ref
        LIMIT 200;
        """
    )
    rows = cur.fetchall()
    print(f"1) Duplicate payment_reference (same ref, multiple rows): {len(rows)} groups")
    for ref, n, ids, sources in rows:
        print(f"   ref={ref!r} count={n} ids={ids} sources={sources}")
    if rows:
        print("   ACTION: Investigate each id pair; delete or merge with DBA review.\n")
    else:
        print("   (none)\n")

    # 2) Exact twin rows (same account, time, amount) — strong duplicate signal
    cur.execute(
        """
        SELECT account_number, transaction_date, transaction_amount,
               COUNT(*) AS n, array_agg(id ORDER BY id) AS ids,
               array_agg(COALESCE(source, '') ORDER BY id) AS sources
        FROM transactions
        WHERE is_payment IS TRUE
        GROUP BY account_number, transaction_date, transaction_amount
        HAVING COUNT(*) > 1
        ORDER BY transaction_date DESC
        LIMIT 200;
        """
    )
    rows2 = cur.fetchall()
    print(f"2) Same account + exact transaction_date + same amount (payments): {len(rows2)} groups")
    for acct, ts, amt, n, ids, sources in rows2:
        print(f"   {acct} @ {ts} amt={amt} count={n} ids={ids} sources={sources}")
    if rows2:
        print("   ACTION: Likely double insert; keep one row, reverse SparkMeter if credited twice.\n")
    else:
        print("   (none)\n")

    # 3) NULL/empty ref rows — same minute + account + amount (review; may be legit back-to-back)
    cur.execute(
        """
        SELECT account_number,
               date_trunc('minute', transaction_date) AS minute,
               transaction_amount,
               COUNT(*) AS n,
               array_agg(id ORDER BY id) AS ids
        FROM transactions
        WHERE is_payment IS TRUE
          AND (payment_reference IS NULL OR trim(payment_reference) = '')
        GROUP BY account_number, date_trunc('minute', transaction_date), transaction_amount
        HAVING COUNT(*) > 1
        ORDER BY minute DESC
        LIMIT 200;
        """
    )
    rows3 = cur.fetchall()
    print(
        f"3) No payment_reference — same account + minute + amount: {len(rows3)} groups "
        "(manual review; not always duplicates)\n"
    )
    for acct, minute, amt, n, ids in rows3:
        print(f"   {acct} @ {minute} amt={amt} count={n} ids={ids}")

    cur.close()
    conn.close()

    bad = bool(rows or rows2)
    if bad:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
