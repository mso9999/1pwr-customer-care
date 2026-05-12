#!/usr/bin/env python3
"""Audit onboarding workbook fee rows against 1PDB ledger (merchant + SMS)."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/")
ACDB_API = Path(os.environ.get("ACDB_API", "/opt/cc-portal/backend"))
if not (ACDB_API / "customer_api.py").exists():
    ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "import_onboarding_workbook",
    Path(__file__).resolve().parent / "import_onboarding_workbook.py",
)
_IOB = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_IOB)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("audit_onboarding_workbook_payments")


def _fee_audit_row(
    cur,
    account: str,
    payment_type: str,
    paid_date,
    amount,
    txn_ref,
) -> dict[str, str]:
    txn_id = _IOB._resolve_fee_transaction(cur, account, payment_type, txn_ref)
    ledger_source = ""
    ledger_amount = ""
    ledger_date = ""
    pv_status = ""
    if txn_id:
        cur.execute(
            """
            SELECT source, transaction_amount, transaction_date::date, payment_reference
            FROM transactions WHERE id = %s
            """,
            (txn_id,),
        )
        trow = cur.fetchone()
        if trow:
            ledger_source = str(trow[0] or "")
            ledger_amount = str(trow[1] or "")
            ledger_date = str(trow[2] or "")
        cur.execute(
            """
            SELECT status FROM payment_verifications
            WHERE transaction_id = %s AND payment_type = %s
            ORDER BY id DESC LIMIT 1
            """,
            (txn_id, payment_type),
        )
        pv = cur.fetchone()
        if pv:
            pv_status = str(pv[0])

    if not paid_date:
        status = "no_workbook_fee"
    elif txn_id and pv_status == "verified":
        status = "verified_ledger"
    elif txn_id:
        status = "txn_unverified"
    else:
        status = "workbook_only"

    return {
        "account_number": account,
        "payment_type": payment_type,
        "workbook_paid_date": str(paid_date or ""),
        "workbook_amount": str(amount or ""),
        "workbook_txn_ref": str(txn_ref or ""),
        "ledger_txn_id": str(txn_id or ""),
        "ledger_source": ledger_source,
        "ledger_amount": ledger_amount,
        "ledger_date": ledger_date,
        "verification_status": pv_status,
        "audit_status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=_IOB.DEFAULT_WORKBOOK)
    parser.add_argument("--report-csv", type=Path, default=Path("/tmp/onboarding_payment_audit.csv"))
    args = parser.parse_args()

    if not args.workbook.exists():
        log.error("Workbook not found: %s", args.workbook)
        return 2

    records = _IOB.load_customer_records(args.workbook)
    _IOB.merge_workbook_sheets(records, args.workbook)
    conn = _IOB._connect()
    rows: list[dict[str, str]] = []
    try:
        cur = conn.cursor()
        for record in records:
            account = record["account_number"]
            if record.get("connection_fee_paid"):
                rows.append(
                    _fee_audit_row(
                        cur,
                        account,
                        "connection_fee",
                        record.get("connection_fee_paid"),
                        record.get("connection_fee_amount"),
                        record.get("connection_fee_txn"),
                    )
                )
            if record.get("readyboard_fee_paid"):
                rows.append(
                    _fee_audit_row(
                        cur,
                        account,
                        "readyboard_fee",
                        record.get("readyboard_fee_paid"),
                        record.get("readyboard_fee_amount"),
                        record.get("readyboard_fee_txn"),
                    )
                )
    finally:
        conn.close()

    fieldnames = sorted({k for row in rows for k in row.keys()})
    with args.report_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    summary = dict(Counter(r["audit_status"] for r in rows))
    log.info("Fee rows audited: %d", len(rows))
    log.info("Summary: %s", summary)
    log.info("Report: %s", args.report_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
