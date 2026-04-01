"""
Meter lifecycle management: assignment tracking, decommission, replacement.

Tracks which meter served which account over time via the meter_assignments
table, enabling continuous consumption history across meter swaps.

The hourly_consumption table already associates data by account_number,
so consumption history is inherently continuous. This module adds the
audit trail and workflow for meter replacements.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from models import CCRole, CurrentUser
from middleware import require_employee
from mutations import log_mutation

logger = logging.getLogger("acdb-api.meter-lifecycle")

router = APIRouter(prefix="/api/meters", tags=["meter-lifecycle"])


def _get_connection():
    from customer_api import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Table initialisation
# ---------------------------------------------------------------------------

def ensure_meter_assignments_table():
    """Create the meter_assignments table if it doesn't exist, then backfill."""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meter_assignments (
                    id              SERIAL PRIMARY KEY,
                    meter_id        VARCHAR(80) NOT NULL,
                    account_number  VARCHAR(20) NOT NULL,
                    community       VARCHAR(10),
                    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    removed_at      TIMESTAMPTZ,
                    removal_reason  TEXT,
                    replaced_by     VARCHAR(80),
                    notes           TEXT,
                    created_by      TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ma_meter
                ON meter_assignments (meter_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ma_account
                ON meter_assignments (account_number)
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ma_active
                ON meter_assignments (meter_id, account_number)
                WHERE removed_at IS NULL
            """)
            conn.commit()

            # Backfill from current meters data if table is empty
            cursor.execute("SELECT COUNT(*) FROM meter_assignments")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO meter_assignments
                        (meter_id, account_number, community, assigned_at, created_by)
                    SELECT
                        meter_id, account_number, community,
                        COALESCE(date_installed, created_at, NOW()),
                        'system_backfill'
                    FROM meters
                    WHERE account_number IS NOT NULL
                      AND account_number != ''
                """)
                backfilled = cursor.rowcount
                conn.commit()
                logger.info("Backfilled %d meter assignments from meters table", backfilled)
            else:
                logger.info("meter_assignments table ready (%d rows)", cursor.fetchone()[0] if False else 0)

    except Exception as e:
        logger.error("meter_assignments init FAILED: %s", e)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

# The meter_status enum in Postgres only allows: active, inactive, decommissioned, maintenance.
# We map user-facing reasons to valid enum values and store the detail in special_notes.
REASON_TO_ENUM: dict[str, str] = {
    "faulty": "decommissioned",
    "test": "inactive",
    "decommissioned": "decommissioned",
    "retired": "decommissioned",
}


class DecommissionRequest(BaseModel):
    reason: str  # "faulty", "test", "decommissioned", "retired"
    replacement_meter_id: Optional[str] = None
    notes: Optional[str] = None


class ReplaceRequest(BaseModel):
    replacement_meter_id: str
    notes: Optional[str] = None


class AssignMeterRequest(BaseModel):
    customer_identifier: str
    meter_id: str
    community: str
    customer_type: str
    account_number: str
    connection_date: str
    village_name: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None


def _row_to_dict(cursor, row) -> dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _resolve_customer_for_assignment(cursor, identifier: str) -> Optional[dict[str, Any]]:
    raw_identifier = str(identifier or "").strip()
    if not raw_identifier:
        return None

    if re.match(r"^\d{3,4}[A-Za-z]{2,4}$", raw_identifier):
        cursor.execute(
            "SELECT c.* FROM accounts a "
            "JOIN customers c ON a.customer_id = c.id "
            "WHERE a.account_number = %s LIMIT 1",
            (raw_identifier.upper(),),
        )
        row = cursor.fetchone()
        return _row_to_dict(cursor, row) if row else None

    if raw_identifier.isdigit():
        cursor.execute("SELECT * FROM customers WHERE id = %s", (raw_identifier,))
        row = cursor.fetchone()
        if row:
            return _row_to_dict(cursor, row)

        cursor.execute(
            "SELECT * FROM customers WHERE customer_id_legacy = %s",
            (raw_identifier,),
        )
        row = cursor.fetchone()
        if row:
            return _row_to_dict(cursor, row)

    return None


def _parse_account_sequence(account_number: str) -> int:
    match = re.match(r"^(\d{3,4})[A-Za-z]{2,4}$", str(account_number or "").strip().upper())
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Account number must start with 3-4 digits followed by the site code",
        )
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/assign")
def assign_meter(
    req: AssignMeterRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Atomically assign a meter and account to an existing customer."""
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Requires superadmin or onm_team role")

    customer_identifier = str(req.customer_identifier or "").strip()
    meter_id = str(req.meter_id or "").strip()
    community = str(req.community or "").strip().upper()
    customer_type = str(req.customer_type or "").strip().upper()
    account_number = str(req.account_number or "").strip().upper()
    connection_date = str(req.connection_date or "").strip()

    if not customer_identifier:
        raise HTTPException(status_code=400, detail="customer_identifier is required")
    if not meter_id:
        raise HTTPException(status_code=400, detail="meter_id is required")
    if not community:
        raise HTTPException(status_code=400, detail="community is required")
    if not customer_type:
        raise HTTPException(status_code=400, detail="customer_type is required")
    if not account_number:
        raise HTTPException(status_code=400, detail="account_number is required")

    account_sequence = _parse_account_sequence(account_number)
    now = datetime.now(timezone.utc).isoformat()

    with _get_connection() as conn:
        cursor = conn.cursor()

        customer = _resolve_customer_for_assignment(cursor, customer_identifier)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer_pg_id = customer.get("id")
        if customer_pg_id is None:
            raise HTTPException(status_code=500, detail="Resolved customer is missing id")

        cursor.execute(
            "SELECT customer_id FROM accounts WHERE account_number = %s",
            (account_number,),
        )
        account_row = cursor.fetchone()
        if account_row:
            existing_customer_id = account_row[0]
            if existing_customer_id and int(existing_customer_id) != int(customer_pg_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Account {account_number} is already linked to another customer",
                )
            cursor.execute(
                "UPDATE accounts SET meter_id = %s, community = %s WHERE account_number = %s",
                (meter_id, community, account_number),
            )
        else:
            cursor.execute(
                "INSERT INTO accounts "
                "(account_number, customer_id, meter_id, community, account_sequence, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (account_number, customer_pg_id, meter_id, community, account_sequence, user.user_id),
            )

        meter_values = (
            community,
            account_number,
            customer_type,
            connection_date or now[:10],
            str(req.village_name or "").strip() or None,
            str(req.latitude or "").strip() or None,
            str(req.longitude or "").strip() or None,
            meter_id,
        )
        cursor.execute("SELECT 1 FROM meters WHERE meter_id = %s", (meter_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE meters SET community = %s, account_number = %s, customer_type = %s, "
                "customer_connect_date = %s, village_name = %s, latitude = %s, longitude = %s "
                "WHERE meter_id = %s",
                meter_values,
            )
        else:
            cursor.execute(
                "INSERT INTO meters "
                "(community, account_number, customer_type, customer_connect_date, village_name, latitude, longitude, meter_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                meter_values,
            )

        cursor.execute(
            "SELECT 1 FROM meter_assignments "
            "WHERE meter_id = %s AND account_number = %s AND removed_at IS NULL",
            (meter_id, account_number),
        )
        if not cursor.fetchone():
            cursor.execute(
                "UPDATE meter_assignments SET removed_at = %s, removal_reason = %s "
                "WHERE removed_at IS NULL AND (meter_id = %s OR account_number = %s)",
                (now, "reassigned", meter_id, account_number),
            )
            cursor.execute(
                "INSERT INTO meter_assignments "
                "(meter_id, account_number, community, assigned_at, created_by, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    meter_id,
                    account_number,
                    community,
                    now,
                    user.user_id,
                    f"Assigned via CC portal to customer {customer.get('customer_id_legacy')}",
                ),
            )

        conn.commit()
        log_mutation(user, "assign", "meters", meter_id)
        return {
            "message": f"Meter {meter_id} assigned to account {account_number}",
            "meter_id": meter_id,
            "account_number": account_number,
            "customer_id_legacy": customer.get("customer_id_legacy"),
        }


@router.post("/{meter_id}/decommission")
def decommission_meter(
    meter_id: str,
    req: DecommissionRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Mark a meter as faulty/test/decommissioned and optionally assign a replacement."""
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Requires superadmin or onm_team role")

    valid_reasons = ("faulty", "test", "decommissioned", "retired")
    if req.reason.lower() not in valid_reasons:
        raise HTTPException(status_code=400, detail=f"reason must be one of: {', '.join(valid_reasons)}")

    with _get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()

        cursor.execute("SELECT account_number, community FROM meters WHERE meter_id = %s", (meter_id,))
        meter_row = cursor.fetchone()
        if not meter_row:
            raise HTTPException(status_code=404, detail=f"Meter {meter_id} not found")

        account_number, community = meter_row[0], meter_row[1]

        # Close the active assignment
        cursor.execute(
            "UPDATE meter_assignments SET removed_at = %s, removal_reason = %s, "
            "replaced_by = %s, notes = %s "
            "WHERE meter_id = %s AND removed_at IS NULL",
            (now, req.reason.lower(), req.replacement_meter_id, req.notes, meter_id),
        )

        reason_lower = req.reason.lower()
        db_status = REASON_TO_ENUM.get(reason_lower, "decommissioned")
        notes_combined = f"[{reason_lower}] {req.notes}" if req.notes else f"[{reason_lower}]"

        cursor.execute(
            "UPDATE meters SET status = %s, status_date = %s, status_set_by = %s, "
            "special_notes = %s WHERE meter_id = %s",
            (db_status, now, user.user_id, notes_combined, meter_id),
        )

        result = {
            "message": f"Meter {meter_id} marked as {req.reason}",
            "meter_id": meter_id,
            "account_number": account_number,
        }

        # If a replacement is specified, create the new assignment
        if req.replacement_meter_id and account_number:
            cursor.execute(
                "SELECT meter_id FROM meters WHERE meter_id = %s",
                (req.replacement_meter_id,),
            )
            if not cursor.fetchone():
                conn.rollback()
                raise HTTPException(
                    status_code=404,
                    detail=f"Replacement meter {req.replacement_meter_id} not found",
                )

            cursor.execute(
                "UPDATE meters SET account_number = %s, community = %s WHERE meter_id = %s",
                (account_number, community, req.replacement_meter_id),
            )
            cursor.execute(
                "INSERT INTO meter_assignments "
                "(meter_id, account_number, community, assigned_at, created_by, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (req.replacement_meter_id, account_number, community, now,
                 user.user_id, f"Replaced {meter_id} ({req.reason})"),
            )
            result["replacement_meter_id"] = req.replacement_meter_id
            result["message"] += f", replaced by {req.replacement_meter_id}"

        conn.commit()
        log_mutation(user, "decommission", "meters", meter_id)
        return result


@router.get("/{meter_id}/history")
def meter_history(
    meter_id: str,
    user: CurrentUser = Depends(require_employee),
):
    """Get the assignment history for a specific meter."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, meter_id, account_number, community, "
            "assigned_at, removed_at, removal_reason, replaced_by, notes, created_by "
            "FROM meter_assignments WHERE meter_id = %s "
            "ORDER BY assigned_at DESC",
            (meter_id,),
        )
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        for r in rows:
            for k, v in r.items():
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    r[k] = str(v)
        return {"meter_id": meter_id, "assignments": rows}


@router.get("/account/{account_number}/history")
def account_meter_history(
    account_number: str,
    user: CurrentUser = Depends(require_employee),
):
    """Get all meters that have served an account, with date ranges."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ma.id, ma.meter_id, ma.account_number, ma.community, "
            "ma.assigned_at, ma.removed_at, ma.removal_reason, ma.replaced_by, "
            "ma.notes, m.status AS current_status, m.platform "
            "FROM meter_assignments ma "
            "LEFT JOIN meters m ON m.meter_id = ma.meter_id "
            "WHERE ma.account_number = %s "
            "ORDER BY ma.assigned_at ASC",
            (account_number,),
        )
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        for r in rows:
            for k, v in r.items():
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    r[k] = str(v)

        return {
            "account_number": account_number,
            "meters": rows,
            "current_meter": next(
                (r["meter_id"] for r in rows if r.get("removed_at") is None), None
            ),
        }


@router.post("/batch-status")
def batch_update_status(
    updates: list[dict],
    user: CurrentUser = Depends(require_employee),
):
    """Batch update meter statuses (for processing spreadsheet annotations).

    Body: [{ "meter_id": "SMRSD-...", "status": "faulty", "notes": "..." }, ...]
    """
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Requires superadmin or onm_team role")

    now = datetime.now(timezone.utc).isoformat()
    results = {"updated": 0, "not_found": 0, "errors": []}

    with _get_connection() as conn:
        cursor = conn.cursor()
        for item in updates:
            mid = item.get("meter_id", "").strip()
            new_status = item.get("status", "").strip().lower()
            notes = item.get("notes", "")
            if not mid or not new_status:
                results["errors"].append(f"Missing meter_id or status: {item}")
                continue
            try:
                cursor.execute("SELECT 1 FROM meters WHERE meter_id = %s", (mid,))
                if not cursor.fetchone():
                    results["not_found"] += 1
                    continue
                db_status = REASON_TO_ENUM.get(new_status, "decommissioned")
                notes_combined = f"[{new_status}] {notes}" if notes else f"[{new_status}]"
                cursor.execute(
                    "UPDATE meters SET status = %s, status_date = %s, "
                    "status_set_by = %s, special_notes = %s WHERE meter_id = %s",
                    (db_status, now, user.user_id, notes_combined, mid),
                )
                cursor.execute(
                    "UPDATE meter_assignments SET removed_at = %s, removal_reason = %s, notes = %s "
                    "WHERE meter_id = %s AND removed_at IS NULL",
                    (now, new_status, notes, mid),
                )
                results["updated"] += 1
            except Exception as e:
                results["errors"].append(f"{mid}: {e}")
        conn.commit()

    return results
