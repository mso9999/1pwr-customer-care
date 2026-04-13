"""
Lesotho M-Pesa SMS parsing and account resolution (Remark-first, phone fallback).

Used by ingest.sms_incoming and by ops reconciliation scripts.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

# Confirmation with Reference line (legacy)
MPESA_PATTERN = re.compile(
    r"(?P<txn_id>\w+)\s+Confirmed\.\s+on\s+.+?"
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\d{8,15})"
    r".*?Reference:\s*(?P<ref>\d+)",
    re.IGNORECASE | re.DOTALL,
)

# Amount + phone without requiring Reference (Remark-only or alternate layouts)
MPESA_LOOSE = re.compile(
    r"(?P<txn_id>[\w-]+)\s+Confirmed\.\s+on\s+.+?"
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\d{8,15})",
    re.IGNORECASE | re.DOTALL,
)

MPESA_FALLBACK = re.compile(
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+(?P<phone>\d{8,15})",
    re.IGNORECASE,
)

REF_PATTERN = re.compile(r"Reference:\s*(\d+)", re.IGNORECASE)
REMARK_PATTERN = re.compile(
    r"Remark:\s*(.+?)(?:\n|$)",
    re.IGNORECASE | re.DOTALL,
)
# Lesotho account: 3–4 digits + 2–4 letter site code (e.g. 0252SHG, 0045 MAK)
ACCOUNT_TOKEN_RE = re.compile(
    r"\b(\d{3,4})\s*([A-Za-z]{2,4})\b",
    re.IGNORECASE,
)


def extract_remark_text(content: str) -> str:
    """Text after 'Remark:' if present."""
    m = REMARK_PATTERN.search(content or "")
    if m:
        return " ".join(m.group(1).split()).strip()
    return ""


def candidate_accounts_from_text(text: str) -> list[str]:
    """Ordered unique account candidates like 0252SHG from digit+site patterns."""
    seen: set[str] = set()
    out: list[str] = []
    if not text:
        return out
    for m in ACCOUNT_TOKEN_RE.finditer(text):
        acct = f"{m.group(1)}{m.group(2).upper()}"
        if acct not in seen:
            seen.add(acct)
            out.append(acct)
    return out


def parse_mpesa_sms(content: str) -> Optional[dict[str, Any]]:
    """Parse M-Pesa confirmation SMS. Returns dict with txn_id, amount, phone, reference, remark_raw."""
    m = MPESA_PATTERN.search(content)
    if m:
        remark = extract_remark_text(content)
        return {
            "txn_id": m.group("txn_id"),
            "amount": float(m.group("amount")),
            "phone": m.group("phone"),
            "reference": m.group("ref"),
            "remark_raw": remark,
            "provider": "mpesa",
        }

    m = MPESA_LOOSE.search(content)
    if m:
        ref_match = REF_PATTERN.search(content)
        remark = extract_remark_text(content)
        return {
            "txn_id": m.group("txn_id"),
            "amount": float(m.group("amount")),
            "phone": m.group("phone"),
            "reference": ref_match.group(1) if ref_match else "",
            "remark_raw": remark,
            "provider": "mpesa",
        }

    m = MPESA_FALLBACK.search(content)
    if m:
        ref_match = REF_PATTERN.search(content)
        remark = extract_remark_text(content)
        return {
            "txn_id": "",
            "amount": float(m.group("amount")),
            "phone": m.group("phone"),
            "reference": ref_match.group(1) if ref_match else "",
            "remark_raw": remark,
            "provider": "mpesa",
        }

    return None


def account_exists(conn, account_number: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM accounts WHERE UPPER(TRIM(account_number)) = UPPER(TRIM(%s)) LIMIT 1",
        (account_number,),
    )
    return cur.fetchone() is not None


def phone_to_account(conn, phone_digits: str) -> Optional[str]:
    """Look up account number from customer phone (cell)."""
    normalized = phone_digits.lstrip("0")
    if normalized.startswith("266"):
        normalized = normalized[3:]

    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.account_number
        FROM customers c
        JOIN accounts a ON a.customer_id = c.id
        WHERE replace(replace(replace(COALESCE(c.phone,''), '+', ''), ' ', ''), '-', '') LIKE %s
           OR replace(replace(replace(COALESCE(c.cell_phone_1,''), '+', ''), ' ', ''), '-', '') LIKE %s
        LIMIT 1
        """,
        (f"%{normalized}", f"%{normalized}"),
    )
    row = cur.fetchone()
    return str(row[0]).strip() if row else None


def resolve_sms_account(
    conn,
    content: str,
    parsed: dict[str, Any],
) -> Tuple[Optional[str], str, str, str]:
    """
    Resolve credit target account: Remark-derived account first, else phone lookup.

    Returns:
        (account_number or None, allocation, remark_stored, reason_if_fallback)
        allocation: 'remark_account' | 'phone_fallback' | 'none'
        reason_if_fallback: empty if remark_account; else short explanation
    """
    remark = (parsed.get("remark_raw") or "").strip() or extract_remark_text(content)

    # 1) Candidates from Remark line only
    candidates: list[str] = []
    if remark:
        candidates.extend(candidate_accounts_from_text(remark))

    # 2) If nothing in Remark, scan full SMS (still before phone)
    if not candidates:
        candidates.extend(candidate_accounts_from_text(content))

    tried: list[str] = []
    for acct in candidates:
        if acct in tried:
            continue
        tried.append(acct)
        if account_exists(conn, acct):
            return acct, "remark_account", remark[:500], ""

    # 3) Phone fallback
    phone = parsed.get("phone") or ""
    phone_acct = phone_to_account(conn, str(phone)) if phone else None

    if not candidates:
        reason = "no_account_pattern_in_remark_or_sms"
    else:
        reason = f"remark_candidates_not_in_db:{','.join(tried)}"

    if phone_acct:
        return phone_acct, "phone_fallback", remark[:500], reason

    return None, "none", remark[:500], reason


def mpesa_receipt_in_use(conn, receipt: str) -> bool:
    """True if this M-Pesa receipt id was already stored on a transaction."""
    r = (receipt or "").strip()
    if not r:
        return False
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM transactions WHERE payment_reference = %s LIMIT 1",
            (r,),
        )
        return cur.fetchone() is not None
    except Exception:
        conn.rollback()
        return False
