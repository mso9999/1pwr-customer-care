"""Isolated physical batch validation for 1Meter gateways and meter strings.

The synthetic balance and dummy customer live only in the validation tables;
they never enter customer accounts, transactions, revenue, or consumption
reporting. Relay commands use the production MQTT/firmware/ack path.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from country_config import COUNTRY
from middleware import require_action
from models import CCRole, CurrentUser
from relay_control import queue_validation_relay

router = APIRouter(prefix="/api/provisioning/validation", tags=["1meter-validation"])

VALIDATION_THINGS = {
    value.strip()
    for value in os.environ.get(
        "ONEMETER_VALIDATION_THINGS",
        os.environ.get("ONEMETER_OTA_CANARY_THINGS", ""),
    ).split(",")
    if value.strip()
}
METER_LAST_SEEN_TABLE = os.environ.get("METER_LAST_SEEN_TABLE", "meter_last_seen")
ROLES = (CCRole.superadmin, CCRole.onm_team, CCRole.engineering)
CC_VALIDATION_GATE = require_action(
    "operate_customer_care",
    system="cc",
    action="run the isolated meter and gateway validation workflow",
    required_level="C",
    fallback_roles=ROLES,
)


def _get_connection():
    from customer_api import get_connection

    return get_connection()


def _ddb():
    import boto3

    return boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def _number(value: object) -> float:
    match = re.match(r"[-+]?\d*\.?\d+", str(value or "").strip())
    return float(match.group()) if match else 0.0


def _read_meter_state(meter_id: str) -> dict[str, Any]:
    response = _ddb().get_item(
        TableName=METER_LAST_SEEN_TABLE,
        Key={"meterId": {"S": meter_id}},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=409, detail=f"No live telemetry exists for meter {meter_id}.")

    def value(name: str) -> Optional[str]:
        raw = item.get(name) or {}
        return str(raw.get("S") or raw.get("N") or "") or None

    state = {
        "meter_id": meter_id,
        "thing_name": value("thingName"),
        "energy_kwh": _number(value("EnergyActive")),
        "relay": value("Relay"),
        "last_seen": value("lastAcceptedTime") or value("last_seen"),
    }
    try:
        last_seen = datetime.strptime(
            str(state["last_seen"]), "%Y%m%d%H%M"
        ).replace(tzinfo=ZoneInfo(COUNTRY.timezone)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail=f"Meter {meter_id} telemetry has no valid last-seen timestamp.",
        )
    age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if age_seconds < -300 or age_seconds > 30 * 60:
        raise HTTPException(
            status_code=409,
            detail=f"Meter {meter_id} telemetry is stale; wait for a new reading before validation.",
        )
    return state


def _relay_command(cur, cmd_id: object) -> Optional[dict[str, Any]]:
    if not cmd_id:
        return None
    cur.execute(
        """
        SELECT cmd_id, thing_name, meter_id, action, status, relay_after,
               requested_at, published_at, acked_at, error
          FROM relay_commands
         WHERE cmd_id = %s::uuid
        """,
        (str(cmd_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    keys = [
        "cmd_id", "thing_name", "meter_id", "action", "status", "relay_after",
        "requested_at", "published_at", "acked_at", "error",
    ]
    data = dict(zip(keys, row))
    for key, val in data.items():
        if val is not None and not isinstance(val, (str, int, float, bool)):
            data[key] = str(val)
    return data


def _session(cur, session_id: str, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    cur.execute(
        """
        SELECT id, batch_reference, thing_name, meter_id, site_code,
               dummy_customer_label, status, simulated_balance_kwh,
               baseline_energy_kwh, latest_energy_kwh, load_delta_kwh,
               disconnect_cmd_id, reconnect_cmd_id, created_by, created_at,
               updated_at, completed_at, notes
          FROM onemeter_validation_sessions
         WHERE id = %s::uuid
        """ + suffix,
        (session_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Validation session not found.")
    keys = [
        "id", "batch_reference", "thing_name", "meter_id", "site_code",
        "dummy_customer_label", "status", "simulated_balance_kwh",
        "baseline_energy_kwh", "latest_energy_kwh", "load_delta_kwh",
        "disconnect_cmd_id", "reconnect_cmd_id", "created_by", "created_at",
        "updated_at", "completed_at", "notes",
    ]
    data = dict(zip(keys, row))
    for key, val in data.items():
        if val is None:
            continue
        if key in {"simulated_balance_kwh", "baseline_energy_kwh", "latest_energy_kwh", "load_delta_kwh"}:
            data[key] = float(val)
        elif not isinstance(val, (str, int, float, bool)):
            data[key] = str(val)
    return data


def _event(cur, session_id: str, event_type: str, user_id: object, **values: Any) -> None:
    cur.execute(
        """
        INSERT INTO onemeter_validation_events
            (session_id, event_type, amount_kwh, energy_kwh, cmd_id, details, created_by)
        VALUES (%s::uuid, %s, %s, %s, %s::uuid, %s::jsonb, %s)
        """,
        (
            session_id,
            event_type,
            values.get("amount_kwh"),
            values.get("energy_kwh"),
            values.get("cmd_id"),
            json.dumps(values.get("details") or {}),
            str(user_id),
        ),
    )


class StartValidation(BaseModel):
    thing_name: str
    batch_reference: str = Field(..., min_length=1, max_length=120)
    dummy_customer_label: str = Field(default="1Meter batch test customer", max_length=120)
    starting_credit_kwh: float = Field(default=0.01, gt=0, le=1)
    notes: Optional[str] = Field(default=None, max_length=500)


class ValidationPayment(BaseModel):
    kwh: float = Field(default=0.05, gt=0, le=5)


@router.post("/sessions")
def start_validation(
    body: StartValidation,
    user: CurrentUser = Depends(CC_VALIDATION_GATE),
):
    thing = body.thing_name.strip()

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT meter_serial, site, ota_status, fw_version,
                   ota_target_version, is_test
              FROM meter_provisioning
             WHERE thing_name = %s
            """,
            (thing,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Provisioned test gateway not found.")
        meter_id, site, ota_status, fw_version, ota_target, is_test = row
        if thing not in VALIDATION_THINGS and not is_test:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This gateway is not authorized for physical validation. "
                    "Allocate the first canary through CC so it is recorded as a test unit."
                ),
            )
        if not meter_id:
            raise HTTPException(status_code=409, detail="The test gateway has not discovered a meter.")
        if str(ota_status or "").upper() != "SUCCEEDED":
            raise HTTPException(status_code=409, detail="Full-firmware OTA must succeed before batch validation.")
        if not ota_target or str(fw_version or "").lstrip("v") != str(ota_target).lstrip("v"):
            raise HTTPException(
                status_code=409,
                detail="Gateway firmware telemetry does not confirm the completed OTA target version.",
            )
        telemetry = _read_meter_state(str(meter_id))
        if telemetry.get("thing_name") != thing:
            raise HTTPException(status_code=409, detail="Live telemetry Thing does not match the selected gateway.")

        session_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO onemeter_validation_sessions
                (id, batch_reference, thing_name, meter_id, site_code,
                 dummy_customer_label, simulated_balance_kwh,
                 baseline_energy_kwh, latest_energy_kwh, created_by, notes)
            VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id, body.batch_reference.strip(), thing, str(meter_id),
                str(site or ""), body.dummy_customer_label.strip(),
                body.starting_credit_kwh, telemetry["energy_kwh"],
                telemetry["energy_kwh"], str(user.user_id), body.notes,
            ),
        )
        _event(
            cur, session_id, "synthetic_credit", user.user_id,
            amount_kwh=body.starting_credit_kwh,
            energy_kwh=telemetry["energy_kwh"],
            details={"firmware": fw_version or ota_target, "isolated_ledger": True},
        )
        conn.commit()
        return validation_status(session_id, user)


@router.get("/sessions/{session_id}")
def validation_status(
    session_id: str,
    _user: CurrentUser = Depends(CC_VALIDATION_GATE),
):
    with _get_connection() as conn:
        cur = conn.cursor()
        session = _session(cur, session_id)
        telemetry = _read_meter_state(session["meter_id"])
        return {
            "session": session,
            "telemetry": telemetry,
            "disconnect_command": _relay_command(cur, session.get("disconnect_cmd_id")),
            "reconnect_command": _relay_command(cur, session.get("reconnect_cmd_id")),
        }


@router.post("/sessions/{session_id}/observe")
def observe_load(
    session_id: str,
    user: CurrentUser = Depends(CC_VALIDATION_GATE),
):
    with _get_connection() as conn:
        cur = conn.cursor()
        session = _session(cur, session_id, lock=True)
        if session["status"] in ("passed", "failed"):
            raise HTTPException(status_code=409, detail="Validation session is already complete.")
        telemetry = _read_meter_state(session["meter_id"])
        if telemetry.get("thing_name") != session["thing_name"]:
            raise HTTPException(status_code=409, detail="Telemetry moved to a different gateway.")

        previous_energy = float(session.get("latest_energy_kwh") or telemetry["energy_kwh"])
        increment = max(0.0, telemetry["energy_kwh"] - previous_energy)
        total_delta = max(0.0, telemetry["energy_kwh"] - float(session["baseline_energy_kwh"]))
        balance = max(0.0, float(session["simulated_balance_kwh"]) - increment)
        status = "load_seen" if total_delta > 0 else session["status"]
        disconnect_cmd_id = session.get("disconnect_cmd_id")

        if total_delta > 0 and balance <= 0 and not disconnect_cmd_id:
            disconnect_cmd_id = queue_validation_relay(
                thing_name=session["thing_name"],
                meter_id=session["meter_id"],
                action="open",
                reason=f"batch_validation_zero_balance:{session_id}",
                requested_by=f"validation:{user.user_id}",
            )
            status = "disconnected"
            _event(
                cur, session_id, "zero_balance_disconnect", user.user_id,
                energy_kwh=telemetry["energy_kwh"], cmd_id=disconnect_cmd_id,
                details={"relay_expected": "0"},
            )

        cur.execute(
            """
            UPDATE onemeter_validation_sessions
               SET simulated_balance_kwh = %s, latest_energy_kwh = %s,
                   load_delta_kwh = %s, disconnect_cmd_id = %s::uuid,
                   status = %s, updated_at = NOW()
             WHERE id = %s::uuid
            """,
            (balance, telemetry["energy_kwh"], total_delta, disconnect_cmd_id, status, session_id),
        )
        _event(
            cur, session_id, "load_observed", user.user_id,
            amount_kwh=increment, energy_kwh=telemetry["energy_kwh"],
            details={"total_load_delta_kwh": total_delta, "balance_kwh": balance},
        )
        conn.commit()
    return validation_status(session_id, user)


@router.post("/sessions/{session_id}/payment")
def apply_test_payment(
    session_id: str,
    body: ValidationPayment,
    user: CurrentUser = Depends(CC_VALIDATION_GATE),
):
    with _get_connection() as conn:
        cur = conn.cursor()
        session = _session(cur, session_id, lock=True)
        disconnect = _relay_command(cur, session.get("disconnect_cmd_id"))
        if not disconnect or disconnect.get("status") != "completed" or disconnect.get("relay_after") != "0":
            raise HTTPException(
                status_code=409,
                detail="Confirm the zero-balance disconnect acknowledgement (relay read-back 0) first.",
            )
        if session.get("reconnect_cmd_id"):
            raise HTTPException(status_code=409, detail="A synthetic payment was already applied.")
        reconnect_cmd_id = queue_validation_relay(
            thing_name=session["thing_name"],
            meter_id=session["meter_id"],
            action="close",
            reason=f"batch_validation_payment:{session_id}",
            requested_by=f"validation:{user.user_id}",
        )
        cur.execute(
            """
            UPDATE onemeter_validation_sessions
               SET simulated_balance_kwh = simulated_balance_kwh + %s,
                   reconnect_cmd_id = %s::uuid, status = 'reconnected',
                   updated_at = NOW()
             WHERE id = %s::uuid
            """,
            (body.kwh, reconnect_cmd_id, session_id),
        )
        _event(
            cur, session_id, "synthetic_payment_reconnect", user.user_id,
            amount_kwh=body.kwh, cmd_id=reconnect_cmd_id,
            details={"relay_expected": "1", "isolated_ledger": True},
        )
        conn.commit()
    return validation_status(session_id, user)


@router.post("/sessions/{session_id}/complete")
def complete_validation(
    session_id: str,
    user: CurrentUser = Depends(CC_VALIDATION_GATE),
):
    with _get_connection() as conn:
        cur = conn.cursor()
        session = _session(cur, session_id, lock=True)
        disconnect = _relay_command(cur, session.get("disconnect_cmd_id"))
        reconnect = _relay_command(cur, session.get("reconnect_cmd_id"))
        failures = []
        if float(session.get("load_delta_kwh") or 0) <= 0:
            failures.append("no positive energy delta from the physical load")
        if not disconnect or disconnect.get("status") != "completed" or disconnect.get("relay_after") != "0":
            failures.append("disconnect was not acknowledged with relay read-back 0")
        if not reconnect or reconnect.get("status") != "completed" or reconnect.get("relay_after") != "1":
            failures.append("reconnect was not acknowledged with relay read-back 1")
        if failures:
            raise HTTPException(status_code=409, detail="; ".join(failures))
        cur.execute(
            """
            UPDATE onemeter_validation_sessions
               SET status = 'passed', completed_at = NOW(), updated_at = NOW()
             WHERE id = %s::uuid
            """,
            (session_id,),
        )
        _event(cur, session_id, "validation_passed", user.user_id)
        conn.commit()
    return validation_status(session_id, user)
