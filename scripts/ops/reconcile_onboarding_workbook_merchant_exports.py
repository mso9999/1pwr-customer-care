#!/usr/bin/env python3
"""Reconcile onboarding workbook fee rows against Dropbox merchant export files."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/")
ACDB_API = Path(os.environ.get("ACDB_API", "/opt/cc-portal/backend"))
if not (ACDB_API / "customer_api.py").exists():
    ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

from merchant_export_parser import (  # noqa: E402
    DEFAULT_ROOT,
    NormalizedPayment,
    iter_payments_from_root,
    summarize_manifest,
)
from mpesa_sms import candidate_accounts_from_text  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "import_onboarding_workbook",
    Path(__file__).resolve().parent / "import_onboarding_workbook.py",
)
_IOB = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_IOB)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reconcile_onboarding_merchant_exports")

_AMOUNT_EPSILON = 0.005
_DATE_WINDOW_DAYS = 30


def _norm_ref(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).upper()


def _norm_phone(value: Any) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _amounts_match(paid: float, target: float) -> bool:
    if target is None or target <= 0:
        return False
    return abs(round(float(paid), 2) - round(float(target), 2)) < _AMOUNT_EPSILON


def _within_date_window(paid_at: datetime, workbook_date: date | None) -> bool:
    if workbook_date is None:
        return True
    if paid_at.tzinfo is None:
        paid_at = paid_at.replace(tzinfo=timezone.utc)
    return abs((paid_at.date() - workbook_date).days) <= _DATE_WINDOW_DAYS


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg2.connect(url)


def _load_default_fees(conn) -> tuple[float, float]:
    from country_config import COUNTRY

    conn_fee = float(COUNTRY.default_connection_fee)
    rb_fee = float(COUNTRY.default_readyboard_fee)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT key, value FROM system_config
            WHERE key IN ('connection_fee_amount', 'readyboard_fee_amount')
            """
        )
        for key, value in cur.fetchall():
            if value is None or not str(value).strip():
                continue
            if key == "connection_fee_amount":
                conn_fee = float(value)
            elif key == "readyboard_fee_amount":
                rb_fee = float(value)
    except Exception:
        pass
    return conn_fee, rb_fee


def _load_account_context(cur) -> set[str]:
    cur.execute(
        """
        SELECT UPPER(TRIM(account_number))
        FROM accounts
        WHERE account_number IS NOT NULL AND TRIM(account_number) <> ''
        """
    )
    return {str(row[0]) for row in cur.fetchall() if row[0]}


def _annotate_payment(
    payment: NormalizedPayment,
    accounts: set[str],
) -> None:
    site = (payment.site_hint or "").upper()
    for candidate in candidate_accounts_from_text(payment.details_text):
        candidate = candidate.upper()
        if site and not candidate.endswith(site):
            continue
        if candidate in accounts:
            payment.account_number = candidate
            payment.resolution_method = "remark_account"
            return
    payment.account_number = None
    payment.resolution_method = "unmatched"


def _index_merchant_exports(
    root: Path,
    accounts: set[str],
) -> tuple[dict[str, list[NormalizedPayment]], dict[tuple[str, float], list[NormalizedPayment]]]:
    by_receipt: dict[str, list[NormalizedPayment]] = defaultdict(list)
    by_account_amount: dict[tuple[str, float], list[NormalizedPayment]] = defaultdict(list)
    seen = 0
    for payment in iter_payments_from_root(root):
        seen += 1
        if seen % 10000 == 0:
            log.info("Parsed %d merchant export payments...", seen)
        _annotate_payment(payment, accounts)
        receipt = _norm_ref(payment.external_id)
        if receipt:
            by_receipt[receipt].append(payment)
        if payment.account_number:
            key = (payment.account_number.strip().upper(), round(float(payment.amount), 2))
            by_account_amount[key].append(payment)
    log.info("Parsed %d merchant export payments", seen)
    return by_receipt, by_account_amount


def _pick_payment(
    candidates: list[NormalizedPayment],
    account: str,
    workbook_date: date | None,
) -> NormalizedPayment | None:
    if not candidates:
        return None
    account = account.strip().upper()
    ranked: list[tuple[int, NormalizedPayment]] = []
    for payment in candidates:
        score = 0
        if payment.account_number and payment.account_number.strip().upper() == account:
            score += 10
        if _within_date_window(payment.paid_at, workbook_date):
            score += 5
        ranked.append((score, payment))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] > 0 else candidates[0]


def _match_workbook_fee(
    cur,
    account: str,
    payment_type: str,
    paid_date: date | None,
    amount: Any,
    txn_ref: Any,
    *,
    by_receipt: dict[str, list[NormalizedPayment]],
    by_account_amount: dict[tuple[str, float], list[NormalizedPayment]],
    default_conn_fee: float,
    default_rb_fee: float,
) -> dict[str, str]:
    ledger = _IOB._resolve_fee_transaction(cur, account, payment_type, txn_ref)
    ledger_status = "no_ledger_txn"
    ledger_source = ""
    if ledger:
        cur.execute("SELECT source FROM transactions WHERE id = %s", (ledger,))
        row = cur.fetchone()
        ledger_source = str(row[0] or "") if row else ""
        cur.execute(
            """
            SELECT status FROM payment_verifications
            WHERE transaction_id = %s AND payment_type = %s
            ORDER BY id DESC LIMIT 1
            """,
            (ledger, payment_type),
        )
        pv = cur.fetchone()
        ledger_status = "verified_ledger" if pv and pv[0] == "verified" else "ledger_txn_unverified"

    target_amount = float(amount or 0)
    if target_amount <= 0:
        target_amount = default_conn_fee if payment_type == "connection_fee" else default_rb_fee

    merchant_match = "merchant_unmatched"
    merchant_receipt = ""
    merchant_account = ""
    merchant_amount = ""
    merchant_date = ""
    merchant_source_file = ""
    merchant_resolution = ""

    ref = _norm_ref(txn_ref)
    payment: NormalizedPayment | None = None
    if ref:
        direct = by_receipt.get(ref)
        if direct:
            payment = _pick_payment(direct, account, paid_date)
            merchant_match = (
                "merchant_receipt_and_account"
                if payment.account_number and payment.account_number.strip().upper() == account.strip().upper()
                else "merchant_receipt_other_account"
            )

    if payment is None:
        key = (account.strip().upper(), round(target_amount, 2))
        candidates = [
            p for p in by_account_amount.get(key, [])
            if _within_date_window(p.paid_at, paid_date)
        ]
        if candidates:
            payment = _pick_payment(candidates, account, paid_date)
            merchant_match = "merchant_account_amount_date"

    if payment is not None:
        merchant_receipt = payment.external_id
        merchant_account = payment.account_number or ""
        merchant_amount = str(payment.amount)
        merchant_date = payment.paid_at.date().isoformat()
        merchant_source_file = payment.source_file
        merchant_resolution = payment.resolution_method

    return {
        "account_number": account,
        "payment_type": payment_type,
        "workbook_paid_date": str(paid_date or ""),
        "workbook_amount": str(amount or ""),
        "workbook_txn_ref": str(txn_ref or ""),
        "merchant_match": merchant_match,
        "merchant_receipt": merchant_receipt,
        "merchant_account": merchant_account,
        "merchant_amount": merchant_amount,
        "merchant_date": merchant_date,
        "merchant_source_file": merchant_source_file,
        "merchant_resolution": merchant_resolution,
        "ledger_txn_id": str(ledger or ""),
        "ledger_status": ledger_status,
        "ledger_source": ledger_source,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=_IOB.DEFAULT_WORKBOOK)
    parser.add_argument("--merchant-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--report-csv",
        type=Path,
        default=Path("/tmp/onboarding_merchant_recon.csv"),
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=None,
        help="Optional JSON-lines progress log for long-running host jobs",
    )
    args = parser.parse_args()

    def _write_progress(stage: str, **fields: object) -> None:
        if not args.progress_file:
            return
        import json

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            **fields,
        }
        with args.progress_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    if not args.workbook.exists():
        log.error("Workbook not found: %s", args.workbook)
        return 2
    if not args.merchant_root.exists():
        log.error("Merchant export root not found: %s", args.merchant_root)
        return 2

    manifest = summarize_manifest(args.merchant_root)
    log.info("Merchant export manifest entries: %d", len(manifest))
    _write_progress("manifest", files=len(manifest))

    records = _IOB.load_customer_records(args.workbook)
    _IOB.merge_workbook_sheets(records, args.workbook)
    log.info("Loaded %d workbook customer rows", len(records))
    _write_progress("workbook_loaded", customers=len(records))

    conn = _connect()
    try:
        default_conn_fee, default_rb_fee = _load_default_fees(conn)
        cur = conn.cursor()
        accounts = _load_account_context(cur)
        log.info("Loaded %d accounts", len(accounts))
        _write_progress("accounts_loaded", accounts=len(accounts))
        by_receipt, by_account_amount = _index_merchant_exports(args.merchant_root, accounts)
        _write_progress(
            "merchant_indexed",
            receipts=len(by_receipt),
            account_amount_buckets=len(by_account_amount),
        )
        rows: list[dict[str, str]] = []
        for idx, record in enumerate(records, start=1):
            if idx % 250 == 0:
                log.info("Matched %d/%d workbook rows...", idx, len(records))
                _write_progress("matching", customers_done=idx, customers_total=len(records), fee_rows=len(rows))
            account = record["account_number"]
            if record.get("connection_fee_paid"):
                rows.append(
                    _match_workbook_fee(
                        cur,
                        account,
                        "connection_fee",
                        _as_date(record.get("connection_fee_paid")),
                        record.get("connection_fee_amount"),
                        record.get("connection_fee_txn"),
                        by_receipt=by_receipt,
                        by_account_amount=by_account_amount,
                        default_conn_fee=default_conn_fee,
                        default_rb_fee=default_rb_fee,
                    )
                )
            if record.get("readyboard_fee_paid"):
                rows.append(
                    _match_workbook_fee(
                        cur,
                        account,
                        "readyboard_fee",
                        _as_date(record.get("readyboard_fee_paid")),
                        record.get("readyboard_fee_amount"),
                        record.get("readyboard_fee_txn"),
                        by_receipt=by_receipt,
                        by_account_amount=by_account_amount,
                        default_conn_fee=default_conn_fee,
                        default_rb_fee=default_rb_fee,
                    )
                )
    finally:
        conn.close()

    fieldnames = sorted({k for row in rows for k in row.keys()})
    with args.report_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    merchant_summary = dict(Counter(r["merchant_match"] for r in rows))
    ledger_summary = dict(Counter(r["ledger_status"] for r in rows))
    log.info("Workbook fee rows reconciled: %d", len(rows))
    log.info("Merchant export summary: %s", merchant_summary)
    log.info("1PDB ledger summary: %s", ledger_summary)
    log.info("Report: %s", args.report_csv)
    _write_progress(
        "complete",
        fee_rows=len(rows),
        merchant_summary=merchant_summary,
        ledger_summary=ledger_summary,
        report_csv=str(args.report_csv),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
