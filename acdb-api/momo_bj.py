"""
Benin MTN MoMo SMS parsing and account resolution (text-first, phone fallback).

Templates vary (French/English). Real samples should be added to tests/comments as validated.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from mpesa_sms import (
    account_exists,
    candidate_accounts_from_text,
    extract_remark_text,
)

# Benin account codes: digits + site (e.g. 0123GBO, 0456 SAM) — same token rule as LS
ACCOUNT_TOKEN_RE = re.compile(
    r"\b(\d{3,4})\s*([A-Za-z]{2,4})\b",
    re.IGNORECASE,
)

# --- Amount: FCFA / XOF / CFA (strip spaces used as thousands separators) ---
_AMT1 = re.compile(
    r"(?:reçu|recu|received|Montant|montant|Amount)\s*[:]?\s*([\d\s.,]+)\s*(?:FCFA|XOF|CFA)\b",
    re.IGNORECASE,
)
_AMT2 = re.compile(
    r"\b([\d\s.,]+)\s*(?:FCFA|XOF|CFA)\b",
    re.IGNORECASE,
)

# --- Phone: Benin MSISDN ---
_PHONE = re.compile(
    r"(?:\+|00)?229\s*(\d{8,10})\b",
    re.IGNORECASE,
)
_PHONE_INLINE = re.compile(r"\b(229\d{8,10})\b")

# Transaction / reference id (avoid matching the word "transaction" as the id)
_TXN_PATTERNS = (
    re.compile(r"ID\s+transaction\s*:\s*([A-Za-z0-9\-]{4,32})", re.IGNORECASE),
    re.compile(r"Transaction\s*ID\s*:\s*([A-Za-z0-9\-]{4,32})", re.IGNORECASE),
    re.compile(r"(?:Réf(?:érence)?|Ref)\s*\.?\s*:\s*([A-Za-z0-9\-]{4,32})", re.IGNORECASE),
    re.compile(r"\bID\s*:\s*([A-Za-z0-9\-]{4,32})", re.IGNORECASE),
)

# French / English free-text fields that may carry account hints
_REMARK_PATTERNS = [
    re.compile(r"Motif\s*[:]?\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"Libellé\s*[:]?\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"Message\s*[:]?\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"Remark\s*[:]?\s*(.+?)(?:\n|$)", re.IGNORECASE | re.DOTALL),
]


def _parse_amount(raw: str) -> Optional[float]:
    s = raw.replace(" ", "").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_remark_bn(content: str) -> str:
    """Collect Motif/Libellé/Message/Remark lines; fallback to M-Pesa-style Remark:."""
    text = (content or "").strip()
    if not text:
        return ""
    for pat in _REMARK_PATTERNS:
        m = pat.search(text)
        if m:
            return " ".join(m.group(1).split()).strip()
    return extract_remark_text(text)


def candidate_accounts_bn(text: str) -> list[str]:
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


def _extract_phone(content: str) -> str:
    compact = re.sub(r"\s+", "", content)
    m = _PHONE.search(content)
    if m:
        return "229" + m.group(1).lstrip("0")
    m2 = _PHONE_INLINE.search(compact)
    if m2:
        return m2.group(1)
    # National 8 digits after de/from/vers (prepend 229)
    m3 = re.search(
        r"(?:de|from|vers|à|a)\s*(\d{8,10})\b",
        content,
        re.IGNORECASE,
    )
    if m3:
        d = m3.group(1).lstrip("0")
        if len(d) >= 8:
            return "229" + d[-8:]
    m4 = re.search(r"\b(229\d{8,10})\b", compact)
    if m4:
        return m4.group(1)
    return ""


def _extract_amount(content: str) -> Optional[float]:
    m = _AMT1.search(content)
    if m:
        a = _parse_amount(m.group(1))
        if a is not None:
            return a
    m = _AMT2.search(content)
    if m:
        return _parse_amount(m.group(1))
    return None


def _extract_txn_id(content: str) -> str:
    for pat in _TXN_PATTERNS:
        m = pat.search(content)
        if m:
            return m.group(1).strip()
    return ""


def parse_momo_bn_sms(content: str) -> Optional[dict[str, Any]]:
    """
    Parse MTN MoMo (Benin) confirmation SMS.

    Returns dict compatible with mpesa_sms.parse_mpesa_sms:
    txn_id, amount, phone, reference, remark_raw, provider.
    """
    if not content or not content.strip():
        return None

    amount = _extract_amount(content)
    if amount is None or amount <= 0:
        return None

    phone = _extract_phone(content)
    if not phone:
        # Some templates only show amount; still record if we can resolve by text later
        phone = ""

    txn_id = _extract_txn_id(content)
    remark = extract_remark_bn(content)

    # Reference: prefer explicit txn line, else numeric ref
    reference = txn_id
    mnum = re.search(r"\b(\d{8,14})\b", content)
    if not reference and mnum:
        reference = mnum.group(1)

    return {
        "txn_id": txn_id,
        "amount": float(amount),
        "phone": phone,
        "reference": reference or "",
        "remark_raw": remark,
        "provider": "momo_bj",
    }


def phone_to_account_bn(conn, phone_digits: str) -> Optional[str]:
    """Look up account by customer phone; normalize Benin 229 MSISDN."""
    normalized = "".join(c for c in phone_digits if c.isdigit())
    if normalized.startswith("229"):
        normalized = normalized[3:]
    normalized = normalized.lstrip("0")

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


def resolve_bn_momo_account(
    conn,
    content: str,
    parsed: dict[str, Any],
) -> Tuple[Optional[str], str, str, str]:
    """
    Resolve credit account: digit+site tokens from remark/full SMS first, else phone.

    Same allocation labels as mpesa_sms.resolve_sms_account.
    """
    remark = (parsed.get("remark_raw") or "").strip() or extract_remark_bn(content)

    candidates: list[str] = []
    if remark:
        candidates.extend(candidate_accounts_bn(remark))
    if not candidates:
        candidates.extend(candidate_accounts_bn(content))

    tried: list[str] = []
    for acct in candidates:
        if acct in tried:
            continue
        tried.append(acct)
        if account_exists(conn, acct):
            return acct, "remark_account", remark[:500], ""

    phone = parsed.get("phone") or ""
    phone_acct = phone_to_account_bn(conn, str(phone)) if phone else None

    if not candidates:
        reason = "no_account_pattern_in_remark_or_sms"
    else:
        reason = f"remark_candidates_not_in_db:{','.join(tried)}"

    if phone_acct:
        return phone_acct, "phone_fallback", remark[:500], reason

    return None, "none", remark[:500], reason
