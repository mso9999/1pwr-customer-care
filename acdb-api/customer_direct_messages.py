"""Customer-to-customer direct messaging (Phase 5).

A signed-in customer can send an in-app message to another registered
customer, with an optional toggle to mirror the message to the
recipient's WhatsApp via the country bridge (``cc_bridge_notify``).

JWT-gated endpoints (mounted under ``/api/app``):
    GET  /api/app/customers/lookup?q=...
    POST /api/app/messages/direct
    GET  /api/app/messages/direct (sent + received threads)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from middleware import get_current_user

logger = logging.getLogger("cc-api.customer-direct-messages")

router = APIRouter(prefix="/api/app", tags=["app-direct-messages"])


def _get_connection():
    from customer_api import get_connection

    return get_connection()


def _customer_user(user) -> Any:
    from models import UserType

    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")
    return user


def _require_customer_dep():
    def _dep(user=Depends(get_current_user)):
        return _customer_user(user)

    return _dep


def _ensure_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS direct_messages (
            id              BIGSERIAL PRIMARY KEY,
            from_customer   TEXT NOT NULL,
            to_customer     TEXT NOT NULL,
            to_phone        TEXT,
            body            TEXT NOT NULL,
            mirror_to_wa    BOOLEAN NOT NULL DEFAULT FALSE,
            delivery_status TEXT NOT NULL DEFAULT 'sent',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS direct_messages_parties_idx "
        "ON direct_messages (from_customer, to_customer, created_at DESC)"
    )
    conn.commit()


def _resolve_recipient(conn, query: str) -> Optional[Dict[str, Any]]:
    """Resolve a recipient by account number (prefixed) or phone digits."""
    q = (query or "").strip()
    if not q:
        return None
    cur = conn.cursor()
    # Account number path (e.g. 0001SAM).
    cur.execute(
        "SELECT a.account_number, c.first_name, c.last_name, "
        "COALESCE(NULLIF(TRIM(c.cell_phone_1),''), NULLIF(TRIM(c.phone),''), "
        "NULLIF(TRIM(c.cell_phone_2),'')) AS phone "
        "FROM accounts a JOIN customers c ON c.id = a.customer_id "
        "WHERE a.account_number = %s LIMIT 1",
        (q.upper(),),
    )
    row = cur.fetchone()
    if not row:
        # Phone path: digits-only match.
        digits = "".join(c for c in q if c.isdigit())
        if len(digits) >= 8:
            cur.execute(
                "SELECT a.account_number, c.first_name, c.last_name, "
                "COALESCE(NULLIF(TRIM(c.cell_phone_1),''), NULLIF(TRIM(c.phone),''), "
                "NULLIF(TRIM(c.cell_phone_2),'')) AS phone "
                "FROM customers c JOIN accounts a ON a.customer_id = c.id "
                "WHERE c.cell_phone_1 LIKE %s OR c.phone LIKE %s OR c.cell_phone_2 LIKE %s "
                "LIMIT 1",
                (f"%{digits}%", f"%{digits}%", f"%{digits}%"),
            )
            row = cur.fetchone()
    if not row:
        return None
    name = " ".join(p for p in [row[1], row[2]] if p).strip() or row[0]
    return {"account_number": row[0], "name": name, "phone": row[3] or ""}


class DirectMessageCreate(BaseModel):
    to_customer: str  # account number or phone
    body: str
    mirror_to_whatsapp: bool = False


@router.get("/customers/lookup")
def lookup_customer(
    q: str = Query(..., min_length=2),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Recipient lookup for direct messaging (by account or phone)."""
    me = _customer_user(user).user_id
    with _get_connection() as conn:
        _ensure_table(conn)
        recipient = _resolve_recipient(conn, q)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if recipient["account_number"].upper() == me.upper():
        raise HTTPException(status_code=400, detail="Cannot message yourself")
    return {"recipient": recipient}


@router.post("/messages/direct")
def send_direct_message(
    body: DirectMessageCreate,
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Send a direct customer-to-customer message, optionally WA-mirrored."""
    from cc_bridge_notify import notify_cc_bridge
    from country_config import COUNTRY

    me = _customer_user(user).user_id
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="body is required")

    with _get_connection() as conn:
        _ensure_table(conn)
        recipient = _resolve_recipient(conn, body.to_customer)
        if not recipient:
            raise HTTPException(status_code=404, detail="Recipient not found")
        to_acct = recipient["account_number"]
        if to_acct.upper() == me.upper():
            raise HTTPException(status_code=400, detail="Cannot message yourself")

        delivery_status = "sent"
        if body.mirror_to_whatsapp and recipient["phone"]:
            try:
                notify_cc_bridge(
                    {
                        "id": 0,
                        "account_number": to_acct,
                        "text": text,
                        "category": "direct_message",
                        "source": "app_direct",
                        "from_customer": me,
                    },
                    country_code=COUNTRY.code,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("direct message WA mirror failed: %s", e)
                delivery_status = "wa_failed"
        elif body.mirror_to_whatsapp and not recipient["phone"]:
            delivery_status = "wa_no_phone"

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO direct_messages
                (from_customer, to_customer, to_phone, body, mirror_to_wa, delivery_status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (me, to_acct, recipient["phone"], text, body.mirror_to_whatsapp, delivery_status),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()

    return {
        "status": "ok",
        "id": new_id,
        "to": recipient,
        "delivery_status": delivery_status,
    }


@router.get("/messages/direct")
def list_direct_messages(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """List direct messages sent by or to the signed-in customer."""
    me = _customer_user(user).user_id
    with _get_connection() as conn:
        _ensure_table(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, from_customer, to_customer, to_phone, body, "
            "mirror_to_wa, delivery_status, created_at "
            "FROM direct_messages WHERE from_customer = %s OR to_customer = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (me, me, limit, offset),
        )
        rows = list(cur.fetchall())
        cur.execute(
            "SELECT count(*) FROM direct_messages "
            "WHERE from_customer = %s OR to_customer = %s",
            (me, me),
        )
        total = int(cur.fetchone()[0] or 0)

    items: List[Dict[str, Any]] = []
    for r in rows:
        created = r[7]
        items.append(
            {
                "id": r[0],
                "from_customer": r[1],
                "to_customer": r[2],
                "to_phone": r[3],
                "body": r[4],
                "mirror_to_wa": bool(r[5]),
                "delivery_status": r[6],
                "direction": "outbound" if r[1] == me else "inbound",
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            }
        )
    response.headers["Cache-Control"] = "no-store"
    return {"messages": items, "total": total, "limit": limit, "offset": offset}
