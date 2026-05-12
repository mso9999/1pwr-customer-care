"""
Parse Lesotho M-Pesa / EcoCash merchant export files into normalized inbound payments.

Used by ``scripts/ops/backfill_merchant_payments_from_exports.py`` to ingest
historical customer payments that never reached the live SMS gateway phone.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from mpesa_sms import (
    candidate_accounts_from_text,
    phone_to_account,
    resolve_sms_account,
)

logger = logging.getLogger("cc-api.merchant-export")

DEFAULT_ROOT = Path(
    "/Users/mattmso/Dropbox/1PWR/1PWR Financial Records/mobile money records"
)

EXCLUDE_PATH_PARTS = (
  "econet merchant withdrawals",
  "payroll",
  "archive",
  "verification information from pm team",
  "no transactions",
)

SITE_CODE_RE = re.compile(r"\(([A-Za-z]{2,4})\)")
TILL_ID_RE = re.compile(r"till[-\s]*(\d+)", re.IGNORECASE)
ECOCASH_MAT_RE = re.compile(r"ecocash\s+([a-z]{2,4})\s+(\d+)", re.IGNORECASE)

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "receipt": (
        "receipt no",
        "receiptno",
        "receipt number",
        "transaction id",
        "trans id",
        "confirmation code",
        "approval code",
        "txn id",
        "reference",
        "trade receipt number",
        "payment receipt number",
    ),
    "paid_at": (
        "completion time",
        "completed time",
        "transaction date",
        "txn date",
        "date",
        "time",
        "finish time",
        "initiation time",
    ),
    "details": (
        "details",
        "description",
        "narrative",
        "transaction details",
        "remark",
        "particulars",
    ),
    "paid_in": (
        "paid in",
        "paidin",
        "deposit",
        "credit amount",
        "amount received",
        "credit",
    ),
    "paid_out": (
        "withdrawn",
        "paid out",
        "paidout",
        "debit amount",
        "withdrawal",
        "debit",
    ),
    "amount": ("amount",),
    "direction": ("type", "dr/cr", "credit/debit", "debit/credit"),
    "payer_phone": (
        "other party info",
        "sender msisdn",
        "phone",
        "msisdn",
        "payer phone",
        "from",
        "opposite party",
    ),
}

RECEIPT_TOKEN_RE = re.compile(r"\b([A-Z0-9]{8,14})\b")
MP_REF_RE = re.compile(r"\bMP\d{6}\.\d{4}\.A\d{5,}\b", re.IGNORECASE)
PAYMERCHANT_PHONE_RE = re.compile(
    r"paymerchant from\s+(\d{8,15})",
    re.IGNORECASE,
)
NON_CUSTOMER_DETAIL_RE = re.compile(
    r"(organization withdrawal|merchant withdrawal|pay merchant reversal|reversal of funds)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MerchantAccount:
    key: str
    label: str
    provider: str
    site_code: str
    till_id: str
    relative_path: str


@dataclass
class NormalizedPayment:
    external_id: str
    amount: float
    currency: str
    paid_at: datetime
    payer_phone: str
    details_text: str
    merchant_account_key: str
    source_file: str
    source_row: int
    provider: str
    site_hint: str = ""
    account_number: str | None = None
    resolution_method: str = "unmatched"
    resolution_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _norm_header(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text.rstrip(".")


def _map_headers(headers: list[str]) -> dict[str, int]:
    normalized = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for idx, header in enumerate(normalized):
            if header in aliases:
                mapping[canonical] = idx
                break
    return mapping


def _parse_amount(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    text = text.lstrip("M").strip()
    if not text:
        return 0.0
    return float(text)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_phone_from_text(text: str) -> str:
    pay_merchant = PAYMERCHANT_PHONE_RE.search(text or "")
    if pay_merchant:
        return "".join(c for c in pay_merchant.group(1) if c.isdigit())
    digits = re.findall(r"\d{8,15}", text or "")
    if not digits:
        return ""
    return "".join(c for c in digits[0] if c.isdigit())


def _extract_receipt_from_details(text: str) -> str:
    if not text:
        return ""
    mp_ref = MP_REF_RE.search(text)
    if mp_ref:
        return mp_ref.group(0)
    first_field = text.split(",", 1)[0].strip().strip('"')
    if RECEIPT_TOKEN_RE.fullmatch(first_field):
        return first_field
    match = RECEIPT_TOKEN_RE.search(text)
    return match.group(1) if match else ""


def _is_header_mapping(mapping: dict[str, int]) -> bool:
    if "details" not in mapping:
        return False
    return any(key in mapping for key in ("paid_in", "paid_out", "amount"))


def _resolve_paid_at(
    row: dict[str, Any],
    headers: list[str],
    values: list[Any],
) -> datetime | None:
    for idx, header in enumerate(headers):
        if _norm_header(header) != "date":
            continue
        dt = _parse_datetime(values[idx] if idx < len(values) else None)
        if dt and (dt.hour or dt.minute or dt.second):
            return dt
    for key in ("paid_at",):
        dt = _parse_datetime(row.get(key))
        if dt:
            return dt
    for idx, header in enumerate(headers):
        if _norm_header(header) != "date":
            continue
        dt = _parse_datetime(values[idx] if idx < len(values) else None)
        if dt:
            return dt
    return None


def _path_excluded(path: Path) -> bool:
    low = str(path).lower()
    return any(part in low for part in EXCLUDE_PATH_PARTS)


def _infer_site_code(path: Path) -> str:
    for part in path.parts:
        m = SITE_CODE_RE.search(part)
        if m:
            return m.group(1).upper()
    m = ECOCASH_MAT_RE.search(str(path))
    if m:
        return m.group(1).upper()
    return ""


def _infer_provider(path: Path) -> str:
    low = str(path).lower()
    if "ecocash" in low:
        return "ecocash"
    return "mpesa"


def _infer_till_id(path: Path) -> str:
    for part in path.parts:
        m = TILL_ID_RE.search(part)
        if m:
            return m.group(1)
    m = ECOCASH_MAT_RE.search(str(path))
    if m:
        return m.group(2)
    return ""


def _merchant_account_for_file(path: Path) -> MerchantAccount:
    provider = _infer_provider(path)
    site_code = _infer_site_code(path)
    till_id = _infer_till_id(path)
    rel = path.name
    key = f"{provider}:{till_id or 'unknown'}:{site_code or 'any'}:{rel}"
    return MerchantAccount(
        key=key,
        label=path.parent.name,
        provider=provider,
        site_code=site_code,
        till_id=till_id,
        relative_path=rel,
    )


def iter_parse_targets(root: Path) -> Iterable[tuple[Path, MerchantAccount]]:
    if root.is_file():
        yield root, _merchant_account_for_file(root)
        return
    for account in build_merchant_manifest(root):
        yield root / account.relative_path, account


def build_merchant_manifest(root: Path) -> list[MerchantAccount]:
    if not root.is_dir():
        return []

    seen: set[str] = set()
    accounts: list[MerchantAccount] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".txt"}:
            continue
        if _path_excluded(path):
            continue
        if path.stat().st_size == 0:
            continue

        rel = path.relative_to(root).as_posix()
        provider = _infer_provider(path)
        site_code = _infer_site_code(path)
        till_id = _infer_till_id(path)
        key = f"{provider}:{till_id or 'unknown'}:{site_code or 'any'}:{rel}"
        if key in seen:
            continue
        seen.add(key)
        accounts.append(
            MerchantAccount(
                key=key,
                label=path.parent.name,
                provider=provider,
                site_code=site_code,
                till_id=till_id,
                relative_path=rel,
            )
        )
    return accounts


def _row_is_inbound(row: dict[str, Any]) -> tuple[bool, float]:
    if "paid_in" in row or "paid_out" in row:
        paid_in = _parse_amount(row.get("paid_in"))
        paid_out = _parse_amount(row.get("paid_out"))
        if paid_out > 0 and paid_in <= 0:
            return False, 0.0
        if paid_in > 0:
            return True, paid_in
        return False, 0.0

    amount = _parse_amount(row.get("amount"))
    if amount <= 0:
        return False, 0.0
    direction = str(row.get("direction") or "").strip().upper()
    if direction in {"DEBIT", "DR", "WITHDRAWAL", "WITHDRAWN"}:
        return False, 0.0
    if direction in {"CREDIT", "CR"}:
        return True, amount
    details = str(row.get("details") or "").lower()
    if NON_CUSTOMER_DETAIL_RE.search(details):
        return False, 0.0
    if "withdrawal" in details or "merchant withdrawal" in details:
        return False, 0.0
    return True, amount


def _iter_calamine_rows(path: Path) -> Iterator[tuple[int, list[Any]]]:
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:
        raise RuntimeError("python-calamine is required to read .xls merchant exports") from exc
    try:
        workbook = CalamineWorkbook.from_path(path)
        sheet = workbook.get_sheet_by_index(0)
        rows = sheet.to_python()
    except Exception as exc:
        logger.warning("Could not read workbook %s: %s", path, exc)
        return
    for idx, row in enumerate(rows, start=1):
        yield idx, list(row)


def _iter_sheet_rows(path: Path) -> Iterator[tuple[int, list[Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            for idx, row in enumerate(reader, start=1):
                yield idx, row
        return

    if suffix == ".xls":
        yield from _iter_calamine_rows(path)
        return

    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise RuntimeError("openpyxl is required to read .xlsx merchant exports") from exc
        try:
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            logger.warning("Could not read workbook %s: %s", path, exc)
            return
        sheet = workbook.active
        for idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            yield idx, list(row)
        return

    if suffix == ".txt":
        with path.open(encoding="utf-8-sig", errors="replace") as handle:
            for idx, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                if "\t" in line:
                    yield idx, line.split("\t")
                elif "," in line:
                    yield idx, next(csv.reader([line]))
                else:
                    yield idx, [line]
        return

    raise ValueError(f"Unsupported merchant export file type: {path.suffix}")


def _rows_to_dicts(path: Path) -> Iterator[tuple[int, dict[str, Any], list[str], list[Any]]]:
    sheet_rows = list(_iter_sheet_rows(path))
    header_row_idx: int | None = None
    headers: list[str] = []
    mapping: dict[str, int] = {}

    for row_idx, values in sheet_rows[:50]:
        candidate_headers = [str(cell or "").strip() for cell in values]
        candidate_mapping = _map_headers(candidate_headers)
        if _is_header_mapping(candidate_mapping):
            header_row_idx = row_idx
            headers = candidate_headers
            mapping = candidate_mapping
            break

    if header_row_idx is None:
        return

    for row_idx, values in sheet_rows:
        if row_idx <= header_row_idx:
            continue
        if not any(str(v or "").strip() for v in values):
            continue
        row: dict[str, Any] = {}
        for canonical, col_idx in mapping.items():
            row[canonical] = values[col_idx] if col_idx < len(values) else None
        yield row_idx, row, headers, list(values)


def parse_merchant_export_file(
    path: Path,
    *,
    merchant_account_key: str,
    provider: str,
    site_hint: str = "",
    currency: str = "LSL",
) -> list[NormalizedPayment]:
    payments: list[NormalizedPayment] = []
    for row_idx, row, headers, values in _rows_to_dicts(path):
        inbound, amount = _row_is_inbound(row)
        if not inbound or amount <= 0:
            continue

        details = str(row.get("details") or "").strip()
        payer_blob = str(row.get("payer_phone") or "").strip()
        payer_phone = _extract_phone_from_text(payer_blob) or _extract_phone_from_text(details)
        external_id = str(row.get("receipt") or "").strip() or _extract_receipt_from_details(details)
        paid_at = _resolve_paid_at(row, headers, values) or datetime.now(timezone.utc)

        payments.append(
            NormalizedPayment(
                external_id=external_id,
                amount=amount,
                currency=currency,
                paid_at=paid_at,
                payer_phone=payer_phone,
                details_text=details,
                merchant_account_key=merchant_account_key,
                source_file=path.as_posix(),
                source_row=row_idx,
                provider=provider,
                site_hint=site_hint,
                raw=row,
            )
        )
    return payments


def _account_matches_site(account_number: str, site_hint: str) -> bool:
    if not site_hint:
        return True
    return account_number.upper().endswith(site_hint.upper())


def resolve_payment_account(conn, payment: NormalizedPayment) -> NormalizedPayment:
    parsed = {
        "remark_raw": payment.details_text,
        "phone": payment.payer_phone,
        "txn_id": payment.external_id,
        "amount": payment.amount,
        "reference": "",
        "provider": payment.provider,
    }
    content = payment.details_text
    if payment.payer_phone and payment.payer_phone not in content:
        content = f"{content} {payment.payer_phone}"

    account, method, remark, reason = resolve_sms_account(conn, content, parsed)
    if account and _account_matches_site(account, payment.site_hint):
        payment.account_number = account
        payment.resolution_method = method
        payment.resolution_reason = reason
        payment.details_text = remark or payment.details_text
        return payment

    candidates = candidate_accounts_from_text(payment.details_text)
    for candidate in candidates:
        if payment.site_hint and not _account_matches_site(candidate, payment.site_hint):
            continue
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM accounts WHERE UPPER(TRIM(account_number)) = UPPER(TRIM(%s)) LIMIT 1",
            (candidate,),
        )
        if cur.fetchone():
            payment.account_number = candidate
            payment.resolution_method = "remark_account"
            payment.resolution_reason = reason or "candidate_from_details"
            return payment

    if payment.payer_phone:
        phone_acct = phone_to_account(conn, payment.payer_phone)
        if phone_acct and _account_matches_site(phone_acct, payment.site_hint):
            payment.account_number = phone_acct
            payment.resolution_method = "phone_fallback"
            payment.resolution_reason = reason or "phone_lookup"
            return payment

    payment.account_number = None
    payment.resolution_method = "unmatched"
    payment.resolution_reason = reason or "no_account_match"
    return payment


def iter_payments_from_root(
    root: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    merchant_key: str | None = None,
) -> Iterable[NormalizedPayment]:
    for file_path, account in iter_parse_targets(root):
        if merchant_key and account.key != merchant_key:
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
            yield payment


def summarize_manifest(root: Path) -> list[dict[str, str]]:
    if root.is_file():
        account = _merchant_account_for_file(root)
        return [
            {
                "key": account.key,
                "label": account.label,
                "provider": account.provider,
                "site_code": account.site_code,
                "till_id": account.till_id,
                "relative_path": account.relative_path,
            }
        ]
    return [
        {
            "key": item.key,
            "label": item.label,
            "provider": item.provider,
            "site_code": item.site_code,
            "till_id": item.till_id,
            "relative_path": item.relative_path,
        }
        for item in build_merchant_manifest(root)
    ]
