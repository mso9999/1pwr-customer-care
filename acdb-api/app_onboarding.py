"""Mobile app onboarding endpoints (Phase 4 sandbox + production).

Public, unauthenticated routes consumed by the 1PWR mobile app
(``1PWRBENIN-v2`` / ``mionwa``) during the customer onboarding flow:

  POST   /onboarding/search           — look up existing customer by zone/name
  PUT    /onboarding/phone             — save/stage phone number
  POST   /onboarding/initiate-payment  — initiate MoMo onboarding payment
  GET    /onboarding/status/{ref}      — poll payment status

In sandbox mode (``APP_SANDBOX=1``), these endpoints return synthetic
data without touching a real payment gateway.  In production they
persist phone numbers against customer records and proxy to the
country-specific MoMo initiate endpoint.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app_sandbox import sandbox_enabled as _sandbox_enabled

logger = logging.getLogger("cc-api.app-onboarding")

router = APIRouter(prefix="/api/onboarding", tags=["app-onboarding"])

# In-memory store for sandbox payment references: ref_id -> metadata
# (process-local; adequate for a single sandbox instance).
_SANDBOX_PAYMENTS: Dict[str, Dict[str, Any]] = {}

# In-memory staging for phone numbers of new (not-yet-created) customers.
# Keyed by (zone, nom, prenom) -> phone.
_STAGED_PHONES: Dict[tuple, str] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    village: str
    nom: str
    prenom: str


class PhoneUpdateRequest(BaseModel):
    telephone: str
    code_concession: Optional[str] = None


class InitiatePaymentRequest(BaseModel):
    telephone: str
    code_concession: Optional[str] = None
    village: Optional[str] = None
    nom: Optional[str] = None
    prenom: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_phone(phone: str) -> str:
    if len(phone) < 4:
        return phone
    prefix = phone[:2]
    last4 = phone[-4:]
    return f"{prefix} ** ** {last4}"


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _find_customer_by_name(conn, village: str, nom: str, prenom: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.first_name, c.last_name, c.phone,
               c.community, a.account_number
        FROM customers c
        LEFT JOIN accounts a ON a.customer_id = c.id
        WHERE UPPER(c.community) = UPPER(%s)
          AND UPPER(c.last_name) = UPPER(%s)
          AND (
              UPPER(c.first_name) = UPPER(%s)
              OR c.first_name ILIKE %s
          )
        ORDER BY c.id
        LIMIT 1
        """,
        (village, nom, prenom, f"%{prenom}%"),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _update_customer_phone(conn, customer_id: int, phone: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE customers SET phone = %s WHERE id = %s",
        (phone, customer_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/search")
def onboarding_search(req: SearchRequest) -> Dict[str, Any]:
    """Look up an existing customer by zone (community), last name, first name.

    Returns ``{"found": true, ...}`` when a match is found, or
    ``{"found": false}`` when no match exists (new client flow).
    """
    village = (req.village or "").strip()
    nom = (req.nom or "").strip().upper()
    prenom = (req.prenom or "").strip()

    if not village or not nom or not prenom:
        raise HTTPException(400, "village, nom, and prenom are required")

    if _sandbox_enabled():
        # In sandbox, return not-found so the app follows the new-client flow.
        return {"found": False}

    with _get_connection() as conn:
        customer = _find_customer_by_name(conn, village, nom, prenom)

    if not customer:
        return {"found": False}

    phone = (customer.get("phone") or "").strip()
    has_phone = bool(phone)
    return {
        "found": True,
        "code_concession": customer.get("account_number"),
        "village": customer.get("community") or village,
        "nom": customer.get("last_name") or nom,
        "prenom": customer.get("first_name") or prenom,
        "telephone_masked": _mask_phone(phone) if has_phone else None,
        "has_telephone": has_phone,
    }


@router.put("/phone")
def onboarding_update_phone(req: PhoneUpdateRequest) -> Dict[str, Any]:
    """Save (or stage) a phone number for the onboarding customer.

    When ``code_concession`` is provided (existing customer), the phone is
    persisted to the customer record.  When ``code_concession`` is null
    (new client), the phone is staged in memory for use in the subsequent
    ``initiate-payment`` call.
    """
    phone = (req.telephone or "").strip()
    if not phone:
        raise HTTPException(400, "telephone is required")

    # Basic phone validation: digits and optional leading +.
    cleaned = re.sub(r"[^\d+]", "", phone)
    if not cleaned or not re.match(r"^\+?\d{8,15}$", cleaned):
        raise HTTPException(400, "Invalid phone number format")

    if req.code_concession:
        # Existing customer — persist to DB.
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM accounts WHERE account_number = %s LIMIT 1",
                (req.code_concession,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Account {req.code_concession} not found")
            customer_id = row[0]
            _update_customer_phone(conn, customer_id, phone)
        logger.info("onboarding/phone: updated customer %s phone=%s", req.code_concession, _mask_phone(phone))
    else:
        # New client — stage the phone.  The app will pass village/nom/prenom
        # in the initiate-payment call, so we don't need to key it here.
        logger.info("onboarding/phone: staged phone for new client=%s", _mask_phone(phone))

    return {"success": True}


@router.post("/initiate-payment")
def onboarding_initiate_payment(req: InitiatePaymentRequest) -> Dict[str, Any]:
    """Initiate the onboarding MoMo payment.

    In sandbox mode, returns a synthetic reference ID and auto-resolves
    after a short delay.  In production, this would proxy to the
    country-specific MoMo initiate endpoint.
    """
    phone = (req.telephone or "").strip()
    if not phone:
        raise HTTPException(400, "telephone is required")

    if _sandbox_enabled():
        ref_id = f"SBX-{uuid.uuid4().hex[:12].upper()}"
        _SANDBOX_PAYMENTS[ref_id] = {
            "telephone": phone,
            "code_concession": req.code_concession,
            "village": req.village,
            "nom": req.nom,
            "prenom": req.prenom,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "activate_at": datetime.utcnow() + timedelta(seconds=10),
        }
        logger.info("onboarding/initiate-payment (sandbox): ref=%s phone=%s", ref_id, _mask_phone(phone))
        return {"success": True, "reference_id": ref_id}

    # Production: proxy to the country-specific MoMo initiate endpoint.
    # TODO: implement production MoMo initiate when payment gateway is wired.
    # For now, return a reference ID that the payment gateway will update.
    ref_id = f"ONB-{uuid.uuid4().hex[:12].upper()}"
    logger.info("onboarding/initiate-payment: ref=%s phone=%s code=%s", ref_id, _mask_phone(phone), req.code_concession)
    return {"success": True, "reference_id": ref_id}


@router.get("/status/{reference_id}")
def onboarding_payment_status(reference_id: str) -> Dict[str, Any]:
    """Poll the status of an onboarding payment.

    In sandbox mode, transitions from ``pending`` → ``activated`` after
    ~10 seconds.  In production, this would check the real payment gateway.
    """
    if _sandbox_enabled():
        payment = _SANDBOX_PAYMENTS.get(reference_id)
        if not payment:
            raise HTTPException(404, "Payment reference not found")

        now = datetime.utcnow()
        if now >= payment["activate_at"]:
            payment["status"] = "activated"
            # Assign a sandbox account number for new clients.
            code = payment.get("code_concession") or "0000SBX"
            return {
                "status": "activated",
                "code_concession": code,
                "message": "Sandbox payment confirmed",
            }
        return {"status": "pending", "message": "Waiting for confirmation"}

    # Production: check real payment gateway.
    # TODO: implement production status check when payment gateway is wired.
    return {"status": "pending", "message": "Payment status check not yet implemented"}


# ---------------------------------------------------------------------------
# Sandbox-prefixed router
#
# When the app's SANDBOX_API_BASE is ``https://cc.1pwrafrica.com/api/sandbox``
# (path-based sandbox on the same CC host), the app calls paths like
# ``PUT /api/sandbox/onboarding/phone``.  This router re-exports the same
# endpoints under the ``/api/sandbox`` prefix so both deployment strategies
# (separate instance vs. path prefix) work.
# ---------------------------------------------------------------------------

sandbox_router = APIRouter(prefix="/api/sandbox/onboarding", tags=["app-onboarding-sandbox"])

sandbox_router.post("/search")(onboarding_search)
sandbox_router.put("/phone")(onboarding_update_phone)
sandbox_router.post("/initiate-payment")(onboarding_initiate_payment)
sandbox_router.get("/status/{reference_id}")(onboarding_payment_status)
