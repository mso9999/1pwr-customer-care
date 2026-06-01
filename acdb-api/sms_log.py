"""
Admin endpoint for browsing the outbound SMS log (``sms_outbound_log`` table)
and receiving CM.com delivery-status callbacks from the SMS gateway hosts.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from models import CCRole, CurrentUser
from middleware import require_role
from customer_api import get_connection

logger = logging.getLogger("cc-api.sms-log")

router = APIRouter(tags=["sms-log"])

# Shared secret for the SMS hosts to report delivery status.
# Uses the same SMS_GATEWAY_KEY that secures the balance gateway.
SMS_GATEWAY_KEY = os.environ.get("SMS_GATEWAY_KEY", "")


def _row_to_dict(cur, row) -> Dict[str, Any]:
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Delivery-status callback (called by send.php on the SMS hosts)
# ---------------------------------------------------------------------------

class DeliveryStatusRequest(BaseModel):
    phone: str
    status: str           # "Accepted", "Rejected", "Delivered", etc.
    error_code: int = 0
    message_error_code: int = 0
    details: str = ""
    sent_at: str = ""     # ISO-8601 from the SMS host


@router.post("/api/sms/delivery-status", status_code=200)
def record_delivery_status(
    body: DeliveryStatusRequest,
    x_gateway_key: str = Header("", alias="X-Gateway-Key"),
) -> Dict[str, Any]:
    """Called by send.php on SMS hosts after CM.com responds.

    Matches the most recent sms_outbound_log row by phone_normalized
    within a ±5-minute window and records the CM.com delivery status.
    """
    if SMS_GATEWAY_KEY and x_gateway_key and x_gateway_key != SMS_GATEWAY_KEY:
        raise HTTPException(status_code=401, detail="invalid gateway key")
    if SMS_GATEWAY_KEY and not x_gateway_key:
        logger.warning(
            "delivery-status callback without X-Gateway-Key accepted in legacy mode"
        )

    phone = "".join(c for c in body.phone if c.isdigit())
    if phone.startswith("00"):
        phone = phone[2:]
    suffix9 = phone[-9:] if phone else ""
    suffix8 = phone[-8:] if phone else ""

    with get_connection() as conn:
        cur = conn.cursor()
        # Find the most recent row for this phone within a 10-min window
        cur.execute(
            """SELECT id FROM sms_outbound_log
               WHERE (
                   phone_normalized = %s
                   OR phone_normalized LIKE %s
                   OR phone_normalized LIKE %s
               )
                 AND sent_at >= NOW() - INTERVAL '10 minutes'
               ORDER BY sent_at DESC
               LIMIT 1""",
            (phone, f"%{suffix9}", f"%{suffix8}"),
        )
        row = cur.fetchone()
        if not row:
            logger.warning(
                "delivery-status: no recent sms_outbound_log row for phone=%s suffix9=%s suffix8=%s",
                phone,
                suffix9,
                suffix8,
            )
            return {"matched": False, "reason": "no recent log row for phone"}

        log_id = row[0]
        cur.execute(
            """UPDATE sms_outbound_log
               SET cm_status = %s,
                   cm_status_at = %s,
                   cm_error_code = %s
               WHERE id = %s""",
            (body.status, datetime.now(timezone.utc), body.error_code, log_id),
        )
        conn.commit()
        logger.info("delivery-status: id=%s status=%s", log_id, body.status)

    return {"matched": True, "id": log_id, "cm_status": body.status}


# ---------------------------------------------------------------------------
# Admin browse endpoint
# ---------------------------------------------------------------------------


@router.get("/api/admin/sms-log")
def list_sms_log(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    sms_type: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    phone: Optional[str] = Query(None, description="Partial match on phone_normalized"),
    account_number: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO-8601 start"),
    date_to: Optional[str] = Query(None, description="ISO-8601 end"),
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
) -> Dict[str, Any]:
    """Paginated, filterable list of outbound SMS records (superadmin only)."""
    where: List[str] = []
    params: List[Any] = []

    if sms_type:
        where.append("sms_type = %s")
        params.append(sms_type)
    if success is not None:
        where.append("success = %s")
        params.append(success)
    if phone:
        where.append("phone_normalized LIKE %s")
        params.append(f"%{phone}%")
    if account_number:
        where.append("account_number = %s")
        params.append(account_number)
    if date_from:
        where.append("sent_at >= %s::timestamptz")
        params.append(date_from)
    if date_to:
        where.append("sent_at <= %s::timestamptz")
        params.append(date_to)

    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(
            f"SELECT COUNT(*) FROM sms_outbound_log{clause}",
            params,
        )
        total = cur.fetchone()[0]

        offset = (page - 1) * per_page
        cur.execute(
            f"SELECT * FROM sms_outbound_log{clause} "
            f"ORDER BY sent_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]

    # Convert datetime objects to ISO strings
    for r in rows:
        if r.get("sent_at"):
            r["sent_at"] = r["sent_at"].isoformat()

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }
