"""
Lesotho M-Pesa and EcoCash SMS parsing and account resolution (Remark-first, phone fallback).

EcoCash (Econet / short code 199, including MAT/ThunderCloud and Koios-backed sites) often does not match
M-Pesa regexes; ``parse_ls_sms_payment`` tries M-Pesa first, then EcoCash-specific patterns.

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

# Phone group allows spaces / + (EcoCash and operator templates sometimes break \d{8,15}).
MPESA_FALLBACK = re.compile(
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+(?P<phone>[\d+\s\u00a0\-]{10,36})",
    re.IGNORECASE,
)

# --- Lesotho EcoCash (MAT site and others): parallel to PHP read_payment_file.php (sender 199) ---
# Templates vary; short code 199 is the usual gateway sender. Amounts are often Maloti as M#.#.
_ECOCASH_BRAND_M = re.compile(
    r"(?is)EcoCash.*?M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\+?266\d{8,10}|\d{10,15})",
)
_ECOCASH_BRAND_M2 = re.compile(
    r"(?is)M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\+?266\d{8,10}|\d{10,15}).*?EcoCash",
)
# No "Confirmed." line (differs from typical M-Pesa) but still has M-line + phone
_ECOCASH_MLINE_ONLY = re.compile(
    r"(?is)^(?P<txn_id>[\w-]{4,36})\s+.*?M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\+?266\d{8,10}|\d{10,15})",
)
# Econet EcoCash (common on MAT): amount BEFORE "received", payer blob, "for NNNNXXX" account — not M-Pesa shape.
# Example: "You have received M25 from Tiisetso Lebotho-62205631 for 0118mat. Approval Code: MP260416...."
_ECOCASH_YOU_HAVE_RECEIVED_FOR = re.compile(
    r"(?is)You have received M(?P<amount>\d+(?:\.\d{1,2})?)\s+from\s+(?P<from_blob>.+?)\s+for\s+"
    r"(?P<acct>\d{3,4})\s*(?P<site>[A-Za-z]{2,4})",
)
_APPROVAL_CODE = re.compile(r"Approval\s*Code:\s*([A-Za-z0-9_.]+)", re.IGNORECASE)
# Reference / transaction id for dedupe (same helpers as M-Pesa)
_EXT_TXN_ID = re.compile(
    r"(?:Reference|Transaction\s*ID|Txn\s*ID|Ref\.?)\s*[:#]?\s*([A-Za-z0-9\-]{4,36})",
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


def _normalize_ls_payment_phone(raw: str) -> str:
    """Digits-only for SMS payer phone (handles +266, spaces, NBSP in EcoCash templates)."""
    return "".join(c for c in (raw or "") if c.isdigit())


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
            "phone": _normalize_ls_payment_phone(m.group("phone")),
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
            "phone": _normalize_ls_payment_phone(m.group("phone")),
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
            "phone": _normalize_ls_payment_phone(m.group("phone")),
            "reference": ref_match.group(1) if ref_match else "",
            "remark_raw": remark,
            "provider": "mpesa",
        }

    return None


def _ecocash_hint(content: str, sender: str) -> bool:
    """True if SMS is likely Lesotho EcoCash (body or short code 199)."""
    if not (content or "").strip():
        return False
    low = content.lower()
    if "ecocash" in low:
        return True
    # Operator templates often say "Econet" / wallet without the word "EcoCash".
    if "econet" in low and re.search(r"266\s*\d{6,12}", content):
        return True
    s = (sender or "").strip()
    if not s:
        return False
    if s == "199":
        return True
    digits = "".join(c for c in s if c.isdigit())
    return digits.endswith("199") or digits == "199"


def _parse_ecocash_you_have_received_for(content: str) -> Optional[dict[str, Any]]:
    """Parse 'You have received M… from … for …0118mat…' (sender 199 / Econet wallet)."""
    m = _ECOCASH_YOU_HAVE_RECEIVED_FOR.search(content)
    if not m:
        return None
    amount = float(m.group("amount"))
    from_blob = (m.group("from_blob") or "").strip()
    # Payer phone: digits after hyphen (Name-62205631) or last 8–12 digit run in blob
    phone_raw = ""
    hy = re.search(r"-\s*(\d{6,12})\s*$", from_blob)
    if hy:
        phone_raw = hy.group(1)
    if not phone_raw:
        runs = re.findall(r"\d{8,12}", from_blob)
        if runs:
            phone_raw = runs[-1]
    if phone_raw and len(phone_raw) == 8:
        phone_raw = "266" + phone_raw
    elif phone_raw and len(phone_raw) == 9 and phone_raw.startswith("5"):
        phone_raw = "266" + phone_raw
    appr = _APPROVAL_CODE.search(content)
    if appr:
        txn_id = appr.group(1).strip()
    else:
        ext_match = _EXT_TXN_ID.search(content)
        txn_id = ext_match.group(1).strip() if ext_match else ""
    return _ecocash_build_dict(
        amount,
        phone_raw,
        content,
        txn_id=txn_id,
    )


def _ecocash_build_dict(
    amount: float,
    phone: str,
    content: str,
    txn_id: str = "",
) -> dict[str, Any]:
    ref_match = REF_PATTERN.search(content)
    ext_match = _EXT_TXN_ID.search(content)
    remark = extract_remark_text(content)
    ref = ref_match.group(1) if ref_match else ""
    tid = (txn_id or "").strip()
    if not tid and ext_match:
        tid = ext_match.group(1).strip()
    if not tid:
        tid = ref
    return {
        "txn_id": tid,
        "amount": amount,
        "phone": _normalize_ls_payment_phone(phone),
        "reference": ref,
        "remark_raw": remark,
        "provider": "ecocash",
    }


def parse_ecocash_ls_sms(content: str, sender: str = "") -> Optional[dict[str, Any]]:
    """Parse Lesotho EcoCash payment SMS (parallel to PHP CSV sender ``199``).

    Called only when :func:`parse_mpesa_sms` returns None — templates differ from M-Pesa.
    """
    # Distinct template (no M-Pesa "M…received from 266…" line) — try before ecocash hint.
    y = _parse_ecocash_you_have_received_for(content)
    if y:
        return y

    if not _ecocash_hint(content, sender):
        return None

    for rx in (_ECOCASH_BRAND_M, _ECOCASH_BRAND_M2):
        m = rx.search(content)
        if m:
            return _ecocash_build_dict(
                float(m.group("amount")),
                m.group("phone"),
                content,
            )

    m = _ECOCASH_MLINE_ONLY.search(content)
    if m:
        return _ecocash_build_dict(
            float(m.group("amount")),
            m.group("phone"),
            content,
            txn_id=m.group("txn_id"),
        )

    # Short code 199: same M…received line as M-Pesa fallback but SMS may not match mpesa
    # (e.g. missing "Confirmed." so MPESA_LOOSE fails).
    if _ecocash_hint(content, sender):
        m = MPESA_FALLBACK.search(content)
        if m:
            return _ecocash_build_dict(
                float(m.group("amount")),
                m.group("phone"),
                content,
            )

    return None


def parse_ls_sms_payment(content: str, sender: str = "") -> Optional[dict[str, Any]]:
    """Lesotho gateway: M-Pesa first, then EcoCash.

    EcoCash confirmations often share the same ``Confirmed`` / ``M…received`` shape as M-Pesa;
    if the body or sender (short code 199) indicates EcoCash, mark ``provider`` accordingly.
    """
    p = parse_mpesa_sms(content)
    if p:
        if _ecocash_hint(content, sender):
            out = dict(p)
            out["provider"] = "ecocash"
            return out
        return p
    return parse_ecocash_ls_sms(content, sender)


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
