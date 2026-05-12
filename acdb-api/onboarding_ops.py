"""Onboarding status read/write for customers (commissioning steps)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from commission import COMMISSIONING_STEPS
from customer_api import get_connection
from middleware import require_employee, CurrentUser

logger = logging.getLogger("cc-api.onboarding")

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

ACCOUNT_RE = re.compile(r"^\d{3,4}[A-Z]{2,4}$")
IMPORT_TAG = "onboarding_import_2026-01"


class OnboardingStepUpdate(BaseModel):
    step: str
    value: bool
    date: Optional[str] = None


class OnboardingPatchRequest(BaseModel):
    steps: list[OnboardingStepUpdate] = Field(default_factory=list)
    house_wiring_test_passed: Optional[bool] = None
    house_wiring_test_date: Optional[str] = None
    ciu_payment_date: Optional[str] = None
    voltage_test_passed: Optional[bool] = None
    voltage_test_date: Optional[str] = None
    meter_autostate_test_passed: Optional[bool] = None
    meter_autostate_test_date: Optional[str] = None
    notes: Optional[str] = None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _customer_for_account(cur, account_number: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT c.*, a.account_number, a.survey_id, m.meter_serial
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        LEFT JOIN meters m ON m.account_number = a.account_number
        WHERE a.account_number = %s
        LIMIT 1
        """,
        (account_number,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Account {account_number} not found")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


@router.get("/customer/{account_number}")
def get_onboarding_status(
    account_number: str,
    user: CurrentUser = Depends(require_employee),
):
    account_number = account_number.strip().upper()
    if not ACCOUNT_RE.match(account_number):
        raise HTTPException(400, "Invalid account number")
    with get_connection() as conn:
        cur = conn.cursor()
        payload = _customer_for_account(cur, account_number)
        steps = {}
        for step in COMMISSIONING_STEPS:
            steps[step] = {
                "value": bool(payload.get(step)),
                "date": payload.get(f"{step}_date"),
            }
        return {
            "account_number": account_number,
            "customer_id": payload["id"],
            "community": payload.get("community"),
            "steps": steps,
            "house_wiring_test_passed": payload.get("house_wiring_test_passed"),
            "house_wiring_test_date": payload.get("house_wiring_test_date"),
            "ciu_payment_date": payload.get("ciu_payment_date"),
            "voltage_test_passed": payload.get("voltage_test_passed"),
            "voltage_test_date": payload.get("voltage_test_date"),
            "meter_autostate_test_passed": payload.get("meter_autostate_test_passed"),
            "meter_autostate_test_date": payload.get("meter_autostate_test_date"),
            "survey_id": payload.get("survey_id"),
            "meter_serial": payload.get("meter_serial"),
            "onboarding_import_tag": payload.get("onboarding_import_tag"),
            "notes": payload.get("notes"),
        }


@router.patch("/customer/{account_number}")
def patch_onboarding_status(
    account_number: str,
    body: OnboardingPatchRequest,
    user: CurrentUser = Depends(require_employee),
):
    account_number = account_number.strip().upper()
    with get_connection() as conn:
        cur = conn.cursor()
        customer = _customer_for_account(cur, account_number)
        sets: list[str] = ["updated_at = NOW()", "updated_by = %s", "onboarding_import_tag = %s"]
        params: list[Any] = [user.user_id, IMPORT_TAG]

        for item in body.steps:
            if item.step not in COMMISSIONING_STEPS:
                raise HTTPException(400, f"Invalid step: {item.step}")
            sets.append(f"{item.step} = %s")
            params.append(item.value)
            date_col = f"{item.step}_date"
            sets.append(f"{date_col} = %s")
            params.append(_parse_date(item.date) if item.value else None)

        extras = {
            "house_wiring_test_passed": body.house_wiring_test_passed,
            "house_wiring_test_date": _parse_date(body.house_wiring_test_date),
            "ciu_payment_date": _parse_date(body.ciu_payment_date),
            "voltage_test_passed": body.voltage_test_passed,
            "voltage_test_date": _parse_date(body.voltage_test_date),
            "meter_autostate_test_passed": body.meter_autostate_test_passed,
            "meter_autostate_test_date": _parse_date(body.meter_autostate_test_date),
            "notes": body.notes,
        }
        for col, val in extras.items():
            if val is not None:
                sets.append(f"{col} = %s")
                params.append(val)

        params.append(customer["id"])
        cur.execute(
            f"UPDATE customers SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        conn.commit()
    return get_onboarding_status(account_number, user)


@router.get("/pipeline/accounts")
def list_pipeline_accounts(
    stage: str = Query(...),
    site: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_employee),
):
    if stage not in COMMISSIONING_STEPS and stage != "registered":
        raise HTTPException(400, "Invalid pipeline stage")
    with get_connection() as conn:
        cur = conn.cursor()
        if stage == "registered":
            clause = "TRUE"
        else:
            clause = f"c.{stage} = TRUE"
        site_clause = ""
        params: list[Any] = []
        if site:
            site_clause = "AND c.community = %s"
            params.append(site.upper())
        cur.execute(
            f"""
            SELECT a.account_number, c.id AS customer_id, c.first_name, c.last_name, c.community,
                   c.connection_fee_paid, c.readyboard_fee_paid, c.customer_commissioned
            FROM customers c
            JOIN accounts a ON a.customer_id = c.id
            LEFT JOIN meters m ON m.account_number = a.account_number
            WHERE {clause} {site_clause}
            ORDER BY a.account_number
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"accounts": rows, "stage": stage, "site": site}
