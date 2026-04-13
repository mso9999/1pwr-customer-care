"""
POST /api/customer/messages — ingest care messages from the mobile app or meter relay.

Auth:
  - X-Service-Key: must match env CC_APP_SERVICE_KEY (IoT / BFF relay), OR
  - Authorization: Bearer <JWT> with user_type=customer (optional; not all deployments issue these yet).

Idempotency: header X-Idempotency-Key (stored unique in app_care_messages).

After insert, optionally POST JSON to the country-specific bridge URL with header
X-Bridge-Secret (see ``cc_bridge_notify.notify_cc_bridge`` — Lesotho vs Benin env vars).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from cc_bridge_notify import notify_cc_bridge
from country_config import COUNTRY
from customer_api import get_connection
from middleware import decode_token

logger = logging.getLogger("cc-api.customer_messages")

router = APIRouter(prefix="/api/customer", tags=["customer"])

CC_APP_SERVICE_KEY = os.environ.get("CC_APP_SERVICE_KEY", "")


class CustomerMessageCreate(BaseModel):
    account_number: Optional[str] = None
    text: str = Field(..., min_length=1, max_length=8000)
    category: Optional[str] = None
    source: str = Field(default="app", max_length=32)
    device_id: Optional[str] = None


def _ensure_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_care_messages (
            id              BIGSERIAL PRIMARY KEY,
            account_number  TEXT,
            body_text       TEXT NOT NULL,
            category        TEXT,
            source          TEXT NOT NULL DEFAULT 'app',
            device_id       TEXT,
            idempotency_key TEXT UNIQUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.commit()


@router.post("/messages")
def post_customer_message(
    body: CustomerMessageCreate,
    x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(None),
):
    authed = False
    account_from_token = None

    if CC_APP_SERVICE_KEY and x_service_key == CC_APP_SERVICE_KEY:
        authed = True
    elif authorization and authorization.startswith("Bearer "):
        try:
            payload = decode_token(authorization[7:].strip())
            if payload.get("user_type") != "customer":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Customer JWT required",
                )
            authed = True
            account_from_token = payload.get("sub")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            ) from e

    if not authed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Service-Key or Bearer token required",
        )

    account = body.account_number or account_from_token
    idem = (x_idempotency_key or "").strip() or None

    with get_connection() as conn:
        _ensure_table(conn)
        cur = conn.cursor()
        if idem:
            cur.execute(
                "SELECT id FROM app_care_messages WHERE idempotency_key = %s",
                (idem,),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return {"status": "ok", "duplicate": True, "id": row[0]}

        cur.execute(
            """
            INSERT INTO app_care_messages
                (account_number, body_text, category, source, device_id, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                account,
                body.text,
                body.category,
                body.source,
                body.device_id,
                idem,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()

    notify_cc_bridge(
        {
            "id": new_id,
            "account_number": account,
            "text": body.text,
            "category": body.category,
            "source": body.source,
        },
        country_code=COUNTRY.code,
    )

    return {"status": "ok", "id": new_id, "duplicate": False}
