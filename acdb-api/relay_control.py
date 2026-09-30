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
  / scheduled jobs to call. It reads ``system_config.relay_auto_trigger_enabled``
  on each check (Billing Priority). A missing row falls back to
  ``RELAY_AUTO_TRIGGER_ENABLED`` (default off). ``RELAY_AUTO_TRIGGER_FORCE_OFF=1``
  locks the switch off.

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

from country_config import COUNTRY
from customer_api import get_connection
from middleware import effective_roles, get_current_user, raise_privilege_denied, require_action, require_employee
from models import CCRole, CurrentUser, UserType
from mutations import log_mutation, try_log_mutation

logger = logging.getLogger("cc-api.relay-control")

router = APIRouter(prefix="/api/meters", tags=["relay-control"])

CC_RELAY_GATE = require_action(
    "approve_financial_and_control",
    system="cc",
    action="issue a manual meter relay command",
    required_level="B",
    fallback_roles=(CCRole.superadmin, CCRole.onm_team),
)

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

# Env fallback when system_config has no row. The live switch is
# ``relay_auto_trigger_enabled(conn)`` — staff set it on Billing Priority.
RELAY_AUTO_TRIGGER_ENABLED = os.environ.get(
    "RELAY_AUTO_TRIGGER_ENABLED", "0"
).strip() in ("1", "true", "True", "yes")

RELAY_CONFIG_KEY = "relay_auto_trigger_enabled"
_TRUTHY = ("1", "true", "True", "yes")
# Nexus scopeCountries uses BJ for Benin; this API's country code is BN.
_SCOPE_ALIASES = {"BJ": "BN", "BENIN": "BN", "LESOTHO": "LS", "ZAMBIA": "ZM"}
_RELAY_EDITOR_ROLES = {
    CCRole.superadmin.value,
    CCRole.onm_team.value,
    CCRole.finance_team.value,
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "0").strip() in _TRUTHY


def canonical_scope_country(raw: str) -> str:
    code = (raw or "").strip().upper()
    return _SCOPE_ALIASES.get(code, code)


def relay_auto_trigger_force_off() -> bool:
    """Host brake. The Billing Priority switch cannot override this."""
    return _env_truthy("RELAY_AUTO_TRIGGER_FORCE_OFF")


def _parse_enabled(value: Any) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text in _TRUTHY


_READ_FAILED = object()


def _load_stored_relay_auto_trigger(conn):
    """Stored bool, None when unset, or ``_READ_FAILED``.

    A read error rolls back to a savepoint so the caller's transaction survives,
    and the switch stays off.
    """
    cur = conn.cursor()
    try:
        cur.execute("SAVEPOINT relay_auto_cfg")
        cur.execute(
            "SELECT value FROM system_config WHERE key = %s LIMIT 1",
            (RELAY_CONFIG_KEY,),
        )
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT relay_auto_cfg")
    except Exception:
        logger.exception("relay auto-trigger config read failed")
        try:
            cur.execute("ROLLBACK TO SAVEPOINT relay_auto_cfg")
        except Exception:
            logger.exception("relay auto-trigger savepoint rollback failed")
        return _READ_FAILED
    if not row:
        return None
    return _parse_enabled(row[0])


def read_stored_relay_auto_trigger(conn) -> Optional[bool]:
    """Stored switch, or None when the row is missing or the read failed."""
    loaded = _load_stored_relay_auto_trigger(conn)
    if loaded is _READ_FAILED:
        return None
    return loaded


def relay_auto_trigger_state(conn) -> dict:
    """One read: effective ``enabled``, stored value, and the host lock."""
    force_off = relay_auto_trigger_force_off()
    loaded = _load_stored_relay_auto_trigger(conn)
    failed = loaded is _READ_FAILED
    stored = None if failed else loaded
    if force_off or failed:
        enabled = False
    elif stored is None:
        enabled = _env_truthy("RELAY_AUTO_TRIGGER_ENABLED")
    else:
        enabled = bool(stored)
    return {
        "enabled": enabled,
        "stored": stored,
        "force_off": force_off,
        "read_error": failed,
    }


def relay_auto_trigger_enabled(conn) -> bool:
    """Live country switch. Force-off wins. Missing row uses the env default."""
    return bool(relay_auto_trigger_state(conn)["enabled"])


# Below 1.1.73 gateways freeze on any relay command; 1.1.73 has open/close
# inverted (a reconnect cuts power).
RELAY_MIN_FIRMWARE = os.environ.get("RELAY_MIN_FIRMWARE", "1.1.74").strip()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _version_tuple(version: Optional[str]) -> Optional[tuple[int, ...]]:
    try:
        return tuple(int(part) for part in str(version).strip().lstrip("v").split("."))
    except (TypeError, ValueError):
        return None


def _gateway_firmware(thing_name: str, meter_id: str) -> Optional[str]:
    """FirmwareVersion on the meter's newest ``1meter_data`` reading through ``thing_name``."""
    try:
        import boto3

        resp = boto3.client("dynamodb", region_name=AWS_REGION).query(
            TableName="1meter_data",
            KeyConditionExpression="device_id = :m",
            ExpressionAttributeValues={":m": {"S": str(meter_id)}},
            ProjectionExpression="FirmwareVersion, thingName",
            ScanIndexForward=False,
            Limit=5,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("firmware lookup failed thing=%s meter=%s err=%s", thing_name, meter_id, exc)
        return None
    for item in resp.get("Items") or []:
        if (item.get("thingName") or {}).get("S") == thing_name:
            return ((item.get("FirmwareVersion") or {}).get("S") or "").strip() or None
    return None


def relay_firmware_block(thing_name: str, meter_id: str) -> Optional[str]:
    """Reason a relay command must not be sent to this gateway, or None if it is safe."""
    firmware = _gateway_firmware(thing_name, meter_id)
    have, need = _version_tuple(firmware), _version_tuple(RELAY_MIN_FIRMWARE)
    if have is not None and need is not None and have >= need:
        return None
    return (
        f"{thing_name} runs firmware {firmware or 'unknown'}; relay commands need "
        f"{RELAY_MIN_FIRMWARE} or newer (older firmware freezes or switches the relay the wrong way)."
    )


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
    thing_name: Optional[str] = Field(default=None, max_length=128)
    meter_id: Optional[str] = Field(default=None, max_length=80)


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
        SELECT mp.meter_serial, COALESCE(NULLIF(mp.account_number, ''), m.account_number)
          FROM meter_provisioning mp
          LEFT JOIN meters m
            ON m.meter_id = mp.meter_serial
            OR regexp_replace(m.meter_id, '^0+', '') =
               regexp_replace(mp.meter_serial, '^0+', '')
         WHERE mp.thing_name = %s
           AND NULLIF(mp.meter_serial, '') IS NOT NULL
           AND mp.status = 'commissioned'
           AND NULLIF(mp.account_number, '') IS NOT NULL
         ORDER BY CASE WHEN mp.status = 'commissioned' THEN 0 ELSE 1 END,
                  mp.updated_at DESC
         LIMIT 1
        """,
        (thing_name,),
    )
    row = cur.fetchone()
    if row:
        return (str(row[0]) if row[0] else None, str(row[1]) if row[1] else None)

    # Compatibility fallback for pre-gateway-pool identities.
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


def _thing_for_meter(cur, meter_id: str, account_number: str) -> Optional[str]:
    """Resolve the PCB Thing that is reporting this physical meter.

    Account bindings live on ``meters``. The gateway Thing is shared, so do
    not require ``meter_provisioning.account_number``.
    """
    try:
        from meter_lifecycle import last_seen_thing_for_meter
        live = last_seen_thing_for_meter(meter_id)
        if live:
            return live
    except Exception as exc:  # noqa: BLE001
        logger.warning("last_seen thing lookup failed for meter %s: %s", meter_id, exc)

    cur.execute(
        """
        SELECT thing_name
          FROM meter_provisioning
         WHERE regexp_replace(meter_serial, '^0+', '') =
               regexp_replace(%s, '^0+', '')
         ORDER BY updated_at DESC
         LIMIT 2
        """,
        (meter_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    if len(rows) > 1 and rows[0][0] != rows[1][0]:
        logger.error(
            "ambiguous gateway binding for meter=%s account=%s: %s",
            meter_id, account_number, [row[0] for row in rows],
        )
        return None
    return str(rows[0][0])


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
    user: CurrentUser = Depends(CC_RELAY_GATE),
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
        if not meter_id or not account_number:
            raise HTTPException(
                status_code=409,
                detail=f"{thing_name} is not commissioned to one unambiguous meter and account.",
            )
        blocked = relay_firmware_block(thing_name, meter_id)
        if blocked:
            raise HTTPException(status_code=409, detail=blocked)

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

        command = 'disconnect' if payload.action == 'open' else 'connect'

        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 command, platform, action, reason, requested_by,
                 ttl_seconds, status, payload)
            VALUES
                (%s::uuid, %s, %s, %s,
                 %s, 'prototype', %s, %s, %s,
                 %s, 'queued', %s::jsonb)
            RETURNING id
            """,
            (
                cmd_id,
                thing_name,
                meter_id,
                account_number,
                command,
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
            "SELECT id, status, thing_name, meter_id "
            "FROM relay_commands WHERE cmd_id = %s::uuid LIMIT 1",
            (ack.cmd_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"unknown cmd_id {ack.cmd_id}")
        relay_row_id = int(row[0])
        prev_status = row[1]
        expected_thing = str(row[2] or "")
        expected_meter = str(row[3] or "")

        if ack.thing_name and ack.thing_name != expected_thing:
            raise HTTPException(status_code=409, detail="ack Thing does not match command target")
        if ack.meter_id and (ack.meter_id.lstrip("0") or ack.meter_id) != (
            expected_meter.lstrip("0") or expected_meter
        ):
            raise HTTPException(status_code=409, detail="ack meter does not match command target")

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


def queue_validation_relay(
    *,
    thing_name: str,
    meter_id: str,
    action: str,
    reason: str,
    requested_by: str,
    follow_up_cmd_id: Optional[str] = None,
) -> str:
    """Queue a relay command for an isolated batch-validation session.

    Authorization and test-Thing allowlisting are owned by
    ``onemeter_validation``. This helper deliberately uses the same MQTT
    payload, relay_commands state, firmware path, and acknowledgement path as
    production control without requiring a real customer financial account.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid relay action {action}")
    blocked = relay_firmware_block(thing_name, meter_id)
    if blocked:
        raise RuntimeError(blocked)
    cmd_id = str(uuid.uuid4())
    now = _now_utc()
    request_payload = {
        "cmd_id": cmd_id,
        "thing_name": thing_name,
        "meter_id": meter_id,
        "account_number": None,
        "action": action,
        "reason": reason,
        "ttl_seconds": DEFAULT_TTL_SECONDS,
        "validation": True,
    }
    with get_connection() as conn:
        cur = conn.cursor()
        recent = _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS)
        if recent and recent.get("status") in ("queued", "published", "acked"):
            same_open = (
                follow_up_cmd_id
                and str(recent.get("cmd_id")) == str(follow_up_cmd_id)
                and action == "close"
            )
            if not same_open:
                raise RuntimeError("another relay command for this test gateway is still within the debounce window")
        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 command, platform, action, reason, requested_by,
                 ttl_seconds, status, payload)
            VALUES (%s::uuid, %s, %s, NULL,
                    %s, 'prototype', %s, %s, %s,
                    %s, 'queued', %s::jsonb)
            """,
            (
                cmd_id,
                thing_name,
                meter_id,
                "disconnect" if action == "open" else "connect",
                action,
                reason,
                requested_by,
                DEFAULT_TTL_SECONDS,
                json.dumps(request_payload),
            ),
        )
        if _iot_publish(thing_name, {
            "cmd_id": cmd_id,
            "meter_id": meter_id,
            "action": action,
            "reason": reason,
            "requested_at": now.isoformat(),
            "requested_at_unix": int(now.timestamp()),
            "ttl_seconds": DEFAULT_TTL_SECONDS,
        }):
            cur.execute(
                "UPDATE relay_commands SET status = 'published', published_at = NOW() "
                "WHERE cmd_id = %s::uuid",
                (cmd_id,),
            )
        conn.commit()
    return cmd_id


# ---------------------------------------------------------------------------
# Auto-trigger hook (Phase 2 — gated off by default)
# ---------------------------------------------------------------------------


def maybe_auto_open_relay(conn, account_number: str, *, reason: str = "zero_balance") -> Optional[str]:
    """If the account is on 1M-primary and balance has hit zero, queue a
    relay-open command.

    No-op unless the country auto-cutoff switch is on. Used by ``record_payment``
    and a scheduled balance sweeper. Returns the new ``cmd_id`` when a command
    was queued, ``None`` otherwise.

    All fail-safes that apply to manual requests apply here too (debounce,
    online check). Payment-grace doesn't apply because zero-balance is
    *after* a payment is processed.
    """
    try:
        if not relay_auto_trigger_enabled(conn):
            return None

        from site_billing_hold import account_effectively_held
        if account_effectively_held(conn, account_number):
            return None

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
            "ORDER BY CASE WHEN role = 'primary' THEN 0 ELSE 1 END LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if not row:
            logger.warning("auto_open_relay: no prototype meter for %s", account_number)
            return None
        meter_id = str(row[0])

        # Safety override check — if the meter is forced off, skip auto-open
        cur.execute(
            "SELECT safety_override FROM meters WHERE meter_id = %s",
            (meter_id,),
        )
        ov_row = cur.fetchone()
        if ov_row and ov_row[0] == 'off':
            logger.info(
                "auto_open_relay: skipping %s (meter %s is in safety override mode)",
                account_number, meter_id,
            )
            return None

        thing_name = _thing_for_meter(cur, meter_id, account_number)
        if not thing_name:
            logger.warning(
                "auto_open_relay: no unambiguous provisioned gateway for meter %s account %s",
                meter_id, account_number,
            )
            return None

        # Debounce
        if _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS):
            return None
        # Online
        if not _device_online(cur, meter_id):
            return None
        blocked = relay_firmware_block(thing_name, meter_id)
        if blocked:
            logger.warning("auto_open_relay: skipping %s: %s", account_number, blocked)
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
                 command, platform, action, reason, requested_by,
                 ttl_seconds, status, payload)
            VALUES (%s::uuid, %s, %s, %s,
                    'disconnect', 'prototype', 'open', %s, %s,
                    %s, 'queued', %s::jsonb)
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


def maybe_auto_close_relay(
    conn,
    account_number: str,
    *,
    reason: str = "positive_balance_after_payment",
) -> Optional[str]:
    """Reconnect a 1Meter-primary account after payment restores credit.

    Uses the same production safety flag, canonical commissioned gateway
    binding, online check, command ledger, MQTT payload, and firmware
    acknowledgement as zero-balance cutoff.
    """
    try:
        if not relay_auto_trigger_enabled(conn):
            return None

        from balance_engine import _resolve_billing_priority, get_balance_kwh

        cur = conn.cursor()
        if _resolve_billing_priority(cur, account_number) != "1m":
            return None
        balance, _ = get_balance_kwh(conn, account_number)
        if balance <= 0:
            return None
        cur.execute(
            "SELECT meter_id, safety_override FROM meters "
            "WHERE account_number = %s AND platform = 'prototype' AND status = 'active' "
            "ORDER BY CASE WHEN role = 'primary' THEN 0 ELSE 1 END LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if not row or row[1] == "off":
            return None
        meter_id = str(row[0])
        thing_name = _thing_for_meter(cur, meter_id, account_number)
        if not thing_name or not _device_online(cur, meter_id):
            return None
        recent = _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS)
        if recent and recent.get("status") in ("queued", "published", "acked"):
            return None

        cmd_id = str(uuid.uuid4())
        request_payload = {
            "cmd_id": cmd_id,
            "thing_name": thing_name,
            "meter_id": meter_id,
            "account_number": account_number,
            "action": "close",
            "reason": reason,
            "ttl_seconds": DEFAULT_TTL_SECONDS,
            "force": False,
            "note": "auto-triggered by restored positive balance",
        }
        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 command, platform, action, reason, requested_by,
                 ttl_seconds, status, payload)
            VALUES (%s::uuid, %s, %s, %s,
                    'connect', 'prototype', 'close', %s, %s,
                    %s, 'queued', %s::jsonb)
            """,
            (
                cmd_id, thing_name, meter_id, account_number, reason,
                f"auto:{reason}", DEFAULT_TTL_SECONDS, json.dumps(request_payload),
            ),
        )
        now_auto = _now_utc()
        if _iot_publish(thing_name, {
            "cmd_id": cmd_id,
            "meter_id": meter_id,
            "action": "close",
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
    except Exception as exc:  # noqa: BLE001
        logger.error("maybe_auto_close_relay failed for %s: %s", account_number, exc)
        return None


def maybe_hold_close_relay(
    conn,
    account_number: str,
    *,
    reason: str = "site_billing_hold",
) -> Optional[str]:
    """Close the relay while a site hold is supplying free power.

    Skipped when this account has a meter set to real billing, and when a
    safety override has cut power. Does not require the cutoff switch.
    """
    try:
        from site_billing_hold import account_effectively_held
        if not account_effectively_held(conn, account_number):
            return None
        cur = conn.cursor()
        cur.execute(
            """
            SELECT meter_id, safety_override FROM meters
             WHERE account_number = %s AND platform = 'prototype' AND status = 'active'
             ORDER BY CASE WHEN role = 'primary' THEN 0 ELSE 1 END
             LIMIT 1
            """,
            (account_number,),
        )
        row = cur.fetchone()
        if not row or row[1] == "off":
            return None
        meter_id = str(row[0])
        thing_name = _thing_for_meter(cur, meter_id, account_number)
        if not thing_name or not _device_online(cur, meter_id):
            return None
        if relay_firmware_block(thing_name, meter_id):
            return None
        recent = _recent_command_for_thing(cur, thing_name, DEBOUNCE_WINDOW_SECONDS)
        if recent and recent.get("action") == "close" and recent.get("status") in (
            "queued", "published", "acked",
        ):
            return None
        cmd_id = str(uuid.uuid4())
        request_payload = {
            "cmd_id": cmd_id,
            "thing_name": thing_name,
            "meter_id": meter_id,
            "account_number": account_number,
            "action": "close",
            "reason": reason,
            "ttl_seconds": DEFAULT_TTL_SECONDS,
            "force": False,
            "note": "site electricity hold keeps power on",
        }
        cur.execute(
            """
            INSERT INTO relay_commands
                (cmd_id, thing_name, meter_id, account_number,
                 command, platform, action, reason, requested_by,
                 ttl_seconds, status, payload)
            VALUES (%s::uuid, %s, %s, %s,
                    'connect', 'prototype', 'close', %s, %s,
                    %s, 'queued', %s::jsonb)
            """,
            (
                cmd_id, thing_name, meter_id, account_number, reason,
                f"auto:{reason}", DEFAULT_TTL_SECONDS, json.dumps(request_payload),
            ),
        )
        now_auto = _now_utc()
        if _iot_publish(thing_name, {
            "cmd_id": cmd_id,
            "meter_id": meter_id,
            "action": "close",
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
    except Exception as exc:  # noqa: BLE001
        logger.error("maybe_hold_close_relay failed for %s: %s", account_number, exc)
        return None


# ---------------------------------------------------------------------------
# Country switch — Billing Priority reads and saves this
# ---------------------------------------------------------------------------


def user_may_edit_relay_auto_trigger(user: CurrentUser) -> bool:
    """Superadmin: any lane. O&M and finance: only a scope that includes this lane.

    An empty scope is global for superadmin only. BJ and BN both mean Benin.
    """
    if user.user_type != UserType.employee:
        return False
    roles = set(effective_roles(user))
    if CCRole.superadmin.value in roles:
        return True
    if not roles.intersection({CCRole.onm_team.value, CCRole.finance_team.value}):
        return False
    scoped = {canonical_scope_country(code) for code in (user.scope_countries or [])}
    return COUNTRY.code.upper() in scoped


def _require_relay_editor(user: CurrentUser) -> None:
    roles = set(effective_roles(user))
    if not roles.intersection(_RELAY_EDITOR_ROLES):
        raise_privilege_denied(
            user, _RELAY_EDITOR_ROLES, "change automatic power cutoff",
        )
    if not user_may_edit_relay_auto_trigger(user):
        raise HTTPException(
            status_code=403,
            detail="You can change automatic power cutoff only for your own country.",
        )


def _switch_payload(state: dict, user: CurrentUser, status: Optional[str] = None) -> dict:
    payload = {
        "country": COUNTRY.code,
        "enabled": bool(state["enabled"]),
        "stored": state["stored"],
        "force_off": bool(state["force_off"]),
        "can_edit": user_may_edit_relay_auto_trigger(user) and not state["force_off"],
    }
    if status:
        payload["status"] = status
    return payload


class RelayAutoTriggerBody(BaseModel):
    enabled: bool


settings_router = APIRouter(prefix="/api/relay-auto-trigger", tags=["relay-control"])


@settings_router.get("")
def get_relay_auto_trigger(user: CurrentUser = Depends(get_current_user)):
    """Read-only for any signed-in user, including a customer on My Dashboard."""
    with get_connection() as conn:
        try:
            return _switch_payload(relay_auto_trigger_state(conn), user)
        finally:
            conn.rollback()


@settings_router.put("")
def put_relay_auto_trigger(
    body: RelayAutoTriggerBody,
    user: CurrentUser = Depends(require_employee),
):
    """Save the country switch and write one mutation row in the same transaction."""
    _require_relay_editor(user)
    if relay_auto_trigger_force_off():
        raise HTTPException(
            status_code=409,
            detail="Automatic power cutoff is locked off on this server.",
        )
    with get_connection() as conn:
        committed = False
        try:
            state = relay_auto_trigger_state(conn)
            if state["read_error"]:
                raise HTTPException(
                    status_code=500,
                    detail="Could not read the current automatic power cutoff setting.",
                )
            current = (
                state["stored"]
                if state["stored"] is not None
                else _env_truthy("RELAY_AUTO_TRIGGER_ENABLED")
            )
            if bool(current) == body.enabled:
                return _switch_payload(state, user, "noop")

            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO system_config (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (RELAY_CONFIG_KEY, "1" if body.enabled else "0"),
            )
            log_mutation(
                user,
                "update",
                "system_config",
                RELAY_CONFIG_KEY,
                old_values={"enabled": bool(current), "country": COUNTRY.code},
                new_values={"enabled": body.enabled, "country": COUNTRY.code},
                metadata={
                    "kind": "relay_auto_trigger",
                    "endpoint": "PUT /api/relay-auto-trigger",
                },
                conn=conn,
            )
            conn.commit()
            committed = True
            return {
                "status": "ok",
                "country": COUNTRY.code,
                "enabled": body.enabled,
                "stored": body.enabled,
                "force_off": False,
                "can_edit": True,
            }
        except HTTPException:
            if not committed:
                conn.rollback()
            raise
        except Exception as exc:
            if not committed:
                conn.rollback()
            logger.exception("relay auto-trigger save failed")
            raise HTTPException(
                status_code=500,
                detail="Failed to save automatic power cutoff.",
            ) from exc
        finally:
            if not committed:
                try:
                    conn.rollback()
                except Exception:
                    logger.exception("relay auto-trigger rollback failed")
