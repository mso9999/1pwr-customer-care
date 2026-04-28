"""
Relay command channel — CC -> AWS IoT -> 1Meter firmware.

Phase 2 of the 1Meter billing migration test (see
``docs/ops/1meter-billing-migration-protocol.md``) requires CC to actuate the
1Meter relay when ``billing_meter_priority='1m'`` and the customer balance
hits zero, so SparkMeter doesn't have to. This module is the cloud half of
that channel.

Flow::

    CC API (this module)
        -> mqtt publish on  oneMeter/<thingName>/cmd/relay
            payload: {cmd_id, action, reason, requested_at, ttl_seconds}
        <-  ingestion_gate Lambda forwards firmware ack from
            oneMeter/<thingName>/cmd/relay/ack
            to POST /api/meters/relay-ack with {cmd_id, status, relay_after}

State lives in ``relay_commands`` (migration ``016_relay_commands.sql``).
Every request opens a paired ``cc_mutations`` row so audit and command-state
are queryable independently.

**This module is intentionally scoped for safe Phase-1 use:**

* ``POST /api/meters/{thing_name}/relay`` accepts manual commands
  (employee-only, role-gated). Used now to validate the channel end-to-end.
* ``POST /api/meters/relay-ack`` accepts firmware acks (HMAC via
  ``X-IoT-Key`` shared secret, same pattern as ``/api/meters/reading``).
* ``maybe_auto_open_relay()`` is the auto-cutoff hook for ``record_payment``
  / scheduled jobs to call. **No-op unless ``RELAY_AUTO_TRIGGER_ENABLED=1``**
  in the environment. Default off; flips to on at Phase 2 entry.

The firmware subscription, mesh-routing, and ack-publish handlers in
``onepwr-aws-mesh`` plus the ack-forwarding Lambda extension in
``ingestion_gate`` are required to make this useful end-to-end. Both are
documented in the protocol doc and tracked as Phase 2 follow-ups.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from customer_api import get_connection
from middleware import require_employee, require_role
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.relay-control")

router = APIRouter(prefix="/api/meters", tags=["relay-control"])

VALID_ACTIONS = ("open", "close")
DEFAULT_TTL_SECONDS = 300
DEBOUNCE_WINDOW_SECONDS = 600           # 10 min
PAYMENT_GRACE_WINDOW_SECONDS = 300      # 5 min
ONLINE_WINDOW_SECONDS = 30 * 60         # 30 min

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
IOT_RELAY_TOPIC_FMT = os.environ.get(
    "IOT_RELAY_TOPIC_FMT", "oneMeter/{thing}/cmd/relay"
)
IOT_RELAY_ACK_KEY = os.environ.get(
    "IOT_INGEST_KEY", "1pwr-iot-ingest-2026"
)  # shared secret for the ack receiver; same as /api/meters/reading

# Phase 2 entry flag. Default off so balance-zero auto-cutoff is dormant
# during Phase 1. Flip to '1' on the host at Phase 2 entry.
RELAY_AUTO_TRIGGER_ENABLED = os.environ.get(
    "RELAY_AUTO_TRIGGER_ENABLED", "0"
).strip() in ("1", "true", "True", "yes")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RelayRequest(BaseModel):
    action: str = Field(..., description="open | close")
    reason: str = Field(..., min_length=1, max_length=120)
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=30, le=3600)
    note: Optional[str] = Field(default=None, max_length=500)
    force: bool = Field(
        default=False,
        description="Bypass debounce + payment-grace fail-safes. Reserved for "
        "ops manual override; logged in cc_mutations metadata.",
    )


class RelayAck(BaseModel):
    cmd_id: str = Field(..., description="UUID echoed from the original request")
    status: str = Field(..., description="acked | rejected | failed")
    relay_after: Optional[str] = Field(default=None, description="'1' (closed) or '0' (open) after the action")
    error: Optional[str] = Field(default=None, max_length=500)
    extra: Optional[dict] = Field(default=None)


# ---------------------------------------------------------------------------
# IoT publish (boto3)
# ---------------------------------------------------------------------------


def _iot_publish(thing_name: str, payload: dict) -> bool:
    """Best-effort publish to ``oneMeter/<thing>/cmd/relay``.

    Returns True if the boto3 call succeeded. Failure is logged and the row
    is left in ``status='queued'`` so the sweeper / next manual call can
    retry. We never raise; the caller decides how to surface to the user.
    """
    try:
        import boto3  # local import — keeps cold-start light when unused
    except ImportError:
        logger.error("boto3 not installed — cannot publish relay command")
        return False

    topic = IOT_RELAY_TOPIC_FMT.format(thing=thing_name)
    body = json.dumps(payload).encode("utf-8")
    try:
        client = boto3.client("iot-data", region_name=AWS_REGION)
        client.publish(topic=topic, qos=1, payload=body)
        logger.info("relay cmd published topic=%s cmd_id=%s", topic, payload.get("cmd_id"))
        return True
    except Exception as exc:  # noqa: BLE001 - network / IAM / boto failure
        logger.warning("iot publish failed topic=%s cmd_id=%s err=%s", topic, payload.get("cmd_id"), exc)
        return False


# ---------------------------------------------------------------------------
# Fail-safe checks
# ---------------------------------------------------------------------------


def _recent_command_for_thing(cur, thing_name: str, within_seconds: int) -> Optional[dict]:
    cur.execute(
        """
        SELECT cmd_id, action, status, requested_at
        FROM relay_commands
        WHERE thing_name = %s
          AND requested_at >= NOW() - (%s || ' seconds')::interval
          AND status NOT IN ('rejected', 'failed', 'timed_out')
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (thing_name, str(within_seconds)),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "cmd_id": str(row[0]),
        "action": row[1],
        "status": row[2],
        "requested_at": row[3].isoformat() if row[3] else None,
    }


def _account_for_thing(cur, thing_name: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort lookup of (meter_id, account_number) for an IoT Thing.

    Thing names embed the meter id only by convention; the canonical link
    is via ``meters.meter_id``. Repeater / gateway nodes have no account.
    """
    cur.execute(
        """
        SELECT meter_id, account_number
        FROM meters
        WHERE platform = 'prototype'
          AND ('OneMeter' || regexp_replace(meter_id, '^0+', '') = %s
               OR meter_id = %s)
        LIMIT 1
        """,
        (thing_name, thing_name),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return (str(row[0]) if row[0] else None, str(row[1]) if row[1] else None)


def _recent_payment_seconds(cur, account_number: Optional[str]) -> Optional[int]:
    if not account_number:
        return None
    try:
        cur.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(transaction_date))) "
            "FROM transactions WHERE account_number = %s AND is_payment = TRUE",
            (account_number,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        cur.connection.rollback()
    return None


def _device_online(cur, meter_id: Optional[str]) -> bool:
    """True if the prototype_meter_state.last_seen_at is within the online window."""
    if not meter_id:
        return False
    try:
        cur.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen_at)) "
            "FROM prototype_meter_state WHERE meter_id = %s",
            (meter_id,),
        )
        row = cur.fetchone()
        if row and row[0] is not None and row[0] <= ONLINE_WINDOW_SECONDS:
            return True
    except Exception:
        cur.connection.rollback()
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{thing_name}/relay")
def request_relay(
    thing_name: str,
    payload: RelayRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin, CCRole.onm_team)),
):
    """Manually issue a relay command to a 1Meter.

    Employee-only with role gating (superadmin or O&M team). Audited via
    ``cc_mutations`` and tracked in ``relay_commands``.

    Fail-safes (skipped when ``force=true`` and logged):

    * Debounce: refuse if a non-final relay command was published for this
      thing in the last :data:`DEBOUNCE_WINDOW_SECONDS`.
    * Payment grace: refuse opens within :data:`PAYMENT_GRACE_WINDOW_SECONDS`
      of the latest payment for the account.
    * Online check: refuse if the device hasn't reported in
      :data:`ONLINE_WINDOW_SECONDS`.
    """
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400, detail=f"action must be one of {VALID_ACTIONS}"
        )

    cmd_id = str(uuid.uuid4())
    now = _now_utc()

    with get_connection() as conn:
        cur = conn.cursor()
        meter_id, account_number = _account_for_thing(cur, thing_name)

        skipped_safeguards: list[str] = []

        if not payload.force:
            # Debounce
            recent = _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS)
            if recent:
                raise HTTPException(
                    status_code=409,
                    detail=f"debounce: another relay command for {thing_name} "
                    f"was issued at {recent['requested_at']} "
                    f"(action={recent['action']}, status={recent['status']}). "
                    f"Set force=true to override.",
                )
            # Payment grace (open only)
            if payload.action == "open":
                gap = _recent_payment_seconds(cur, account_number)
                if gap is not None and gap < PAYMENT_GRACE_WINDOW_SECONDS:
                    raise HTTPException(
                        status_code=409,
                        detail=f"payment grace: {gap}s since last payment for "
                        f"{account_number}. Set force=true to override.",
                    )
            # Online check
            if not _device_online(cur, meter_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"device offline: {thing_name} ({meter_id}) "
                    f"has not reported within {ONLINE_WINDOW_SECONDS}s. "
                    f"Set force=true to publish anyway.",
                )
        else:
            skipped_safeguards = ["debounce", "payment_grace", "online_check"]

        request_payload = {
            "cmd_id": cmd_id,
            "thing_name": thing_name,
            "meter_id": meter_id,
            "account_number": account_number,
            "action": payload.action,
            "reason": payload.reason,
            "ttl_seconds": payload.ttl_seconds,
            "force": payload.force,
            "note": payload.note,
        }

        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 action, reason, requested_by, ttl_seconds, status, payload)
            VALUES
                (%s::uuid, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb)
            RETURNING id
            """,
            (
                cmd_id,
                thing_name,
                meter_id,
                account_number,
                payload.action,
                payload.reason,
                f"user:{user.user_id}",
                payload.ttl_seconds,
                json.dumps(request_payload),
            ),
        )
        relay_row_id = cur.fetchone()[0]

        mutation_id = try_log_mutation(
            user,
            "create",
            "relay_commands",
            str(relay_row_id),
            new_values=request_payload,
            metadata={
                "kind": "relay_command_request",
                "endpoint": "POST /api/meters/{thing_name}/relay",
                "thing_name": thing_name,
                "skipped_safeguards": skipped_safeguards,
            },
            conn=conn,
        )
        if mutation_id is not None:
            cur.execute(
                "UPDATE relay_commands SET cc_mutation_id = %s WHERE id = %s",
                (mutation_id, relay_row_id),
            )

        conn.commit()

        # Publish to AWS IoT outside the DB transaction (slow + may fail).
        # Firmware needs:
        # - meter_id (Modbus serial number) to route the action to the right meter
        # - requested_at_unix (epoch seconds, int) to check TTL without ISO-8601 parsing on-device
        mqtt_payload = {
            "cmd_id": cmd_id,
            "meter_id": meter_id,
            "action": payload.action,
            "reason": payload.reason,
            "requested_at": now.isoformat(),
            "requested_at_unix": int(now.timestamp()),
            "ttl_seconds": payload.ttl_seconds,
        }
        if _iot_publish(thing_name, mqtt_payload):
            with get_connection() as conn2:
                cur2 = conn2.cursor()
                cur2.execute(
                    "UPDATE relay_commands SET status = 'published', "
                    "published_at = NOW() WHERE id = %s AND status = 'queued'",
                    (relay_row_id,),
                )
                conn2.commit()

        return {
            "cmd_id": cmd_id,
            "thing_name": thing_name,
            "action": payload.action,
            "status": "queued",  # client polls /status if needed
            "ttl_seconds": payload.ttl_seconds,
            "skipped_safeguards": skipped_safeguards,
        }


@router.post("/relay-ack")
def receive_relay_ack(
    ack: RelayAck,
    x_iot_key: Optional[str] = Header(default=None, alias="X-IoT-Key"),
):
    """Receive a firmware ack for a relay command.

    Same auth pattern as ``/api/meters/reading`` (shared-secret header from
    the ingestion_gate Lambda forwarder). Idempotent on ``cmd_id``.
    """
    if x_iot_key != IOT_RELAY_ACK_KEY:
        raise HTTPException(status_code=403, detail="Invalid IoT key")

    if ack.status not in ("acked", "rejected", "failed"):
        raise HTTPException(status_code=400, detail="invalid ack.status")

    new_status_map = {
        "acked": "completed" if ack.relay_after is not None else "acked",
        "rejected": "rejected",
        "failed": "failed",
    }
    new_status = new_status_map[ack.status]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, status FROM relay_commands WHERE cmd_id = %s::uuid LIMIT 1",
            (ack.cmd_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"unknown cmd_id {ack.cmd_id}")
        relay_row_id = int(row[0])
        prev_status = row[1]

        if prev_status in ("completed", "rejected", "failed", "timed_out"):
            return {"status": "noop", "current_status": prev_status}

        cur.execute(
            """
            UPDATE relay_commands
            SET status = %s,
                acked_at = NOW(),
                relay_after = COALESCE(%s, relay_after),
                error = COALESCE(%s, error),
                ack_payload = %s::jsonb
            WHERE id = %s
            """,
            (
                new_status,
                ack.relay_after,
                ack.error,
                json.dumps({"status": ack.status, "relay_after": ack.relay_after, "error": ack.error, "extra": ack.extra}),
                relay_row_id,
            ),
        )
        conn.commit()

    return {
        "cmd_id": ack.cmd_id,
        "status": new_status,
    }


# ---------------------------------------------------------------------------
# Auto-trigger hook (Phase 2 — gated off by default)
# ---------------------------------------------------------------------------


def maybe_auto_open_relay(conn, account_number: str, *, reason: str = "zero_balance") -> Optional[str]:
    """If the account is on 1M-primary and balance has hit zero, queue a
    relay-open command.

    No-op unless ``RELAY_AUTO_TRIGGER_ENABLED=1``. Used by ``record_payment``
    and a scheduled balance sweeper. Returns the new ``cmd_id`` when a command
    was queued, ``None`` otherwise.

    All fail-safes that apply to manual requests apply here too (debounce,
    online check). Payment-grace doesn't apply because zero-balance is
    *after* a payment is processed.
    """
    if not RELAY_AUTO_TRIGGER_ENABLED:
        return None

    try:
        from balance_engine import _resolve_billing_priority, get_balance_kwh

        cur = conn.cursor()
        priority = _resolve_billing_priority(cur, account_number)
        if priority != "1m":
            return None

        balance, _ = get_balance_kwh(conn, account_number)
        if balance > 0:
            return None

        # Find the prototype meter for this account
        cur.execute(
            "SELECT meter_id FROM meters "
            "WHERE account_number = %s AND platform = 'prototype' AND status = 'active' "
            "LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if not row:
            logger.warning("auto_open_relay: no prototype meter for %s", account_number)
            return None
        meter_id = str(row[0])
        thing_name = f"OneMeter{int(meter_id):d}" if meter_id.isdigit() else meter_id

        # Debounce
        if _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS):
            return None
        # Online
        if not _device_online(cur, meter_id):
            return None

        cmd_id = str(uuid.uuid4())
        request_payload = {
            "cmd_id": cmd_id,
            "thing_name": thing_name,
            "meter_id": meter_id,
            "account_number": account_number,
            "action": "open",
            "reason": reason,
            "ttl_seconds": DEFAULT_TTL_SECONDS,
            "force": False,
            "note": "auto-triggered by balance engine",
        }
        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 action, reason, requested_by, ttl_seconds, status, payload)
            VALUES (%s::uuid, %s, %s, %s, 'open', %s, %s, %s, 'queued', %s::jsonb)
            """,
            (
                cmd_id,
                thing_name,
                meter_id,
                account_number,
                reason,
                f"auto:{reason}",
                DEFAULT_TTL_SECONDS,
                json.dumps(request_payload),
            ),
        )
        # The caller commits the parent transaction.

        # Best-effort publish; status update happens in a follow-up tx if it succeeds.
        now_auto = _now_utc()
        if _iot_publish(thing_name, {
            "cmd_id": cmd_id,
            "meter_id": meter_id,
            "action": "open",
            "reason": reason,
            "requested_at": now_auto.isoformat(),
            "requested_at_unix": int(now_auto.timestamp()),
            "ttl_seconds": DEFAULT_TTL_SECONDS,
        }):
            cur.execute(
                "UPDATE relay_commands SET status = 'published', published_at = NOW() "
                "WHERE cmd_id = %s::uuid",
                (cmd_id,),
            )
        return cmd_id
    except Exception as exc:  # noqa: BLE001 - never break the caller
        logger.error("maybe_auto_open_relay failed for %s: %s", account_number, exc)
        return None
