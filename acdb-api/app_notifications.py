"""App notifications: inbox + FCM push mirroring CC's SMS sends.

Phase 3 of the cross-repo app initiative. Every SMS CC sends today
(payment receipt, low balance, welcome/commission) is mirrored into the
`app_notifications` inbox and dispatched via FCM to the customer's
registered device tokens. The aim is to discontinue SMS once the inbox
+ push are validated.

Public helper:
    mirror_to_app(account_number, type, title, body, payload)

JWT-gated endpoints (mounted under ``/api/app``):
    GET    /api/app/notifications
    POST   /api/app/notifications/read
    DELETE /api/app/notifications/{id}
    POST   /api/app/device
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from middleware import get_current_user

logger = logging.getLogger("cc-api.app-notifications")

router = APIRouter(prefix="/api/app", tags=["app-notifications"])

_FCM_SERVICE_ACCOUNT = os.path.join(os.path.dirname(__file__), "firebase-service-account.json")


def _get_connection():
    """Lazy import to avoid a circular import with ``customer_api``."""
    from customer_api import get_connection

    return get_connection()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _ensure_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_notifications (
            id            BIGSERIAL PRIMARY KEY,
            account_number TEXT NOT NULL,
            type          TEXT NOT NULL,
            title         TEXT,
            body          TEXT NOT NULL,
            payload_json  TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            read_at       TIMESTAMPTZ,
            fcm_status    TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_device_tokens (
            id             BIGSERIAL PRIMARY KEY,
            account_number TEXT NOT NULL,
            token          TEXT NOT NULL,
            platform       TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (account_number, token)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS app_notifications_account_idx "
        "ON app_notifications (account_number, created_at DESC)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Auth dependency (customer-only)
# ---------------------------------------------------------------------------


def _customer_user(user) -> Any:
    from models import UserType

    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")
    return user


def _require_customer_dep():
    def _dep(user=Depends(get_current_user)):
        return _customer_user(user)

    return _dep


# ---------------------------------------------------------------------------
# FCM dispatch
# ---------------------------------------------------------------------------


def _device_tokens(conn, account_number: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT token FROM app_device_tokens WHERE account_number = %s",
        (account_number,),
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def _dispatch_fcm(conn, account_number: str, title: str, body: str, data: Dict[str, Any]) -> str:
    """Best-effort FCM send to every registered device token.

    Returns ``sent`` if at least one message was dispatched, ``no_tokens``
    if none registered, or ``error`` on hard failure. Never raises.
    """
    tokens = _device_tokens(conn, account_number)
    if not tokens:
        return "no_tokens"
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(_FCM_SERVICE_ACCOUNT)
            firebase_admin.initialize_app(cred)

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in data.items() if v is not None},
        )
        messaging.send_each_for_multicast(message)
        return "sent"
    except Exception as e:  # noqa: BLE001
        logger.warning("FCM dispatch failed for %s: %s", account_number, e)
        return "error"


# ---------------------------------------------------------------------------
# Public mirror helper (called from SMS send points)
# ---------------------------------------------------------------------------


def mirror_to_app(
    account_number: Optional[str],
    ntype: str,
    title: str,
    body: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Mirror one SMS-equivalent notification into the app inbox + FCM.

    Safe to call from background tasks; never raises. No-ops when the
    account is unknown or the DB is unavailable.
    """
    if not account_number:
        return
    try:
        with _get_connection() as conn:
            _ensure_tables(conn)
            fcm_status = _dispatch_fcm(conn, account_number, title, body, payload or {})
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO app_notifications
                    (account_number, type, title, body, payload_json, fcm_status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    account_number,
                    ntype,
                    title,
                    body,
                    json.dumps(payload) if payload else None,
                    fcm_status,
                ),
            )
            cur.fetchone()
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("mirror_to_app failed for %s: %s", account_number, e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class DeviceRegistration(BaseModel):
    token: str
    platform: Optional[str] = None  # android / ios / web


class MarkReadRequest(BaseModel):
    notification_id: Optional[int] = None
    all: bool = False


@router.post("/device")
def register_device(
    body: DeviceRegistration,
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Register/refresh an FCM device token for the signed-in customer."""
    user = _customer_user(user)
    acct = user.user_id
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    with _get_connection() as conn:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO app_device_tokens (account_number, token, platform)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_number, token)
            DO UPDATE SET platform = EXCLUDED.platform, updated_at = NOW()
            RETURNING id
            """,
            (acct, token, body.platform),
        )
        tid = int(cur.fetchone()[0])
        conn.commit()
    return {"status": "ok", "id": tid}


@router.get("/notifications")
def list_notifications(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Paginated inbox for the signed-in customer (newest first)."""
    user = _customer_user(user)
    acct = user.user_id
    with _get_connection() as conn:
        _ensure_tables(conn)
        cur = conn.cursor()
        where = "account_number = %s"
        params: List[Any] = [acct]
        if unread_only:
            where += " AND read_at IS NULL"
        cur.execute(
            f"SELECT id, type, title, body, payload_json, created_at, read_at, fcm_status "
            f"FROM app_notifications WHERE {where} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        rows = list(cur.fetchall())
        cur.execute(
            f"SELECT count(*) FROM app_notifications WHERE {where}", tuple(params)
        )
        total = int(cur.fetchone()[0] or 0)
        cur.execute(
            f"SELECT count(*) FROM app_notifications WHERE {where} AND read_at IS NULL",
            tuple(params),
        )
        unread = int(cur.fetchone()[0] or 0)

    items = []
    for r in rows:
        created = r[5]
        items.append(
            {
                "id": r[0],
                "type": r[1],
                "title": r[2],
                "body": r[3],
                "payload": json.loads(r[4]) if r[4] else None,
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                "read_at": r[6].isoformat() if r[6] and hasattr(r[6], "isoformat") else (str(r[6]) if r[6] else None),
                "fcm_status": r[7],
            }
        )
    response.headers["Cache-Control"] = "no-store"
    return {"notifications": items, "total": total, "unread": unread, "limit": limit, "offset": offset}


@router.post("/notifications/read")
def mark_notifications_read(
    body: MarkReadRequest,
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Mark a single notification (or all) as read."""
    user = _customer_user(user)
    acct = user.user_id
    with _get_connection() as conn:
        _ensure_tables(conn)
        cur = conn.cursor()
        if body.all:
            cur.execute(
                "UPDATE app_notifications SET read_at = NOW() "
                "WHERE account_number = %s AND read_at IS NULL",
                (acct,),
            )
        elif body.notification_id is not None:
            cur.execute(
                "UPDATE app_notifications SET read_at = NOW() "
                "WHERE id = %s AND account_number = %s AND read_at IS NULL",
                (body.notification_id, acct),
            )
        else:
            raise HTTPException(status_code=400, detail="notification_id or all=true required")
        affected = cur.rowcount
        conn.commit()
    return {"status": "ok", "updated": affected}


@router.delete("/notifications/{notification_id}")
def delete_notification(
    notification_id: int,
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Delete a notification owned by the signed-in customer."""
    user = _customer_user(user)
    acct = user.user_id
    with _get_connection() as conn:
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM app_notifications WHERE id = %s AND account_number = %s",
            (notification_id, acct),
        )
        affected = cur.rowcount
        conn.commit()
    if not affected:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "ok", "deleted": affected}
