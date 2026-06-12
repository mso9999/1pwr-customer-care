"""
Portal API for merchant-export payments parked in merchant_unmatched_payments.

Lets O&M view, link to accounts, or dismiss rows already reconciled manually.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from customer_api import _normalize_phone, get_connection
from merchant_unmatched import (
    _ACCOUNT_RE,
    claim_unmatched_row,
    dismiss_unmatched_row,
)
from middleware import require_employee
from models import CurrentUser

logger = logging.getLogger("cc-api.merchant-unmatched-api")

router = APIRouter(prefix="/api/merchant-unmatched", tags=["merchant-unmatched"])


class ClaimRequest(BaseModel):
    account_number: str = Field(..., min_length=4, max_length=10)


class DismissRequest(BaseModel):
    account_number: Optional[str] = Field(None, max_length=10)


def _phone_matches_for_conn(conn, payer_phone: str) -> list[dict]:
    digits = _normalize_phone(payer_phone or "")
    if len(digits) < 7:
        return []
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.account_number, c.first_name, c.last_name, c.phone, c.cell_phone_1, c.cell_phone_2
        FROM customers c
        JOIN accounts a ON a.customer_id = c.id
        WHERE regexp_replace(COALESCE(c.phone, ''), '[^0-9]', '', 'g') LIKE '%%' || %s
           OR regexp_replace(COALESCE(c.cell_phone_1, ''), '[^0-9]', '', 'g') LIKE '%%' || %s
           OR regexp_replace(COALESCE(c.cell_phone_2, ''), '[^0-9]', '', 'g') LIKE '%%' || %s
        ORDER BY a.account_number
        LIMIT 10
        """,
        (digits, digits, digits),
    )
    matches = []
    for acct, first, last, phone, c1, c2 in cur.fetchall():
        for raw in (phone, c1, c2):
            if raw and _normalize_phone(str(raw)) == digits:
                matches.append({
                    "account_number": acct,
                    "name": " ".join(x for x in (first, last) if x).strip(),
                })
                break
        else:
            matches.append({
                "account_number": acct,
                "name": " ".join(x for x in (first, last) if x).strip(),
            })
    # Deduplicate by account
    seen = set()
    unique = []
    for m in matches:
        if m["account_number"] in seen:
            continue
        seen.add(m["account_number"])
        unique.append(m)
    return unique


def _enrich_row(conn, row: dict) -> dict:
    ref = (row.get("reference_text") or "").upper()
    refs = sorted({m.upper() for m in _ACCOUNT_RE.findall(ref)})
    existing_accounts = []
    if refs:
        cur = conn.cursor()
        cur.execute(
            "SELECT account_number FROM accounts WHERE account_number = ANY(%s)",
            (refs,),
        )
        existing_accounts = [r[0] for r in cur.fetchall()]

    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM transactions
        WHERE lower(payment_reference) LIKE '%%' || lower(trim(%s)) LIMIT 1
        """,
        (row.get("receipt") or "",),
    )
    already_booked = cur.fetchone() is not None

    return {
        **row,
        "reference_accounts": refs,
        "existing_reference_accounts": existing_accounts,
        "phone_matches": _phone_matches_for_conn(conn, row.get("payer_phone") or ""),
        "already_booked": already_booked,
    }


@router.get("")
def list_unmatched(
    status: str = Query("open"),
    category: str = Query("customer"),
    search: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_employee),
):
    clauses: list[str] = []
    params: list = []

    if status == "open":
        clauses.append("resolved_at IS NULL")
    elif status == "resolved":
        clauses.append("resolved_at IS NOT NULL")
    if category in ("customer", "treasury"):
        clauses.append("category = %s")
        params.append(category)

    if search:
        clauses.append(
            "(receipt ILIKE %s OR reference_text ILIKE %s OR payer_phone ILIKE %s)"
        )
        like = f"%{search.strip()}%"
        params.extend([like, like, like])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, receipt, amount, paid_at, reference_text, payer_phone,
                   site_hint, provider, source_file, parked_at, resolved_at,
                   resolved_txn_id, resolved_account, category
            FROM merchant_unmatched_payments
            {where}
            ORDER BY resolved_at NULLS FIRST, paid_at DESC, amount DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute(
            f"SELECT count(*) FROM merchant_unmatched_payments {where}",
            params,
        )
        total = cur.fetchone()[0]

        cur.execute(
            """
            SELECT count(*), COALESCE(sum(amount), 0)
            FROM merchant_unmatched_payments
            WHERE resolved_at IS NULL AND category = 'customer'
            """
        )
        open_count, open_total = cur.fetchone()

        enriched = [_enrich_row(conn, row) for row in rows]

    return {
        "payments": enriched,
        "total": total,
        "open_customer_count": open_count,
        "open_customer_total": float(open_total or 0),
    }


@router.post("/{payment_id}/claim")
def claim_payment(
    payment_id: int,
    body: ClaimRequest,
    user: CurrentUser = Depends(require_employee),
):
    account = body.account_number.strip().upper()
    with get_connection() as conn:
        try:
            result = claim_unmatched_row(conn, payment_id, account)
            conn.commit()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("Claim failed for parked payment %s", payment_id)
            raise HTTPException(500, f"Claim failed: {exc}") from exc
    return result


@router.post("/{payment_id}/dismiss")
def dismiss_payment(
    payment_id: int,
    body: DismissRequest,
    user: CurrentUser = Depends(require_employee),
):
    account = (body.account_number or "").strip().upper() or None
    with get_connection() as conn:
        try:
            result = dismiss_unmatched_row(conn, payment_id, account_number=account)
            conn.commit()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return result


@router.get("/export")
def export_unmatched(
    status: str = Query("open"),
    category: str = Query("customer"),
    user: CurrentUser = Depends(require_employee),
):
    clauses: list[str] = []
    params: list = []
    if status == "open":
        clauses.append("resolved_at IS NULL")
    elif status == "resolved":
        clauses.append("resolved_at IS NOT NULL")
    if category in ("customer", "treasury"):
        clauses.append("category = %s")
        params.append(category)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT receipt, amount, paid_at, reference_text, payer_phone,
                   resolved_at, resolved_account, resolved_txn_id, category
            FROM merchant_unmatched_payments
            {where}
            ORDER BY amount DESC, paid_at
            """,
            params,
        )
        rows = cur.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "receipt", "amount", "paid_at", "reference_text", "phone",
        "resolved_at", "resolved_account", "resolved_txn_id", "category",
    ])
    for row in rows:
        writer.writerow([
            row[0], row[1],
            row[2].isoformat() if row[2] else "",
            row[3], row[4],
            row[5].isoformat() if row[5] else "",
            row[6] or "", row[7] or "", row[8],
        ])

    filename = f"merchant_unmatched_{status}_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
