"""
Billing-source primacy management (1Meter migration test).

The protocol (see ``docs/ops/1meter-billing-migration-protocol.md``) lets ops
flip which meter source is authoritative for an account's kWh balance:

* ``sm`` — SparkMeter (``thundercloud`` or ``koios``); the regulator-billed
  meter; today the default for the entire fleet.
* ``1m`` — 1Meter prototype (``iot``); the test meter being evaluated as a
  potential replacement.

Resolution precedence (in :func:`balance_engine._resolve_billing_priority`):

  1. ``accounts.billing_meter_priority`` (per-account override)
  2. ``system_config(key='billing_meter_priority')`` (fleet default)
  3. Hardcoded ``'sm'`` fallback

This module exposes:

* ``GET  /api/billing-priority`` — fleet default + caller's per-account
  overrides count.
* ``GET  /api/billing-priority/{account_number}`` — effective + override.
* ``PATCH /api/billing-priority/{account_number}`` — set the per-account
  override (or clear it with ``priority=null``); employee-only; audited.
* ``PATCH /api/billing-priority`` — set the fleet default; superadmin-only;
  audited.

Every change writes a ``cc_mutations`` row in the same DB transaction as the
update, mirroring the manual-payment audit pattern. Phase flips are the
single most material billing operation we have, so the audit guarantee here
is non-negotiable.
"""

from __future__ import annotations

import logging
from typing import Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from balance_engine import (
    DEFAULT_PRIORITY,
    VALID_PRIORITIES,
    _resolve_billing_priority,
)
from customer_api import get_connection
from middleware import require_employee, require_role
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.billing-priority")

router = APIRouter(prefix="/api/billing-priority", tags=["billing-priority"])


class PriorityPayload(BaseModel):
    """Body for setting an account or fleet billing priority."""

    priority: Optional[str] = Field(
        default=None,
        description=(
            "'sm', '1m', or null. Null on the per-account endpoint clears the "
            "override (account inherits fleet default). Null on the fleet "
            "endpoint is rejected."
        ),
    )
    note: Optional[str] = Field(
        default=None, max_length=500, description="Free-form ops note recorded in cc_mutations metadata."
    )


def _validate_priority(value: Optional[str], *, allow_null: bool) -> Optional[str]:
    if value is None:
        if allow_null:
            return None
        raise HTTPException(status_code=400, detail="priority is required")
    if value not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"priority must be one of {VALID_PRIORITIES} (got {value!r})",
        )
    return value


def _fetch_account(cur, account_number: str) -> dict:
    cur.execute(
        "SELECT id, account_number, billing_meter_priority "
        "FROM accounts WHERE account_number = %s LIMIT 1",
        (account_number,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Account {account_number} not found")
    return {"id": int(row[0]), "account_number": str(row[1]), "billing_meter_priority": row[2]}


def _fetch_fleet_default(cur) -> str:
    try:
        cur.execute(
            "SELECT value FROM system_config WHERE key = 'billing_meter_priority' LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0] in VALID_PRIORITIES:
            return row[0]
    except Exception:
        cur.connection.rollback()
    return DEFAULT_PRIORITY


@router.get("")
def get_priority_summary(user: CurrentUser = Depends(require_employee)):
    """Fleet default + total count of per-account overrides."""
    with get_connection() as conn:
        cur = conn.cursor()
        fleet_default = _fetch_fleet_default(cur)
        try:
            cur.execute(
                "SELECT billing_meter_priority, COUNT(*) "
                "FROM accounts WHERE billing_meter_priority IS NOT NULL "
                "GROUP BY billing_meter_priority"
            )
            overrides = {row[0]: int(row[1]) for row in cur.fetchall()}
        except Exception:
            conn.rollback()
            overrides = {}
        return {
            "fleet_default": fleet_default,
            "valid_priorities": list(VALID_PRIORITIES),
            "per_account_overrides": overrides,
        }


@router.get("/{account_number}")
def get_account_priority(
    account_number: str, user: CurrentUser = Depends(require_employee)
):
    """Resolved priority for an account + the explicit override (if any)."""
    with get_connection() as conn:
        cur = conn.cursor()
        acct = _fetch_account(cur, account_number)
        effective = _resolve_billing_priority(cur, account_number)
        return {
            "account_number": acct["account_number"],
            "override": acct["billing_meter_priority"],
            "effective_priority": effective,
            "fleet_default": _fetch_fleet_default(cur),
        }


@router.patch("/{account_number}")
def set_account_priority(
    account_number: str,
    payload: PriorityPayload,
    user: CurrentUser = Depends(require_employee),
):
    """Set or clear the per-account priority override.

    ``priority=null`` clears the override (account falls back to the fleet
    default). Audited via ``cc_mutations``.
    """
    new_value = _validate_priority(payload.priority, allow_null=True)

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            acct = _fetch_account(cur, account_number)
            old_value = acct["billing_meter_priority"]

            if old_value == new_value:
                effective = _resolve_billing_priority(cur, account_number)
                return {
                    "status": "noop",
                    "account_number": account_number,
                    "override": new_value,
                    "effective_priority": effective,
                }

            cur.execute(
                "UPDATE accounts SET billing_meter_priority = %s, updated_at = NOW() "
                "WHERE account_number = %s",
                (new_value, account_number),
            )

            try_log_mutation(
                user,
                "update",
                "accounts",
                str(acct["id"]),
                old_values={"billing_meter_priority": old_value},
                new_values={"billing_meter_priority": new_value},
                metadata={
                    "kind": "billing_priority_change",
                    "endpoint": "PATCH /api/billing-priority/{account_number}",
                    "account_number": account_number,
                    "note": (payload.note or "").strip()[:500] or None,
                },
                conn=conn,
            )

            conn.commit()
            effective = _resolve_billing_priority(cur, account_number)
            return {
                "status": "ok",
                "account_number": account_number,
                "previous_override": old_value,
                "override": new_value,
                "effective_priority": effective,
            }
    except HTTPException:
        raise
    except psycopg2.Error as exc:
        logger.error("billing-priority update failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("")
def set_fleet_priority(
    payload: PriorityPayload,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Set the fleet-wide default. Superadmin-only because it shifts billing
    primacy for every account that doesn't have a per-account override.

    Phase 1 -> 2 transition is the canonical use of this endpoint. Audited.
    """
    new_value = _validate_priority(payload.priority, allow_null=False)

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            old_value = _fetch_fleet_default(cur)
            if old_value == new_value:
                return {
                    "status": "noop",
                    "fleet_default": new_value,
                }

            cur.execute(
                "INSERT INTO system_config (key, value) VALUES ('billing_meter_priority', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                (new_value,),
            )

            try_log_mutation(
                user,
                "update",
                "system_config",
                "billing_meter_priority",
                old_values={"value": old_value},
                new_values={"value": new_value},
                metadata={
                    "kind": "fleet_billing_priority_change",
                    "endpoint": "PATCH /api/billing-priority",
                    "note": (payload.note or "").strip()[:500] or None,
                },
                conn=conn,
            )

            conn.commit()
            return {
                "status": "ok",
                "previous_default": old_value,
                "fleet_default": new_value,
            }
    except HTTPException:
        raise
    except psycopg2.Error as exc:
        logger.error("fleet billing-priority update failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
