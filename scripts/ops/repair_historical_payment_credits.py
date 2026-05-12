#!/usr/bin/env python3
"""Credit missing historical electricity payments with evidence-based dedupe.

Scans merchant-export rows tagged ``mm:%`` with NULL ``kwh_value``. When no
credited payment exists for the same account, amount, and ±24h window, proposes
(or applies) ``record_payment_kwh`` at the historical timestamp. Does not alter
the existing ``mm:%`` audit row.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
OPS = Path(__file__).resolve().parent
for path in (ACDB_API, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from balance_engine import record_payment_kwh  # noqa: E402
from country_config import get_tariff_rate_for_site  # noqa: E402
from cutover_ls_common import is_bulk_excluded_account, site_code  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("repair_hist_pay")


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def find_candidates(conn, *, account: str | None, limit: int | None) -> list[dict[str, object]]:
    cur = conn.cursor()
    params: list[object] = []
    account_filter = ""
    if account:
        account_filter = "AND mm.account_number = %s"
        params.append(account.upper())
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT %s"
        params.append(limit)
    cur.execute(
        f"""
        SELECT mm.id, mm.account_number, mm.meter_id, mm.transaction_date,
               mm.transaction_amount, mm.payment_reference, mm.source_table
        FROM transactions mm
        WHERE mm.source_table LIKE 'mm:%%'
          AND mm.is_payment = TRUE
          AND mm.kwh_value IS NULL
          {account_filter}
          AND NOT EXISTS (
            SELECT 1 FROM transactions cred
            WHERE cred.account_number = mm.account_number
              AND cred.is_payment = TRUE
              AND COALESCE(cred.kwh_value, 0) > 0
              AND cred.transaction_amount BETWEEN mm.transaction_amount - 0.01
                                              AND mm.transaction_amount + 0.01
              AND cred.transaction_date BETWEEN mm.transaction_date - INTERVAL '24 hours'
                                            AND mm.transaction_date + INTERVAL '24 hours'
          )
          AND NOT EXISTS (
            SELECT 1 FROM transactions cref
            WHERE cref.payment_reference = mm.payment_reference
              AND cref.is_payment = TRUE
              AND COALESCE(cref.kwh_value, 0) > 0
              AND mm.payment_reference IS NOT NULL
              AND mm.payment_reference <> ''
          )
        ORDER BY mm.transaction_date
        {limit_sql}
        """,
        params,
    )
    candidates: list[dict[str, object]] = []
    for txn_id, acct, meter_id, paid_at, amount, ref, source_table in cur.fetchall():
        if is_bulk_excluded_account(acct):
            continue
        site = site_code(acct)
        rate = float(get_tariff_rate_for_site(site) or 0)
        if rate <= 0:
            continue
        candidates.append(
            {
                "mm_txn_id": int(txn_id),
                "account": acct,
                "site": site,
                "paid_at": paid_at,
                "amount_lsl": float(amount),
                "rate": rate,
                "proposed_kwh": round(float(amount) / rate, 4),
                "payment_reference": ref or "",
                "source_table": source_table or "",
            }
        )
    cur.close()
    return candidates


def write_report(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "mm_txn_id",
        "account",
        "site",
        "paid_at",
        "amount_lsl",
        "rate",
        "proposed_kwh",
        "payment_reference",
        "source_table",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "mm_txn_id": row["mm_txn_id"],
                    "account": row["account"],
                    "site": row["site"],
                    "paid_at": row["paid_at"].isoformat() if row["paid_at"] else "",
                    "amount_lsl": row["amount_lsl"],
                    "rate": row["rate"],
                    "proposed_kwh": row["proposed_kwh"],
                    "payment_reference": row["payment_reference"],
                    "source_table": row["source_table"],
                }
            )


def apply_repairs(conn, rows: list[dict[str, object]]) -> int:
    applied = 0
    for row in rows:
        repair_ref = f"hist_repair:{row['mm_txn_id']}"
        txn_id, _, _ = record_payment_kwh(
            conn,
            str(row["account"]),
            "",
            float(row["amount_lsl"]),
            float(row["rate"]),
            source="portal",
            timestamp=row["paid_at"],
            payment_reference=repair_ref,
        )
        log.info("Applied repair txn=%s from mm_txn=%s", txn_id, row["mm_txn_id"])
        applied += 1
    conn.commit()
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="Limit to one account")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report-csv", type=Path, default=Path("/tmp/hist_payment_repair.csv"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = _connect()
    try:
        candidates = find_candidates(conn, account=args.account, limit=args.limit)
        write_report(args.report_csv, candidates)
        log.info("Found %d repair candidates; wrote %s", len(candidates), args.report_csv)
        if args.apply and candidates:
            applied = apply_repairs(conn, candidates)
            log.info("Applied %d historical payment credits", applied)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
