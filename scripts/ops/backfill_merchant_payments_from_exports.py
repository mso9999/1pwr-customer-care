#!/usr/bin/env python3
"""
Backfill 1PDB customer payments from Lesotho M-Pesa / EcoCash merchant exports.

Reads finance export files (CSV / XLSX / TXT), resolves accounts with the same
Remark-first / phone-fallback rules as SMS ingest, deduplicates against existing
1PDB rows, and optionally inserts missing payments as ``source = merchant_export``.

Fee rows are auto-verified in ``payment_verifications``. Electricity rows do not
call SparkMeter credit and do not credit kWh balance (historical ledger only).

Examples:
  PYTHONPATH=acdb-api python3 scripts/ops/backfill_merchant_payments_from_exports.py \\
      --root scripts/ops/fixtures/merchant_exports --dry-run --report-csv /tmp/mm-backfill.csv

  PYTHONPATH=acdb-api python3 scripts/ops/backfill_merchant_payments_from_exports.py \\
      --root docs/TRANSACTION_20260512152511.xlsx --dry-run --report-csv /tmp/mm-recent.csv

  PYTHONPATH=acdb-api DATABASE_URL=postgresql://... \\
      python3 scripts/ops/backfill_merchant_payments_from_exports.py --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg2.extensions

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

from merchant_export_parser import (  # noqa: E402
    DEFAULT_ROOT,
    NormalizedPayment,
    iter_parse_targets,
    iter_payments_from_root,
    parse_merchant_export_file,
    resolve_payment_account,
    summarize_manifest,
)

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(format=LOG_FMT, level=logging.INFO)
logger = logging.getLogger("cc-ops.mm-backfill")

VERIFIED_BY = "merchant_export_backfill"
# 1PDB `transaction_source` enum has no `merchant_export`; book as portal and
# keep provenance in `source_table` / SMS metadata columns.
SOURCE = "portal"
_AMOUNT_EPSILON = 0.005


def _amounts_match(paid: float, target: float) -> bool:
    if target <= 0:
        return False
    return abs(round(paid, 2) - round(target, 2)) < _AMOUNT_EPSILON


def _has_verified_fee(conn, account_number: str, payment_type: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM payment_verifications
            WHERE account_number = %s
              AND payment_type = %s
              AND status = 'verified'
            LIMIT 1
            """,
            (account_number, payment_type),
        )
        return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("Fee verification lookup failed for %s: %s", account_number, exc)
        return False


def _load_country_fees(conn) -> dict[str, float | str]:
    from country_config import COUNTRY

    cur = conn.cursor()
    fees = {
        "connection_fee_amount": float(COUNTRY.default_connection_fee),
        "readyboard_fee_amount": float(COUNTRY.default_readyboard_fee),
        "currency": COUNTRY.currency,
    }
    try:
        cur.execute(
            """
            SELECT key, value FROM system_config
            WHERE key IN ('connection_fee_amount', 'readyboard_fee_amount')
            """
        )
        for key, value in cur.fetchall():
            if value is not None and str(value).strip():
                fees[key] = float(value)
    except Exception as exc:
        logger.warning("Could not read country fees from system_config: %s", exc)
    return fees


def _classify_payment_for_backfill(conn, account_number: str, amount: float) -> dict[str, Any]:
    if amount is None or amount <= 0:
        return {"category": "electricity", "matched_amount": None, "currency": ""}

    fees = _load_country_fees(conn)
    conn_fee = float(fees.get("connection_fee_amount") or 0)
    rb_fee = float(fees.get("readyboard_fee_amount") or 0)
    currency = str(fees.get("currency") or "")

    if _amounts_match(amount, conn_fee) and not _has_verified_fee(
        conn, account_number, "connection_fee"
    ):
        return {
            "category": "connection_fee",
            "matched_amount": conn_fee,
            "currency": currency,
        }

    if _amounts_match(amount, rb_fee) and not _has_verified_fee(
        conn, account_number, "readyboard_fee"
    ):
        return {
            "category": "readyboard_fee",
            "matched_amount": rb_fee,
            "currency": currency,
        }

    return {
        "category": "electricity",
        "matched_amount": None,
        "currency": currency,
    }


def _parse_cli_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"Invalid datetime: {value!r}") from exc
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _payment_ref_taken(conn, ref: str) -> tuple[int, str] | None:
    receipt = (ref or "").strip()
    if not receipt:
        return None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number FROM transactions
        WHERE lower(trim(payment_reference)) = lower(trim(%s))
          AND payment_reference IS NOT NULL AND trim(payment_reference) <> ''
        LIMIT 1
        """,
        (receipt,),
    )
    row = cur.fetchone()
    return (int(row[0]), str(row[1])) if row else None


def _resolve_meter(conn, account_number: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "SELECT meter_id FROM meters WHERE account_number = %s AND status = 'active' LIMIT 1",
        (account_number,),
    )
    row = cur.fetchone()
    return row[0] if row else ""


def _get_tariff_rate(conn, account_number: str) -> float:
    from country_config import COUNTRY, get_tariff_rate_for_site

    cur = conn.cursor()
    cur.execute(
        "SELECT community FROM meters WHERE account_number = %s AND status = 'active' LIMIT 1",
        (account_number,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return get_tariff_rate_for_site(row[0])
    cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate' LIMIT 1")
    row = cur.fetchone()
    return float(row[0]) if row else COUNTRY.default_tariff_rate


def _create_verification_entry(
    conn,
    transaction_id: int,
    account_number: str,
    payment_type: str,
    amount: float,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_verifications
            (transaction_id, account_number, payment_type, amount)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (transaction_id, account_number, payment_type, amount),
    )
    return int(cur.fetchone()[0])


def _fuzzy_already_credited(
    conn,
    account_number: str,
    amount: float,
    paid_at: datetime,
) -> bool:
    window_start = paid_at - timedelta(hours=24)
    window_end = paid_at + timedelta(hours=24)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM transactions
        WHERE account_number = %s
          AND is_payment = true
          AND transaction_amount BETWEEN %s AND %s
          AND transaction_date BETWEEN %s AND %s
        LIMIT 1
        """,
        (account_number, amount - 0.01, amount + 0.01, window_start, window_end),
    )
    return cur.fetchone() is not None


def _ref_in_inbound_log(conn, receipt: str) -> bool:
    receipt = (receipt or "").strip()
    if not receipt:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM sms_inbound_log WHERE receipt_key = %s LIMIT 1",
            (receipt,),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _conflict_for_reference(
    conn,
    receipt: str,
    account_number: str,
    amount: float,
) -> str | None:
    receipt = (receipt or "").strip()
    if not receipt:
        return None
    dup = _payment_ref_taken(conn, receipt)
    if not dup:
        return None
    txn_id, existing_account = dup
    cur = conn.cursor()
    cur.execute(
        "SELECT transaction_amount FROM transactions WHERE id = %s",
        (txn_id,),
    )
    row = cur.fetchone()
    existing_amount = float(row[0]) if row and row[0] is not None else None
    if existing_account != account_number or (
        existing_amount is not None and abs(existing_amount - amount) > 0.01
    ):
        return (
            f"reference {receipt} already on txn {txn_id} "
            f"acct={existing_account} amount={existing_amount}"
        )
    return None


def _source_table_tag(payment: NormalizedPayment) -> str:
    receipt = (payment.external_id or "noref")[:24]
    return f"mm:{receipt}:r{payment.source_row}"[:50]


def _annotate_transaction(
    conn,
    txn_id: int,
    *,
    source_table: str,
    payer_phone: str,
    details_text: str,
    payment_category: str | None = None,
) -> None:
    cur = conn.cursor()
    cur.execute("SAVEPOINT mm_backfill_annotate")
    try:
        sets = ["source_table = %s"]
        params: list[Any] = [source_table[:50]]
        if payer_phone:
            sets.append("sms_payer_phone = %s")
            params.append(payer_phone)
        if details_text:
            sets.append("sms_remark_raw = %s")
            params.append(details_text[:500])
        if payment_category:
            sets.append("payment_category = %s")
            params.append(payment_category)
        params.append(txn_id)
        cur.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        cur.execute("RELEASE SAVEPOINT mm_backfill_annotate")
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT mm_backfill_annotate")
        logger.warning("Could not annotate txn %s: %s", txn_id, exc)


def _auto_verify_fee(conn, txn_id: int, note: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE payment_verifications
        SET status = 'verified',
            verified_by = %s,
            verified_at = NOW(),
            note = COALESCE(%s, note)
        WHERE transaction_id = %s AND status = 'pending'
        """,
        (VERIFIED_BY, note, txn_id),
    )


def _insert_payment(
    conn,
    payment: NormalizedPayment,
    *,
    apply: bool,
) -> dict[str, Any]:
    from balance_engine import (
        record_fee_transaction,
        record_historical_payment_transaction,
    )

    source_table = _source_table_tag(payment)
    note = f"merchant export {payment.source_file}"
    classification = _classify_payment_for_backfill(conn, payment.account_number, payment.amount)
    category = classification["category"]
    meter_id = _resolve_meter(conn, payment.account_number)
    rate = _get_tariff_rate(conn, payment.account_number)

    result = {
        "outcome": "would_insert",
        "category": category,
        "account_number": payment.account_number,
        "amount": payment.amount,
        "external_id": payment.external_id,
        "paid_at": payment.paid_at.isoformat(),
        "resolution_method": payment.resolution_method,
        "source_file": payment.source_file,
        "source_row": payment.source_row,
    }

    if not apply:
        return result

    if category in ("connection_fee", "readyboard_fee"):
        txn_id, _ = record_fee_transaction(
            conn,
            payment.account_number,
            meter_id,
            amount_currency=payment.amount,
            payment_category=category,
            source=SOURCE,
            timestamp=payment.paid_at,
            payment_reference=payment.external_id or None,
            extra_columns={
                "source_table": source_table,
                "sms_payer_phone": payment.payer_phone or None,
                "sms_remark_raw": payment.details_text[:500] or None,
            },
        )
        _create_verification_entry(
            conn, txn_id, payment.account_number, category, payment.amount,
        )
        _auto_verify_fee(conn, txn_id, note)
        try:
            from onboarding_derive import derive_payment_steps_for_accounts

            derive_payment_steps_for_accounts(conn, [payment.account_number])
        except Exception as exc:
            logger.warning("Onboarding derive failed for %s: %s", payment.account_number, exc)
    else:
        txn_id, _ = record_historical_payment_transaction(
            conn,
            payment.account_number,
            meter_id,
            payment.amount,
            rate,
            source=SOURCE,
            timestamp=payment.paid_at,
            payment_reference=payment.external_id or None,
            extra_columns={
                "source_table": source_table,
                "sms_payer_phone": payment.payer_phone or None,
                "sms_remark_raw": payment.details_text[:500] or None,
                "payment_category": "electricity",
            },
        )

    result["outcome"] = "inserted"
    result["transaction_id"] = txn_id
    return result


def process_payments(
    conn,
    payments: list[NormalizedPayment],
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payment in payments:
        base = {
            "external_id": payment.external_id,
            "amount": payment.amount,
            "paid_at": payment.paid_at.isoformat(),
            "payer_phone": payment.payer_phone,
            "details_text": payment.details_text,
            "merchant_account_key": payment.merchant_account_key,
            "source_file": payment.source_file,
            "source_row": payment.source_row,
            "site_hint": payment.site_hint,
            "provider": payment.provider,
        }

        if not payment.account_number:
            rows.append({**base, "outcome": "unmatched_account", "reason": payment.resolution_reason})
            continue

        if payment.external_id:
            conflict = _conflict_for_reference(
                conn, payment.external_id, payment.account_number, payment.amount,
            )
            if conflict:
                rows.append({**base, "outcome": "conflict", "reason": conflict, "account_number": payment.account_number})
                continue
            if _payment_ref_taken(conn, payment.external_id):
                rows.append({**base, "outcome": "skipped_duplicate_ref", "account_number": payment.account_number})
                continue
            if _ref_in_inbound_log(conn, payment.external_id):
                rows.append({**base, "outcome": "skipped_duplicate_ref", "account_number": payment.account_number, "reason": "sms_inbound_log"})
                continue

        if _fuzzy_already_credited(conn, payment.account_number, payment.amount, payment.paid_at):
            rows.append({**base, "outcome": "skipped_fuzzy_duplicate", "account_number": payment.account_number})
            continue

        inserted = _insert_payment(conn, payment, apply=apply)
        rows.append({**base, **inserted})
    return rows


def collect_payments(
    root: Path,
    conn,
    *,
    since: datetime | None,
    until: datetime | None,
    merchant_key: str | None,
) -> list[NormalizedPayment]:
    collected: list[NormalizedPayment] = []
    for payment in iter_payments_from_root(
        root, since=since, until=until, merchant_key=merchant_key,
    ):
        resolve_payment_account(conn, payment)
        collected.append(payment)
    return collected


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("outcome\n")
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict[str, Any]]) -> None:
    counts = Counter(row.get("outcome", "unknown") for row in rows)
    logger.info("Summary: %s", dict(counts))
    would = [row for row in rows if row.get("outcome") in {"would_insert", "inserted"}]
    fee = sum(1 for row in would if row.get("category") in {"connection_fee", "readyboard_fee"})
    elec = sum(1 for row in would if row.get("category") == "electricity")
    amount = sum(float(row.get("amount") or 0) for row in would)
    logger.info(
        "Insertable payments: %d (fee=%d electricity=%d) total LSL %.2f",
        len(would), fee, elec, amount,
    )


def validate_after_apply(conn) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE source = %s AND source_table IS NOT NULL AND source_table LIKE %s
        """,
        (SOURCE, "mm:%"),
    )
    txn_count = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT COUNT(*) FROM payment_verifications pv
        JOIN transactions t ON t.id = pv.transaction_id
        WHERE t.source = %s AND pv.status = 'verified' AND pv.verified_by = %s
          AND t.source_table IS NOT NULL AND t.source_table LIKE %s
        """,
        (SOURCE, VERIFIED_BY, "mm:%"),
    )
    verified_fees = int(cur.fetchone()[0])
    return {"merchant_export_transactions": txn_count, "auto_verified_fees": verified_fees}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--until", type=str, default=None)
    parser.add_argument("--merchant-key", type=str, default=None)
    parser.add_argument("--report-csv", type=Path, default=None)
    parser.add_argument("--manifest-json", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Write rows to 1PDB")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only (default unless --apply is set)",
    )
    args = parser.parse_args()

    apply = bool(args.apply)
    if not apply:
        args.dry_run = True

    since = _parse_cli_datetime(args.since)
    until = _parse_cli_datetime(args.until)

    manifest = summarize_manifest(args.root)
    if args.manifest_json:
        args.manifest_json.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("Wrote manifest with %d files to %s", len(manifest), args.manifest_json)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        if apply:
            logger.error("DATABASE_URL is required for --apply")
            return 2
        logger.warning("DATABASE_URL not set; account resolution and dedup will be skipped")
        payments: list[NormalizedPayment] = []
        for file_path, account in iter_parse_targets(args.root):
            if args.merchant_key and account.key != args.merchant_key:
                continue
            for payment in parse_merchant_export_file(
                file_path,
                merchant_account_key=account.key,
                provider=account.provider,
                site_hint=account.site_code,
            ):
                if since and payment.paid_at < since:
                    continue
                if until and payment.paid_at > until:
                    continue
                payments.append(payment)
        rows = [
            {
                **asdict(payment),
                "outcome": "parsed_only",
                "paid_at": payment.paid_at.isoformat(),
            }
            for payment in payments
        ]
        print_summary(rows)
        if args.report_csv:
            write_report(args.report_csv, rows)
        return 0

    import psycopg2

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        payments = collect_payments(
            args.root, conn, since=since, until=until, merchant_key=args.merchant_key,
        )
        rows = process_payments(conn, payments, apply=apply)
        if apply:
            conn.commit()
            validation = validate_after_apply(conn)
            logger.info("Post-apply validation: %s", validation)
        else:
            conn.rollback()
        print_summary(rows)
        if args.report_csv:
            write_report(args.report_csv, rows)
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
