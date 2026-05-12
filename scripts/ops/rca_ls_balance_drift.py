#!/usr/bin/env python3
"""Payment-history RCA for Lesotho accounts with SparkMeter vs 1PDB balance drift."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2

def _acdb_api_roots() -> list[Path]:
    roots: list[Path] = []
    script = Path(__file__).resolve()
    if len(script.parents) > 2:
        roots.append(script.parents[2] / "acdb-api")
    roots.append(Path("/opt/cc-portal/backend"))
    return roots


for candidate in _acdb_api_roots():
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break

from balance_engine import get_balance_kwh  # noqa: E402

AUDIT_LINE_RE = re.compile(
    r"^(?P<acct>\d{4}[A-Z]{2,4})\s+(?P<platform>\S+)\s+"
    r"(?P<sm>[-\d.]+)\s+(?P<pdb>[-\d.]+)\s+(?P<delta>[-\d.]+)"
)
SITE_CODE_RE = re.compile(r"([A-Z]{2,4})$")


def _site_code(account_number: str) -> str:
    match = SITE_CODE_RE.search((account_number or "").upper())
    return match.group(1) if match else ""


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def load_accounts_from_audit(
    path: Path,
    *,
    site: str | None,
    limit: int | None,
) -> list[str]:
    rows: list[tuple[float, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = AUDIT_LINE_RE.match(line)
        if not match:
            continue
        acct = match.group("acct")
        if acct == "0500MAK" or acct.endswith("BVW"):
            continue
        if site and not acct.endswith(site):
            continue
        delta = float(match.group("delta"))
        rows.append((delta, acct))
    rows.sort(key=lambda item: -abs(item[0]))
    if limit is None:
        return [acct for _, acct in rows]
    return [acct for _, acct in rows[:limit]]


def analyze_account(conn, account: str) -> dict[str, object]:
    cur = conn.cursor()
    balance, _ = get_balance_kwh(conn, account)

    cur.execute(
        """
        SELECT source,
               COUNT(*) FILTER (WHERE is_payment) AS pay_rows,
               ROUND(COALESCE(SUM(CASE WHEN is_payment THEN kwh_value END), 0)::numeric, 3) AS pay_kwh,
               ROUND(COALESCE(SUM(CASE WHEN is_payment THEN transaction_amount END), 0)::numeric, 2) AS pay_lsl,
               COUNT(*) FILTER (WHERE source_table LIKE 'mm:%%') AS mm_rows,
               ROUND(COALESCE(SUM(CASE WHEN source_table LIKE 'mm:%%'
                   THEN transaction_amount END), 0)::numeric, 2) AS mm_lsl
        FROM transactions
        WHERE account_number = %s
        GROUP BY source
        ORDER BY pay_kwh DESC NULLS LAST, pay_rows DESC
        """,
        (account,),
    )
    by_source = [
        {
            "source": source,
            "pay_rows": int(pay_rows or 0),
            "pay_kwh": float(pay_kwh or 0),
            "pay_lsl": float(pay_lsl or 0),
            "mm_rows": int(mm_rows or 0),
            "mm_lsl": float(mm_lsl or 0),
        }
        for source, pay_rows, pay_kwh, pay_lsl, mm_rows, mm_lsl in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT ROUND(COALESCE(SUM(kwh), 0)::numeric, 3)
        FROM hourly_consumption
        WHERE account_number = %s
        """,
        (account,),
    )
    consumption_kwh = float(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*),
               ROUND(COALESCE(SUM(transaction_amount), 0)::numeric, 2)
        FROM transactions
        WHERE account_number = %s
          AND is_payment
          AND source = 'accdb'
          AND COALESCE(kwh_value, 0) < 1
          AND transaction_amount <= 5
        """,
        (account,),
    )
    accdb_micro_rows, accdb_micro_lsl = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*),
               ROUND(COALESCE(SUM(transaction_amount), 0)::numeric, 2)
        FROM transactions
        WHERE account_number = %s
          AND source_table LIKE 'mm:%%'
          AND COALESCE(kwh_value, 0) = 0
        """,
        (account,),
    )
    mm_null_kwh_rows, mm_lsl = cur.fetchone()

    cur.close()
    credited_kwh = sum(row["pay_kwh"] for row in by_source)
    return {
        "account": account,
        "balance_kwh": round(balance, 3),
        "credited_kwh": round(credited_kwh, 3),
        "consumption_kwh": consumption_kwh,
        "ledger_gap_kwh": round(credited_kwh - consumption_kwh, 3),
        "accdb_micro_rows": int(accdb_micro_rows or 0),
        "accdb_micro_lsl": float(accdb_micro_lsl or 0),
        "mm_null_kwh_rows": int(mm_null_kwh_rows or 0),
        "mm_lsl": float(mm_lsl or 0),
        "by_source": by_source,
    }


def flatten_result(item: dict[str, object]) -> dict[str, object]:
    by_source = {row["source"]: row for row in item["by_source"]}  # type: ignore[index]
    row = {
        "account": item["account"],
        "site": _site_code(str(item["account"])),
        "balance_kwh": item["balance_kwh"],
        "credited_kwh": item["credited_kwh"],
        "consumption_kwh": item["consumption_kwh"],
        "ledger_gap_kwh": item["ledger_gap_kwh"],
        "accdb_micro_rows": item["accdb_micro_rows"],
        "accdb_micro_lsl": item["accdb_micro_lsl"],
        "mm_null_kwh_rows": item["mm_null_kwh_rows"],
        "mm_lsl": item["mm_lsl"],
    }
    for source in (
        "accdb",
        "koios",
        "thundercloud",
        "sms_gateway",
        "portal",
        "balance_seed",
    ):
        payload = by_source.get(source, {})
        row[f"{source}_pay_rows"] = payload.get("pay_rows", 0)
        row[f"{source}_pay_kwh"] = payload.get("pay_kwh", 0.0)
        row[f"{source}_pay_lsl"] = payload.get("pay_lsl", 0.0)
    return row


def write_summary_csv(path: Path, results: list[dict[str, object]]) -> None:
    rows = [flatten_result(item) for item in results]
    fieldnames = list(rows[0].keys()) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_site_summary(results: list[dict[str, object]]) -> None:
    by_site: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in results:
        by_site[_site_code(str(item["account"]))].append(item)

    print("SITE SUMMARY")
    print(
        "SITE  ACCOUNTS  AVG_LEDGER_GAP  SUM_MM_LSL  SUM_MM_ROWS  "
        "SUM_ACCDB_MICRO_LSL  SUM_BALANCE_SEED_KWH"
    )
    for site in sorted(by_site):
        items = by_site[site]
        avg_gap = sum(float(item["ledger_gap_kwh"]) for item in items) / len(items)
        mm_lsl = sum(float(item["mm_lsl"]) for item in items)
        mm_rows = sum(int(item["mm_null_kwh_rows"]) for item in items)
        micro_lsl = sum(float(item["accdb_micro_lsl"]) for item in items)
        seed_kwh = sum(
            float(row.get("pay_kwh", 0.0))
            for item in items
            for row in item["by_source"]  # type: ignore[index]
            if row.get("source") == "balance_seed"
        )
        print(
            f"{site:4}  {len(items):8d}  {avg_gap:14.2f}  {mm_lsl:10.2f}  "
            f"{mm_rows:11d}  {micro_lsl:19.2f}  {seed_kwh:20.3f}"
        )


def print_report(results: list[dict[str, object]], *, detailed: bool) -> None:
    if not detailed:
        return
    for item in results:
        print("=" * 88)
        print(
            f"{item['account']}: balance={item['balance_kwh']} kWh  "
            f"credited={item['credited_kwh']}  consumption={item['consumption_kwh']}  "
            f"ledger_gap={item['ledger_gap_kwh']}"
        )
        print(
            f"  accdb micro payments: {item['accdb_micro_rows']} rows / "
            f"M{item['accdb_micro_lsl']:.2f}; "
            f"merchant history-only rows: {item['mm_null_kwh_rows']} / M{item['mm_lsl']:.2f}"
        )
        for row in item["by_source"]:
            print(
                f"  {row['source']:<14} pay_rows={row['pay_rows']:<4} "
                f"pay_kwh={row['pay_kwh']:<10.3f} pay_lsl={row['pay_lsl']:<10.2f} "
                f"mm_rows={row['mm_rows']}"
            )
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-file", type=Path, default=Path("/home/ubuntu/audit_ls_balances.txt"))
    parser.add_argument("--site", type=str, default=None, help="Only accounts ending with this site code")
    parser.add_argument("--account", action="append", default=[], help="Explicit account number(s)")
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Top absolute-delta accounts from audit file (0 = all drifted)",
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=None,
        help="Write one row per account to CSV",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Print per-account source breakdown (default off for large runs)",
    )
    args = parser.parse_args()

    accounts = [acct.upper() for acct in args.account]
    if not accounts:
        if not args.audit_file.exists():
            raise SystemExit(f"Audit file not found: {args.audit_file}")
        limit = None if args.limit == 0 else args.limit
        accounts = load_accounts_from_audit(args.audit_file, site=args.site, limit=limit)
    if not accounts:
        raise SystemExit("No accounts selected")

    conn = _connect()
    try:
        results = [analyze_account(conn, account) for account in accounts]
    finally:
        conn.close()
    if args.summary_csv:
        write_summary_csv(args.summary_csv, results)
        print(f"Wrote {len(results)} rows to {args.summary_csv}")
    print_site_summary(results)
    print_report(results, detailed=args.detailed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
