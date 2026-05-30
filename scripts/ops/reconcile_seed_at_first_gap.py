#!/usr/bin/env python3
"""
Seed reconciliation workflow:

QUARANTINED NON-POLICY TOOL:
- This script is intentionally outside the current CC<->SM event-parity baseline.
- It is for controlled recovery/forensics only, not recurring steady-state sync.
- Do not include in event-parity PRs unless explicitly approved.

1) Identify and (optionally) reverse previous balance_seed rows in a controlled batch.
2) For each drifted account, locate a "gap candidate" timestamp from inbound
   payment evidence (sms_inbound_log receipt present but not credited in transactions).
3) Insert a replacement balance_seed at that timestamp (optional apply).

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


@dataclass
class GuardDecision:
    safe: bool
    reason: str
    existing_seed_kwh: float
    net_change_kwh: float
    predicted_residual_kwh: float


def _connect(url: str):
    return psycopg2.connect(url)


def _accounts_in_master(conn) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT account_number FROM accounts")
    return {str(row[0]).upper() for row in cur.fetchall() if row and row[0]}


def _collect_drift(conn, country: str, threshold: float, strict_shared_cohort: bool) -> list[DriftRow]:
    if country == "LS":
        from audit_ls_balances import (  # noqa: WPS433
            fetch_sparkmeter_balances,
            is_bulk_excluded_account,
            run_audit as run_ls_audit,
        )

        rows = run_ls_audit(conn)
        if strict_shared_cohort:
            accounts_master = _accounts_in_master(conn)
            sm_accounts = {a.upper() for a in fetch_sparkmeter_balances().keys()}
            shared = accounts_master & sm_accounts
            out = []
            for account, _, _, delta, _ in rows:
                acct = str(account).upper()
                if abs(float(delta)) < threshold:
                    continue
                if not VALID_LS_RE.match(acct):
                    continue
                if acct not in shared:
                    continue
                if is_bulk_excluded_account(acct):
                    continue
                out.append(DriftRow(acct, float(delta)))
            return out

        out = [DriftRow(a.upper(), float(d)) for a, _, _, d, _ in rows if abs(float(d)) >= threshold]
        return [r for r in out if VALID_LS_RE.match(r.account)]

    # BN audit requires Koios web session
    try:
        from audit_bn_balances import (  # noqa: WPS433
            fetch_koios_balances,
            koios_login,
            run_audit as run_bn_audit,
        )
    except ModuleNotFoundError:
        bn_path = os.environ.get("BN_AUDIT_SCRIPT", "/opt/1pdb/services/audit_bn_balances.py")
        spec = importlib.util.spec_from_file_location("audit_bn_balances_ext", bn_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_bn_audit = mod.run_audit
        koios_login = mod.koios_login
        fetch_koios_balances = mod.fetch_koios_balances

    session = requests.Session()
    try:
        koios_login(session)
        rows = run_bn_audit(conn, session)
        if strict_shared_cohort:
            accounts_master = _accounts_in_master(conn)
            sm_accounts = {a.upper() for a in fetch_koios_balances(session).keys()}
            shared = accounts_master & sm_accounts
            out = []
            for account, _, _, delta in rows:
                acct = str(account).upper()
                if abs(float(delta)) < threshold:
                    continue
                if not VALID_BN_RE.match(acct):
                    continue
                if acct not in shared:
                    continue
                out.append(DriftRow(acct, float(delta)))
            return out
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


def _latest_uncredited_receipt_ts(conn, account: str):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(s.received_at)
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


def _latest_tx_ts(conn, account: str):
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(transaction_date) FROM transactions WHERE account_number = %s",
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


def _account_seed_rows_to_reverse(conn, account: str, exclude_ref: str) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number, transaction_date, transaction_amount, rate_used, kwh_value
        FROM transactions
        WHERE source = 'balance_seed'
          AND account_number = %s
          AND (payment_reference IS NULL OR payment_reference NOT LIKE 'seed_reversal:%%')
          AND COALESCE(payment_reference, '') <> %s
        ORDER BY transaction_date ASC, id ASC
        """,
        (account, exclude_ref),
    )
    return cur.fetchall()


def _sum_account_seed_kwh(conn, account: str, exclude_ref: str) -> float:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(COALESCE(kwh_value, 0)), 0)
        FROM transactions
        WHERE source = 'balance_seed'
          AND account_number = %s
          AND (payment_reference IS NULL OR payment_reference NOT LIKE 'seed_reversal:%%')
          AND COALESCE(payment_reference, '') <> %s
        """,
        (account, exclude_ref),
    )
    return float(cur.fetchone()[0] or 0.0)


def _simulate_guard(
    *,
    delta_kwh: float,
    singular_seed: bool,
    existing_seed_kwh: float,
    max_predicted_residual_kwh: float,
    max_net_change_kwh: float,
) -> GuardDecision:
    if not singular_seed:
        net_change = float(delta_kwh)
        predicted_residual = float(delta_kwh - net_change)
    else:
        net_change = float(delta_kwh - existing_seed_kwh)
        predicted_residual = float(delta_kwh - net_change)

    if abs(net_change) > max_net_change_kwh:
        return GuardDecision(
            safe=False,
            reason=f"net_change_exceeds_{max_net_change_kwh}",
            existing_seed_kwh=existing_seed_kwh,
            net_change_kwh=net_change,
            predicted_residual_kwh=predicted_residual,
        )

    if abs(predicted_residual) > max_predicted_residual_kwh:
        return GuardDecision(
            safe=False,
            reason=f"predicted_residual_exceeds_{max_predicted_residual_kwh}",
            existing_seed_kwh=existing_seed_kwh,
            net_change_kwh=net_change,
            predicted_residual_kwh=predicted_residual,
        )

    return GuardDecision(
        safe=True,
        reason="ok",
        existing_seed_kwh=existing_seed_kwh,
        net_change_kwh=net_change,
        predicted_residual_kwh=predicted_residual,
    )


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


def _resolve_anchor_ts(conn, account: str, anchor_mode: str) -> datetime:
    if anchor_mode == "last_gap":
        anchor_ts = _latest_uncredited_receipt_ts(conn, account)
        if anchor_ts is None:
            anchor_ts = _latest_tx_ts(conn, account)
    elif anchor_mode == "first_gap":
        anchor_ts = _first_uncredited_receipt_ts(conn, account)
        if anchor_ts is None:
            cur = conn.cursor()
            cur.execute(
                "SELECT MIN(transaction_date) FROM transactions WHERE account_number = %s",
                (account,),
            )
            anchor_ts = cur.fetchone()[0]
    else:  # last_tx
        anchor_ts = _latest_tx_ts(conn, account)
    return anchor_ts or datetime.now(timezone.utc)


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
    ap.add_argument(
        "--anchor-mode",
        choices=["last_gap", "first_gap", "last_tx"],
        default="last_gap",
        help="Anchor seed timestamp strategy. Default: last_gap (most recent uncredited receipt, fallback latest transaction).",
    )
    ap.add_argument(
        "--singular-seed",
        action="store_true",
        default=True,
        help="When applying anchors, reverse existing non-reversal balance_seed rows per account before inserting one replacement seed.",
    )
    ap.add_argument(
        "--no-singular-seed",
        dest="singular_seed",
        action="store_false",
        help="Disable per-account singular-seed cleanup before anchor insertion.",
    )
    ap.add_argument("--batch-tag", default=f"anchor_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    ap.add_argument(
        "--guarded-singular",
        action="store_true",
        default=True,
        help="Enable pre-apply simulation gate for singular seeding (default on).",
    )
    ap.add_argument(
        "--no-guarded-singular",
        dest="guarded_singular",
        action="store_false",
        help="Disable simulation guard (not recommended).",
    )
    ap.add_argument(
        "--max-predicted-residual-kwh",
        type=float,
        default=2.0,
        help="Max allowed predicted post-action residual drift in kWh.",
    )
    ap.add_argument(
        "--max-net-change-kwh",
        type=float,
        default=100.0,
        help="Max allowed net kWh mutation per account in one apply step.",
    )
    ap.add_argument(
        "--strict-shared-cohort",
        action="store_true",
        help="Limit reconciliation scope to strict shared accounts (valid ID, present in accounts table and SparkMeter snapshot).",
    )
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

        drift_rows = _collect_drift(conn, args.country, args.threshold, args.strict_shared_cohort)
        planned = []
        anchors_applied = 0
        guarded_skipped = 0
        account_seed_reversals_applied = 0
        for row in drift_rows:
            anchor_ts = _resolve_anchor_ts(conn, row.account, args.anchor_mode)
            ref = f"{args.batch_tag}:{row.account}"
            account_reversals_planned = 0
            existing_seed_kwh = 0.0
            guard = GuardDecision(True, "ok", 0.0, float(row.delta_kwh), 0.0)
            if args.singular_seed:
                account_reversal_rows = _account_seed_rows_to_reverse(conn, row.account, ref)
                account_reversals_planned = len(account_reversal_rows)
                existing_seed_kwh = _sum_account_seed_kwh(conn, row.account, ref)

            if args.guarded_singular:
                guard = _simulate_guard(
                    delta_kwh=row.delta_kwh,
                    singular_seed=args.singular_seed,
                    existing_seed_kwh=existing_seed_kwh,
                    max_predicted_residual_kwh=args.max_predicted_residual_kwh,
                    max_net_change_kwh=args.max_net_change_kwh,
                )
            if args.guarded_singular and not guard.safe:
                guarded_skipped += 1

            planned.append(
                {
                    "account": row.account,
                    "delta_kwh": round(row.delta_kwh, 4),
                    "anchor_ts": anchor_ts.isoformat(),
                    "anchor_mode": args.anchor_mode,
                    "singular_seed": int(args.singular_seed),
                    "account_seed_reversals_planned": account_reversals_planned,
                    "existing_seed_kwh": round(existing_seed_kwh, 4),
                    "guarded_singular": int(args.guarded_singular),
                    "guard_pass": int(guard.safe),
                    "guard_reason": guard.reason,
                    "net_change_kwh": round(guard.net_change_kwh, 4),
                    "predicted_residual_kwh": round(guard.predicted_residual_kwh, 4),
                    "batch_tag": args.batch_tag,
                }
            )
            if args.apply_anchor_seeds:
                if args.guarded_singular and not guard.safe:
                    continue
                if args.singular_seed and account_reversals_planned:
                    account_reversal_rows = _account_seed_rows_to_reverse(conn, row.account, ref)
                    account_seed_reversals_applied += _apply_reversals(conn, account_reversal_rows, args.batch_tag)
                if _apply_anchor_seed(conn, row.account, row.delta_kwh, anchor_ts, args.batch_tag):
                    anchors_applied += 1

        if args.apply_anchor_seeds:
            conn.commit()

        args.plan_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.plan_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "account",
                    "delta_kwh",
                    "anchor_ts",
                    "anchor_mode",
                    "singular_seed",
                    "account_seed_reversals_planned",
                    "existing_seed_kwh",
                    "guarded_singular",
                    "guard_pass",
                    "guard_reason",
                    "net_change_kwh",
                    "predicted_residual_kwh",
                    "batch_tag",
                ],
            )
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
                "anchor_mode": args.anchor_mode,
                "singular_seed": args.singular_seed,
                "guarded_singular": args.guarded_singular,
                "guarded_skipped": guarded_skipped,
                "max_predicted_residual_kwh": args.max_predicted_residual_kwh,
                "max_net_change_kwh": args.max_net_change_kwh,
                "strict_shared_cohort": args.strict_shared_cohort,
                "account_seed_reversals_applied": account_seed_reversals_applied,
                "batch_tag": args.batch_tag,
            }
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

