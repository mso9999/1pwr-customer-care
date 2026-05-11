"""
Meter safety override: ops team toggle to force a meter's relay open
regardless of credit balance. For emergency / safety scenarios.

Two states:
  - NULL or not 'off'  = auto (normal billing mode)
  - 'off'              = safety override (relay forced open, auto-trigger suppressed)

Platform routing:
  - SparkMeter (platform IS NULL or 'sparkmeter'): via Koios/ThunderCloud API
  - 1Meter (platform = 'prototype'): via AWS IoT MQTT relay command
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware import require_role
from models import CCRole, CurrentUser
from mutations import log_mutation

logger = logging.getLogger("cc-api.meter-safety-override")

router = APIRouter(prefix="/api/meters", tags=["meter-safety-override"])

VALID_STATES = ("auto", "off")

# 1Meter IoT config (mirrors relay_control.py)
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
IOT_RELAY_TOPIC_FMT = os.environ.get(
    "IOT_RELAY_TOPIC_FMT", "oneMeter/{thing}/cmd/relay"
)
DEFAULT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SafetyOverrideRequest(BaseModel):
    state: str = Field(
        ..., description="'auto' (normal billing) or 'off' (safety override, relay forced open)",
    )
    reason: str = Field(..., min_length=1, max_length=200, description="Reason for the override (audited)")
    note: Optional[str] = Field(default=None, max_length=500, description="Additional notes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _thing_name_for_meter(meter_id: str) -> str:
    """Convert a meter_id to an IoT Thing name following the 1Meter convention."""
    if meter_id.isdigit():
        return f"OneMeter{int(meter_id):d}"
    return meter_id


def _iot_publish(thing_name: str, payload: dict) -> bool:
    """Best-effort publish to oneMeter/<thing>/cmd/relay."""
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed — cannot publish relay command")
        return False

    topic = IOT_RELAY_TOPIC_FMT.format(thing=thing_name)
    body = json.dumps(payload).encode("utf-8")
    try:
        client = boto3.client("iot-data", region_name=AWS_REGION)
        client.publish(topic=topic, qos=1, payload=body)
        logger.info("safety-override: published topic=%s cmd_id=%s", topic, payload.get("cmd_id"))
        return True
    except Exception as exc:
        logger.warning("safety-override: iot publish failed topic=%s err=%s", topic, exc)
        return False


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/{meter_id}/override")
def set_meter_override(
    meter_id: str,
    payload: SafetyOverrideRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin, CCRole.onm_team)),
):
    """Set or clear a safety override on a meter.

    **off**: force the relay open regardless of credit balance.
      For 1Meter, queues a relay-open via AWS IoT. For SparkMeter,
      calls the Koios/ThunderCloud disconnect API.

    **auto**: clear the override and return to normal billing.
      Does NOT send a relay-close — billing engine / customer top-ups
      handle reconnection.
    """
    if payload.state not in VALID_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"state must be one of {VALID_STATES}, got '{payload.state}'",
        )

    desired_override = "off" if payload.state == "off" else None

    with _get_connection() as conn:
        cur = conn.cursor()

        # Fetch meter
        cur.execute(
            "SELECT meter_id, platform, account_number, safety_override, community "
            "FROM meters WHERE meter_id = %s",
            (meter_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Meter '{meter_id}' not found")

        mid = str(row[0] or "").strip()
        platform = str(row[1] or "").strip() if row[1] else ""
        account_number = str(row[2] or "").strip()
        current_override = str(row[3] or "").strip() if row[3] else None
        community = str(row[4] or "").strip()

        # No-op if already in desired state
        if current_override == desired_override:
            state_label = "off" if desired_override else "auto"
            return {
                "status": "noop",
                "meter_id": mid,
                "state": state_label,
                "note": f"Meter already in '{state_label}' state",
            }

        # Snapshot before-state for audit
        before_state = {
            "meter_id": mid,
            "platform": platform,
            "account_number": account_number,
            "safety_override": current_override,
            "community": community,
        }

        # Determine if this is a prototype/1Meter (platform is 'prototype')
        is_prototype = (platform == "prototype")

        relay_result = None

        # Execute relay action when toggling to 'off'
        if payload.state == "off":
            if is_prototype:
                # 1Meter: queue relay-open via existing IoT MQTT
                thing_name = _thing_name_for_meter(mid)
                cmd_id = str(uuid.uuid4())
                now = _now_utc()

                request_payload = {
                    "cmd_id": cmd_id,
                    "thing_name": thing_name,
                    "meter_id": mid,
                    "account_number": account_number,
                    "action": "open",
                    "reason": f"safety_override: {payload.reason}",
                    "ttl_seconds": DEFAULT_TTL_SECONDS,
                    "force": True,
                    "note": payload.note or "Safety override by ops team",
                }

                cur.execute(
                    """
                    INSERT INTO relay_commands
                        (cmd_id, thing_name, meter_id, account_number,
                         command, platform, action, reason, requested_by,
                         ttl_seconds, status, payload)
                    VALUES
                        (%s::uuid, %s, %s, %s,
                         'disconnect', 'prototype', 'open', %s, %s,
                         %s, 'queued', %s::jsonb)
                    RETURNING id
                    """,
                    (
                        cmd_id,
                        thing_name,
                        mid,
                        account_number,
                        f"safety_override: {payload.reason}",
                        f"user:{user.user_id}",
                        DEFAULT_TTL_SECONDS,
                        json.dumps(request_payload),
                    ),
                )
                relay_row_id = cur.fetchone()[0]

                relay_result = {
                    "platform": "prototype",
                    "success": True,
                    "cmd_id": cmd_id,
                    "relay_row_id": relay_row_id,
                }

            else:
                # SparkMeter: call Koios/ThunderCloud disconnect API
                try:
                    from sparkmeter_control import disconnect_sparkmeter
                except ImportError:
                    logger.warning("sparkmeter_control not available")
                    disconnect_sparkmeter = None

                if disconnect_sparkmeter:
                    result = disconnect_sparkmeter(mid, account_number)
                    relay_result = {
                        "platform": result.platform,
                        "success": result.success,
                        "error": result.error,
                    }
                else:
                    relay_result = {
                        "platform": "sparkmeter",
                        "success": False,
                        "error": "sparkmeter_control module not available",
                    }

        # Update meters table
        cur.execute(
            """
            UPDATE meters
            SET safety_override = %s,
                safety_override_by = %s,
                safety_override_at = %s
            WHERE meter_id = %s
            """,
            (desired_override, user.user_id, _now_utc(), mid),
        )

        # Snapshot after-state
        after_state = {
            "meter_id": mid,
            "platform": platform,
            "account_number": account_number,
            "safety_override": desired_override,
            "community": community,
        }

        # Log mutation for audit
        log_mutation(
            user,
            "override",
            "meters",
            mid,
            old_values=before_state,
            new_values=after_state,
            metadata={
                "state": payload.state,
                "reason": payload.reason,
                "note": payload.note,
                "platform": platform,
                "relay_result": relay_result,
            },
            conn=conn,
        )

        conn.commit()

        # For 1Meter toggle to off: publish MQTT after commit (slow, best-effort)
        if payload.state == "off" and is_prototype and relay_result:
            mqtt_payload = {
                "cmd_id": relay_result["cmd_id"],
                "meter_id": mid,
                "action": "open",
                "reason": f"safety_override: {payload.reason}",
                "requested_at": _now_utc().isoformat(),
                "requested_at_unix": int(_now_utc().timestamp()),
                "ttl_seconds": DEFAULT_TTL_SECONDS,
            }
            published = _iot_publish(relay_result.get("thing_name", _thing_name_for_meter(mid)), mqtt_payload)
            if published:
                with _get_connection() as conn2:
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE relay_commands SET status = 'published', "
                        "published_at = NOW() WHERE id = %s AND status = 'queued'",
                        (relay_result["relay_row_id"],),
                    )
                    conn2.commit()
            relay_result["published"] = published

        return {
            "status": "ok",
            "meter_id": mid,
            "state": payload.state,
            "platform": platform,
            "account_number": account_number,
            "relay_action": relay_result,
        }
