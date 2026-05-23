#!/usr/bin/env python3
"""
Seed reconciliation workflow:

1) Identify and (optionally) reverse previous balance_seed rows in a controlled batch.
2) For each drifted account, locate the earliest "gap candidate" timestamp from inbound
   payment evidence (sms_inbound_log receipt present but not credited in transactions).
3) Insert a replacement balance_seed at that first gap timestamp (optional apply).

Default mode is dry-run and writes a planning CSV.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import psycopg2

import sys

# Prefer caller-provided PYTHONPATH (used on the production host). Fall back to
# repo-relative discovery when executed from this repository tree.
try:
    ROOT = Path(__file__).resolve().parents[2]
except IndexError:
    ROOT = None

if ROOT is not None:
    for p in (ROOT / "acdb-api", ROOT / "scripts" / "ops"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

import requests  # noqa: E402

VALID_LS_RE = re.compile(r"^\d{4}[A-Z]{2,4}$")
VALID_BN_RE = re.compile(r"^\d{4}(GBO|SAM)$")


@dataclass
class DriftRow:
    account: str
    delta_kwh: float


def _connect(url: str):
    return psycopg2.connect(url)


def _collect_drift(conn, country: str, threshold: float) -> list[DriftRow]:
    if country == "LS":
        from audit_ls_balances import run_audit as run_ls_audit  # noqa: WPS433

        rows = run_ls_audit(conn)
        out = [DriftRow(a, float(d)) for a, _, _, d, _ in rows if abs(float(d)) >= threshold]
        return [r for r in out if VALID_LS_RE.match(r.account)]

    # BN audit requires Koios web session
    try:
        from audit_bn_balances import run_audit as run_bn_audit, koios_login  # noqa: WPS433
    except ModuleNotFoundError:
        bn_path = os.environ.get("BN_AUDIT_SCRIPT", "/opt/1pdb/services/audit_bn_balances.py")
        spec = importlib.util.spec_from_file_location("audit_bn_balances_ext", bn_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_bn_audit = mod.run_audit
        koios_login = mod.koios_login

    session = requests.Session()
    try:
        koios_login(session)
        rows = run_bn_audit(conn, session)
    finally:
        session.close()
    out = [DriftRow(a.upper(), float(d)) for a, _, _, d in rows if abs(float(d)) >= threshold]
    return [r for r in out if VALID_BN_RE.match(r.account)]


def _first_uncredited_receipt_ts(conn, account: str):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MIN(s.received_at)
        FROM sms_inbound_log s
        WHERE s.account_number = %s
          AND s.receipt_key IS NOT NULL
          AND btrim(s.receipt_key) <> ''
          AND NOT EXISTS (
              SELECT 1 FROM transactions t
              WHERE lower(trim(t.payment_reference)) = lower(trim(s.receipt_key))
          )
        """,
        (account,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _seed_rows_to_reverse(conn, since_ts: datetime) -> Iterable[tuple]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number, transaction_date, transaction_amount, rate_used, kwh_value
        FROM transactions
        WHERE source = 'balance_seed'
          AND transaction_date >= %s
          AND (payment_reference IS NULL OR payment_reference NOT LIKE 'seed_reversal:%%')
        ORDER BY transaction_date ASC, id ASC
        """,
        (since_ts,),
    )
    return cur.fetchall()


def _apply_reversals(conn, rows: list[tuple], batch_tag: str) -> int:
    cur = conn.cursor()
    inserted = 0
    for txn_id, account, ts, amount, rate, kwh in rows:
        ref = f"seed_reversal:{txn_id}"
        cur.execute("SELECT 1 FROM transactions WHERE payment_reference = %s LIMIT 1", (ref,))
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
                datetime.now(timezone.utc),
                -float(amount or 0),
                float(rate or 0),
                -float(kwh or 0),
                ref,
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def _apply_anchor_seed(conn, account: str, delta_kwh: float, anchor_ts: datetime, batch_tag: str) -> bool:
    cur = conn.cursor()
    ref = f"{batch_tag}:{account}"
    cur.execute("SELECT 1 FROM transactions WHERE payment_reference = %s LIMIT 1", (ref,))
    if cur.fetchone():
        return False
    # Derive amount at account's historical effective rate fallback.
    cur.execute(
        """
        SELECT COALESCE(MAX(rate_used), 0)
        FROM transactions
        WHERE account_number = %s AND rate_used IS NOT NULL
        """,
        (account,),
    )
    rate = float(cur.fetchone()[0] or 0)
    amount = round(delta_kwh * rate, 4) if rate > 0 else 0.0
    cur.execute(
        """
        INSERT INTO transactions
            (account_number, meter_id, transaction_date, transaction_amount,
             rate_used, kwh_value, is_payment, current_balance, source, payment_reference)
        VALUES (%s, '', %s, %s, %s, %s, true, 0, 'balance_seed', %s)
        """,
        (account, anchor_ts, amount, rate, delta_kwh, ref),
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--country", choices=["LS", "BN"], required=True)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--threshold", type=float, default=1.0)
    ap.add_argument("--reverse-since", default=None, help="ISO timestamp for legacy seed reversals")
    ap.add_argument("--apply-reversals", action="store_true")
    ap.add_argument("--apply-anchor-seeds", action="store_true")
    ap.add_argument("--batch-tag", default=f"anchor_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    ap.add_argument("--plan-csv", type=Path, default=Path("/tmp/seed_anchor_plan.csv"))
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")

    conn = _connect(args.database_url)
    try:
        reversals_planned = 0
        reversals_applied = 0
        if args.reverse_since:
            since_ts = datetime.fromisoformat(args.reverse_since.replace("Z", "+00:00"))
            rows = list(_seed_rows_to_reverse(conn, since_ts))
            reversals_planned = len(rows)
            if args.apply_reversals and rows:
                reversals_applied = _apply_reversals(conn, rows, args.batch_tag)

        drift_rows = _collect_drift(conn, args.country, args.threshold)
        planned = []
        anchors_applied = 0
        for row in drift_rows:
            anchor_ts = _first_uncredited_receipt_ts(conn, row.account)
            if anchor_ts is None:
                # Fall back to earliest tx timestamp for account.
                cur = conn.cursor()
                cur.execute(
                    "SELECT MIN(transaction_date) FROM transactions WHERE account_number = %s",
                    (row.account,),
                )
                anchor_ts = cur.fetchone()[0] or datetime.now(timezone.utc)
            planned.append(
                {
                    "account": row.account,
                    "delta_kwh": round(row.delta_kwh, 4),
                    "anchor_ts": anchor_ts.isoformat(),
                    "batch_tag": args.batch_tag,
                }
            )
            if args.apply_anchor_seeds:
                if _apply_anchor_seed(conn, row.account, row.delta_kwh, anchor_ts, args.batch_tag):
                    anchors_applied += 1

        if args.apply_anchor_seeds:
            conn.commit()

        args.plan_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.plan_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["account", "delta_kwh", "anchor_ts", "batch_tag"])
            w.writeheader()
            w.writerows(planned)

        print(
            {
                "country": args.country,
                "threshold": args.threshold,
                "drift_accounts_planned": len(planned),
                "plan_csv": str(args.plan_csv),
                "reversals_planned": reversals_planned,
                "reversals_applied": reversals_applied,
                "anchors_applied": anchors_applied,
                "batch_tag": args.batch_tag,
            }
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

