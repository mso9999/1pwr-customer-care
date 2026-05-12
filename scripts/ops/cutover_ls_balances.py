#!/usr/bin/env python3
"""Portfolio cutover: align 1PDB balances to SparkMeter with tagged balance_seed rows."""

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

from audit_ls_balances import (  # noqa: E402
    DRIFT_THRESHOLD_KWH,
    run_audit,
)
from country_config import get_tariff_rate_for_site  # noqa: E402
from cutover_ls_common import (  # noqa: E402
    cutover_tag_for,
    is_bulk_excluded_account,
    site_code,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cutover_ls")


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def _eligible_row(
    account: str,
    delta: float,
    *,
    threshold: float,
    skip_negative: bool,
) -> bool:
    if is_bulk_excluded_account(account):
        return False
    if abs(delta) < threshold:
        return False
    if skip_negative and delta < 0:
        return False
    return True


def preview_csv(
    results: list[tuple[str, float, float, float, str]],
    *,
    threshold: float,
    skip_negative: bool,
    tag: str,
    out_path: Path,
) -> int:
    fieldnames = [
        "account",
        "site",
        "platform",
        "sm_kwh",
        "pdb_kwh",
        "delta_kwh",
        "delta_lsl",
        "seed_kwh",
        "cutover_tag",
        "eligible",
    ]
    count = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for account, sm_kwh, pdb_kwh, delta, platform in sorted(
            results, key=lambda row: -abs(row[3])
        ):
            site = site_code(account)
            rate = float(get_tariff_rate_for_site(site) or 0)
            eligible = _eligible_row(account, delta, threshold=threshold, skip_negative=skip_negative)
            if eligible:
                count += 1
            writer.writerow(
                {
                    "account": account,
                    "site": site,
                    "platform": platform,
                    "sm_kwh": f"{sm_kwh:.4f}",
                    "pdb_kwh": f"{pdb_kwh:.4f}",
                    "delta_kwh": f"{delta:.4f}",
                    "delta_lsl": f"{delta * rate:.2f}" if rate > 0 else "0.00",
                    "seed_kwh": f"{delta:.4f}" if eligible else "0.0000",
                    "cutover_tag": tag,
                    "eligible": "yes" if eligible else "no",
                }
            )
    return count


def apply_seeds(
    conn,
    results: list[tuple[str, float, float, float, str]],
    *,
    threshold: float,
    skip_negative: bool,
    tag: str,
    cutover_ts: datetime,
) -> int:
    cur = conn.cursor()
    inserted = 0
    skipped = 0
    for account, _, _, delta, _ in results:
        if not _eligible_row(account, delta, threshold=threshold, skip_negative=skip_negative):
            skipped += 1
            continue
        site = site_code(account)
        rate = float(get_tariff_rate_for_site(site) or 0)
        amount = round(delta * rate, 4) if rate > 0 else 0.0
        ref = f"{tag}:{account}"
        cur.execute(
            """
            SELECT 1 FROM transactions
            WHERE payment_reference = %s
            LIMIT 1
            """,
            (ref,),
        )
        if cur.fetchone():
            skipped += 1
            continue
        cur.execute(
            """
            INSERT INTO transactions
                (account_number, meter_id, transaction_date, transaction_amount,
                 rate_used, kwh_value, is_payment, current_balance, source,
                 payment_reference)
            VALUES (%s, '', %s, %s, %s, %s, true, 0, 'balance_seed', %s)
            """,
            (account, cutover_ts, amount, rate, delta, ref),
        )
        inserted += 1
    conn.commit()
    cur.close()
    log.info("Inserted %d balance_seed rows (skipped %d)", inserted, skipped)
    return inserted


def post_audit(conn, threshold: float) -> tuple[int, int]:
    results = run_audit(conn)
    drifted = [
        row
        for row in results
        if abs(row[3]) >= threshold and not is_bulk_excluded_account(row[0])
    ]
    return len(results), len(drifted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-csv", type=Path, default=Path("/tmp/smp_cutover_preview.csv"))
    parser.add_argument("--threshold", type=float, default=DRIFT_THRESHOLD_KWH)
    parser.add_argument("--skip-negative-delta", action="store_true", default=True)
    parser.add_argument("--allow-negative-delta", action="store_true", help="Seed 1PDB>SM deltas too")
    parser.add_argument("--cutover-tag", default=None)
    parser.add_argument("--cutover-ts", default=None, help="ISO timestamp (default: now UTC)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    skip_negative = not args.allow_negative_delta
    cutover_ts = (
        datetime.fromisoformat(args.cutover_ts.replace("Z", "+00:00"))
        if args.cutover_ts
        else datetime.now(timezone.utc)
    )
    tag = args.cutover_tag or cutover_tag_for(cutover_ts)

    conn = _connect()
    try:
        log.info("Running SparkMeter vs 1PDB audit snapshot...")
        results = run_audit(conn)
        eligible = preview_csv(
            results,
            threshold=args.threshold,
            skip_negative=skip_negative,
            tag=tag,
            out_path=args.preview_csv,
        )
        log.info("Preview CSV %s (%d eligible seeds)", args.preview_csv, eligible)
        if args.apply:
            log.info("Applying cutover seeds tag=%s ts=%s", tag, cutover_ts.isoformat())
            apply_seeds(
                conn,
                results,
                threshold=args.threshold,
                skip_negative=skip_negative,
                tag=tag,
                cutover_ts=cutover_ts,
            )
            total, drifted = post_audit(conn, args.threshold)
            log.info("Post-cutover audit: %d accounts, %d in-scope drifted", total, drifted)
            if drifted:
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
