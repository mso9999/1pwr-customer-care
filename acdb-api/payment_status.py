"""
Manual payment status overrides and proof-of-payment uploads.

Ops team can override a customer's inferred payment status and upload
proof documents (PDF, PNG, JPEG).  The override takes precedence over
transaction-based inference in analytics funnel metrics.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from models import CurrentUser
from middleware import require_employee
from customer_api import get_connection
from country_config import _REGISTRY

logger = logging.getLogger("cc-api.payment_status")

router = APIRouter(prefix="/api/payment-status", tags=["payment_status"])

# ---------------------------------------------------------------------------
# File storage
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PAYMENT_PROOFS_DIR = os.path.join(_THIS_DIR, "payment_proofs")

_ALLOWED_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _proofs_dir(customer_id: int) -> str:
    path = os.path.join(PAYMENT_PROOFS_DIR, str(customer_id))
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Fee threshold helper (same logic as analytics._build_query)
# ---------------------------------------------------------------------------


def _get_fee_threshold(country_code: str) -> float:
    cfg = _REGISTRY.get(country_code.upper().strip())
    if cfg:
        t = cfg.default_connection_fee + cfg.default_readyboard_fee
        if t > 0:
            return t
    return 1.0


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SetOverrideRequest(BaseModel):
    status: str  # "not_paid" | "paid" | "fully_paid"
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{customer_id}/override")
def set_payment_status_override(
    customer_id: int,
    body: SetOverrideRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Set a manual payment status override for a customer."""
    status = body.status.strip().lower()
    if status not in ("not_paid", "paid", "fully_paid"):
        raise HTTPException(400, "status must be not_paid, paid, or fully_paid")

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE customers
               SET payment_status_override = %s,
                   payment_status_override_by = %s,
                   payment_status_override_at = %s
               WHERE id = %s""",
            (status, user.user_id, now, customer_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Customer {customer_id} not found")

    logger.info(
        "payment_status_override set: customer=%d status=%s by=%s",
        customer_id, status, user.user_id,
    )

    # If a note was provided, create a proof-less note entry in payment_proofs
    if body.note and body.note.strip():
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO payment_proofs
                   (customer_id, file_path, file_name, content_type,
                    size_bytes, sha256, uploaded_by, uploaded_at, note)
                   VALUES (%s, '', 'override_note', 'text/plain', 0, '', %s, %s, %s)""",
                (customer_id, user.user_id, now, body.note.strip()),
            )
            conn.commit()

    return {
        "payment_status_override": status,
        "payment_status_override_by": user.user_id,
        "payment_status_override_at": now.isoformat(),
    }


@router.delete("/{customer_id}/override")
def clear_payment_status_override(
    customer_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Clear a manual payment status override, reverting to inferred."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE customers
               SET payment_status_override = NULL,
                   payment_status_override_by = NULL,
                   payment_status_override_at = NULL
               WHERE id = %s""",
            (customer_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Customer {customer_id} not found")

    logger.info(
        "payment_status_override cleared: customer=%d by=%s",
        customer_id, user.user_id,
    )
    return {"payment_status_override": None}


@router.get("/{customer_id}/inferred")
def get_inferred_payment_status(
    customer_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Compute the transaction-inferred payment status for a customer."""
    with get_connection() as conn:
        cur = conn.cursor()

        # Get customer's account numbers and community
        cur.execute(
            """SELECT a.account_number, c.community, c.payment_status_override
               FROM customers c
               LEFT JOIN accounts a ON a.customer_id = c.id
               WHERE c.id = %s
               LIMIT 1""",
            (customer_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Customer {customer_id} not found")

        account_number = row[0]
        community = row[1] or ""
        override = row[2]

        # Resolve country from community
        country_code = ""
        for code, cfg in _REGISTRY.items():
            if community.upper() in cfg.site_abbrev:
                country_code = code
                break

        fee_threshold = _get_fee_threshold(country_code) if country_code else 1.0

        total_paid = 0.0
        if account_number:
            cur.execute(
                """SELECT COALESCE(SUM(t.transaction_amount), 0)
                   FROM transactions t
                   WHERE t.account_number = %s
                     AND t.is_payment = true""",
                (account_number,),
            )
            total_paid = float(cur.fetchone()[0] or 0)

        if total_paid <= 0:
            inferred = "not_paid"
        elif total_paid >= fee_threshold:
            inferred = "fully_paid"
        else:
            inferred = "paid"

        effective = override if override else inferred

    return {
        "inferred_status": inferred,
        "total_paid": round(total_paid, 2),
        "fee_threshold": round(fee_threshold, 2),
        "effective_status": effective,
        "has_override": override is not None,
        "payment_status_override": override,
    }


@router.post("/{customer_id}/proof")
async def upload_payment_proof(
    customer_id: int,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    user: CurrentUser = Depends(require_employee),
):
    """Upload a proof-of-payment document for a customer."""
    if not file.filename:
        raise HTTPException(400, "File is required")

    content_type = (file.content_type or "").lower().strip()
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            415,
            f"Unsupported file type {content_type!r}; allowed: {', '.join(sorted(_ALLOWED_MIME))}",
        )

    body = await file.read()
    size = len(body)
    if size == 0:
        raise HTTPException(400, "File is empty")
    if size > _MAX_BYTES:
        raise HTTPException(413, f"File exceeds {_MAX_BYTES // (1024*1024)} MB limit")

    file_hash = hashlib.sha256(body).hexdigest()

    # Generate a unique filename: {timestamp}_{original_name}
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    disk_name = f"{ts}_{safe_name}"

    dest_dir = _proofs_dir(customer_id)
    dest_path = os.path.join(dest_dir, disk_name)
    with open(dest_path, "wb") as f:
        f.write(body)

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        # Verify customer exists
        cur = conn.cursor()
        cur.execute("SELECT id FROM customers WHERE id = %s", (customer_id,))
        if not cur.fetchone():
            os.unlink(dest_path)
            raise HTTPException(404, f"Customer {customer_id} not found")

        cur.execute(
            """INSERT INTO payment_proofs
               (customer_id, file_path, file_name, content_type,
                size_bytes, sha256, uploaded_by, uploaded_at, note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (customer_id, dest_path, file.filename, content_type,
             size, file_hash, user.user_id, now, note.strip() if note else None),
        )
        proof_id = cur.fetchone()[0]
        conn.commit()

    logger.info(
        "payment_proof uploaded: id=%d customer=%d file=%s by=%s",
        proof_id, customer_id, file.filename, user.user_id,
    )

    return {
        "id": proof_id,
        "customer_id": customer_id,
        "file_name": file.filename,
        "content_type": content_type,
        "size_bytes": size,
        "uploaded_by": user.user_id,
        "uploaded_at": now.isoformat(),
        "note": note.strip() if note else None,
    }


@router.get("/{customer_id}/proofs")
def list_payment_proofs(
    customer_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """List all proof-of-payment uploads for a customer."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, customer_id, file_name, content_type, size_bytes,
                      uploaded_by, uploaded_at, note
               FROM payment_proofs
               WHERE customer_id = %s
               ORDER BY uploaded_at DESC""",
            (customer_id,),
        )
        rows = cur.fetchall()

    proofs: List[dict] = []
    for row in rows:
        proofs.append({
            "id": row[0],
            "customer_id": row[1],
            "file_name": row[2],
            "content_type": row[3],
            "size_bytes": row[4],
            "uploaded_by": row[5],
            "uploaded_at": row[6].isoformat() if row[6] else None,
            "note": row[7],
        })

    return {"proofs": proofs}


@router.get("/{customer_id}/proof/{proof_id}/download")
def download_payment_proof(
    customer_id: int,
    proof_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Download a proof-of-payment file."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT file_path, file_name, content_type
               FROM payment_proofs
               WHERE id = %s AND customer_id = %s""",
            (proof_id, customer_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Proof {proof_id} not found for customer {customer_id}")

        file_path, file_name, content_type = row

    if not os.path.isfile(file_path):
        raise HTTPException(404, "Proof file not found on disk")

    return FileResponse(
        file_path,
        media_type=content_type or "application/octet-stream",
        filename=file_name,
    )
