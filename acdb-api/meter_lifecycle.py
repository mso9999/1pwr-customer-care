"""
Meter lifecycle management: assignment tracking, decommission, replacement.

Tracks which meter served which account over time via the meter_assignments
table, enabling continuous consumption history across meter swaps.

The hourly_consumption table already associates data by account_number,
so consumption history is inherently continuous. This module adds the
audit trail and workflow for meter replacements.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

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

class DecommissionRequest(BaseModel):
    reason: str  # "faulty", "test", "decommissioned"
    replacement_meter_id: Optional[str] = None
    notes: Optional[str] = None


class ReplaceRequest(BaseModel):
    replacement_meter_id: str
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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

        # Update meter status
        cursor.execute(
            "UPDATE meters SET status = %s, status_date = %s, status_set_by = %s "
            "WHERE meter_id = %s",
            (req.reason.lower(), now, user.user_id, meter_id),
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
                cursor.execute(
                    "UPDATE meters SET status = %s, status_date = %s, "
                    "status_set_by = %s, special_notes = %s WHERE meter_id = %s",
                    (new_status, now, user.user_id, notes, mid),
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
