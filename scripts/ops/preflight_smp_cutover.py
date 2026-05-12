#!/usr/bin/env python3
"""Preflight checks and finance sign-off CSV for SMP 1PDB–SparkMeter cutover."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
OPS = Path(__file__).resolve().parent
for path in (ACDB_API, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from country_config import get_tariff_rate_for_site  # noqa: E402
from cutover_ls_common import (  # noqa: E402
    DEFAULT_DRIFT_THRESHOLD_KWH,
    is_bulk_excluded_account,
    site_code,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("preflight_smp")


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def _merchant_mm_summary(conn) -> dict[str, int | float]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE kwh_value IS NOT NULL),
               COALESCE(SUM(transaction_amount), 0)
        FROM transactions
        WHERE source_table LIKE 'mm:%%'
          AND is_payment = TRUE
        """
    )
    rows, credited, lsl = cur.fetchone()
    cur.close()
    return {"rows": int(rows), "rows_with_kwh": int(credited), "lsl": float(lsl)}


def _duplicate_credit_summary(conn) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT account_number, transaction_amount, date_trunc('minute', transaction_date)
            FROM transactions
            WHERE is_payment = TRUE
              AND COALESCE(kwh_value, 0) > 0
            GROUP BY 1, 2, 3
            HAVING COUNT(*) > 1
        ) d
        """
    )
    dup_minute = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE is_payment = TRUE
          AND source = 'thundercloud'
          AND COALESCE(kwh_value, 0) > 0
          AND transaction_date >= NOW() - INTERVAL '30 days'
        """
    )
    tc_recent = int(cur.fetchone()[0])
    cur.close()
    return {"duplicate_minute_buckets": dup_minute, "tc_credited_30d": tc_recent}


def _load_rca(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {row["account"]: row for row in csv.DictReader(handle)}


def _load_audit_rows(path: Path, threshold: float) -> list[dict[str, object]]:
    import re

    line_re = re.compile(
        r"^(?P<acct>\d{4}[A-Z]{2,4})\s+(?P<platform>\S+)\s+"
        r"(?P<sm>[-\d.]+)\s+(?P<pdb>[-\d.]+)\s+(?P<delta>[-\d.]+)"
    )
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = line_re.match(line)
        if not match:
            continue
        account = match.group("acct")
        delta = float(match.group("delta"))
        if abs(delta) < threshold:
            continue
        rows.append(
            {
                "account": account,
                "platform": match.group("platform"),
                "sm_kwh": float(match.group("sm")),
                "pdb_kwh": float(match.group("pdb")),
                "delta_kwh": delta,
            }
        )
    return rows


def build_signoff_csv(
    audit_rows: list[dict[str, object]],
    rca_by_account: dict[str, dict[str, str]],
    *,
    threshold: float,
    out_path: Path,
) -> tuple[int, int]:
    fieldnames = [
        "account",
        "site",
        "platform",
        "sm_kwh",
        "pdb_kwh",
        "delta_kwh",
        "delta_lsl",
        "proposed_seed_kwh",
        "bulk_excluded",
        "skip_negative_seed",
        "mm_null_kwh_rows",
        "mm_lsl",
        "accdb_micro_rows",
        "balance_seed_pay_kwh",
        "ledger_gap_kwh",
    ]
    included = 0
    skipped = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(audit_rows, key=lambda item: -abs(float(item["delta_kwh"]))):
            account = str(row["account"])
            site = site_code(account)
            rate = float(get_tariff_rate_for_site(site) or 0)
            delta = float(row["delta_kwh"])
            excluded = is_bulk_excluded_account(account)
            skip_negative = delta < 0
            rca = rca_by_account.get(account, {})
            proposed = 0.0 if excluded or skip_negative or abs(delta) < threshold else delta
            if excluded or skip_negative:
                skipped += 1
            else:
                included += 1
            writer.writerow(
                {
                    "account": account,
                    "site": site,
                    "platform": row["platform"],
                    "sm_kwh": f"{float(row['sm_kwh']):.4f}",
                    "pdb_kwh": f"{float(row['pdb_kwh']):.4f}",
                    "delta_kwh": f"{delta:.4f}",
                    "delta_lsl": f"{delta * rate:.2f}" if rate > 0 else "0.00",
                    "proposed_seed_kwh": f"{proposed:.4f}",
                    "bulk_excluded": "yes" if excluded else "no",
                    "skip_negative_seed": "yes" if skip_negative else "no",
                    "mm_null_kwh_rows": rca.get("mm_null_kwh_rows", ""),
                    "mm_lsl": rca.get("mm_lsl", ""),
                    "accdb_micro_rows": rca.get("accdb_micro_rows", ""),
                    "balance_seed_pay_kwh": rca.get("balance_seed_pay_kwh", ""),
                    "ledger_gap_kwh": rca.get("ledger_gap_kwh", ""),
                }
            )
    return included, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-file",
        type=Path,
        default=Path("/home/ubuntu/audit_ls_balances.txt"),
    )
    parser.add_argument(
        "--rca-csv",
        type=Path,
        default=Path("/home/ubuntu/rca_ls_smp_all.csv"),
    )
    parser.add_argument(
        "--signoff-csv",
        type=Path,
        default=Path("/home/ubuntu/smp_cutover_signoff.csv"),
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_DRIFT_THRESHOLD_KWH)
    args = parser.parse_args()

    conn = _connect()
    try:
        mm = _merchant_mm_summary(conn)
        dupes = _duplicate_credit_summary(conn)
    finally:
        conn.close()

    log.info("Merchant mm rows: %d (%d with kwh_value)", mm["rows"], mm["rows_with_kwh"])
    log.info("Duplicate credited minute buckets: %d", dupes["duplicate_minute_buckets"])
    log.info("ThunderCloud credited rows (30d): %d", dupes["tc_credited_30d"])

    if mm["rows_with_kwh"] > 0:
        log.error("BLOCKER: merchant mm rows still credit kWh — run reconcile_merchant_backfill_balances.py --apply")
        return 2

    if not args.audit_file.exists():
        log.error("Audit file missing: %s", args.audit_file)
        return 2

    audit_rows = _load_audit_rows(args.audit_file, args.threshold)
    rca_by_account = _load_rca(args.rca_csv)
    included, skipped = build_signoff_csv(
        audit_rows,
        rca_by_account,
        threshold=args.threshold,
        out_path=args.signoff_csv,
    )
    log.info(
        "Wrote sign-off CSV %s (%d seed candidates, %d excluded/skipped-negative)",
        args.signoff_csv,
        included,
        skipped,
    )
    log.info("Generated at %s", datetime.now(timezone.utc).isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
