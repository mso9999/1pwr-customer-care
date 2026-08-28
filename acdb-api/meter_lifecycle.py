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
from middleware import require_action, require_employee
from mutations import log_mutation
from sparkmeter_customer import sync_sparkmeter_customer_and_meter

logger = logging.getLogger("acdb-api.meter-lifecycle")

router = APIRouter(prefix="/api/meters", tags=["meter-lifecycle"])

CC_METER_OPERATE_GATE = require_action(
    "operate_customer_care",
    system="cc",
    action="assign and maintain customer meter records",
    required_level="C",
    fallback_roles=(CCRole.superadmin, CCRole.onm_team),
)


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
            # Fail fast rather than hang startup if a backup/long read holds a
            # conflicting lock (see meter_provisioning note, 2026-07-06). All
            # DDL below is idempotent, so skipping this startup is safe.
            cursor.execute("SET lock_timeout = '4s'")
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
    thing_name: Optional[str] = None
    activate_1meter_billing: bool = False
    platform: str = "sparkmeter"
    community: str
    customer_type: str
    account_number: str
    connection_date: str
    village_name: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None


class MeterAssignmentUpdate(BaseModel):
    platform: str
    role: str
    note: Optional[str] = None


VALID_METER_PLATFORMS = {"sparkmeter", "prototype"}
OPERATOR_TO_DB_ROLE = {
    "primary": "primary",
    "secondary": "check",
    # Keep accepting the database label for API compatibility. The CC UI calls
    # this role "secondary", which is clearer to country operators.
    "check": "check",
}


def _normalise_platform(value: str) -> str:
    platform = str(value or "").strip().lower()
    if platform not in VALID_METER_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail="platform must be sparkmeter or prototype (1Meter)",
        )
    return platform


def _normalise_assignment_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in OPERATOR_TO_DB_ROLE:
        raise HTTPException(status_code=400, detail="role must be primary or secondary")
    return OPERATOR_TO_DB_ROLE[role]


def _operator_role(db_role: str) -> str:
    return "secondary" if db_role == "check" else db_role


def _assignment_role(existing_primary_id: Optional[str], promote_1meter: bool) -> str:
    return "primary" if promote_1meter or not existing_primary_id else "check"


def _normalise_meter_serial(value: str) -> str:
    serial = str(value or "").strip()
    return serial.lstrip("0") or serial


def _lock_provisioned_gateway_for_assignment(
    cursor,
    *,
    thing_name: str,
    requested_meter_id: str,
    community: str,
    account_number: str,
) -> tuple[str, dict[str, Any]]:
    """Validate and lock CC's discovered gateway/meter binding.

    Operators must select a gateway whose serial was learned from live
    telemetry. This removes manual serial transcription from the 1Meter
    commissioning path and makes the gateway, meter, site, and account update
    one database transaction.
    """
    cursor.execute(
        """
        SELECT thing_name, meter_serial, site, account_number, status,
               last_seen_online, ota_status, fw_version, ota_target_version
          FROM meter_provisioning
         WHERE thing_name = %s
         FOR UPDATE
        """,
        (thing_name,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Provisioned gateway {thing_name} not found")

    columns = [d[0] for d in cursor.description]
    gateway = dict(zip(columns, row))
    detected_serial = str(gateway.get("meter_serial") or "").strip()
    if not detected_serial:
        raise HTTPException(
            status_code=409,
            detail="This gateway has not discovered a meter yet. Power the meter and gateway, "
                   "wait for telemetry, then reconcile provisioning before assignment.",
        )
    if _normalise_meter_serial(requested_meter_id) != _normalise_meter_serial(detected_serial):
        raise HTTPException(
            status_code=409,
            detail=f"Meter serial does not match the serial reported by {thing_name}.",
        )
    if str(gateway.get("site") or "").strip().upper() != community:
        raise HTTPException(
            status_code=409,
            detail=f"Gateway {thing_name} belongs to {gateway.get('site')}, not {community}.",
        )
    existing_account = str(gateway.get("account_number") or "").strip().upper()
    if existing_account and existing_account != account_number:
        raise HTTPException(
            status_code=409,
            detail=f"Gateway {thing_name} is already commissioned to {existing_account}.",
        )
    if not gateway.get("last_seen_online"):
        raise HTTPException(
            status_code=409,
            detail=f"Gateway {thing_name} has not been observed online.",
        )
    if str(gateway.get("ota_status") or "").upper() != "SUCCEEDED":
        raise HTTPException(
            status_code=409,
            detail=f"Gateway {thing_name} has not completed the required full-firmware OTA.",
        )

    cursor.execute(
        """
        SELECT thing_name
          FROM meter_provisioning
         WHERE meter_serial = %s
           AND thing_name <> %s
           AND NULLIF(account_number, '') IS NOT NULL
           AND status = 'commissioned'
         LIMIT 1
        """,
        (detected_serial, thing_name),
    )
    conflict = cursor.fetchone()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Meter {detected_serial} is already commissioned through {conflict[0]}.",
        )

    # Reuse the canonical short serial already present in `meters`, if any.
    stripped = _normalise_meter_serial(detected_serial)
    cursor.execute(
        """
        SELECT meter_id
          FROM meters
         WHERE platform = 'prototype'
           AND meter_id = ANY(%s)
         ORDER BY CASE WHEN meter_id = %s THEN 0 ELSE 1 END
         LIMIT 1
        """,
        ([detected_serial, stripped], stripped),
    )
    existing_meter = cursor.fetchone()
    canonical_meter_id = str(existing_meter[0]) if existing_meter else detected_serial
    return canonical_meter_id, gateway


def _row_to_dict(cursor, row) -> dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _fetch_table_row(cursor, table_name: str, column: str, value: Optional[str]) -> Optional[dict[str, Any]]:
    if not value:
        return None
    cursor.execute(f"SELECT * FROM {table_name} WHERE {column} = %s", (value,))
    row = cursor.fetchone()
    return _row_to_dict(cursor, row) if row else None


def _fetch_active_assignment(
    cursor,
    *,
    meter_id: Optional[str] = None,
    account_number: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if meter_id:
        cursor.execute(
            """
            SELECT id, meter_id, account_number, community, assigned_at, removed_at,
                   removal_reason, replaced_by, notes, created_by
            FROM meter_assignments
            WHERE meter_id = %s AND removed_at IS NULL
            ORDER BY assigned_at DESC
            LIMIT 1
            """,
            (meter_id,),
        )
    elif account_number:
        cursor.execute(
            """
            SELECT id, meter_id, account_number, community, assigned_at, removed_at,
                   removal_reason, replaced_by, notes, created_by
            FROM meter_assignments
            WHERE account_number = %s AND removed_at IS NULL
            ORDER BY assigned_at DESC
            LIMIT 1
            """,
            (account_number,),
        )
    else:
        return None

    row = cursor.fetchone()
    return _row_to_dict(cursor, row) if row else None


def _snapshot_meter_lifecycle_state(
    cursor,
    meter_id: str,
    account_number: Optional[str] = None,
    related_meter_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "meter": _fetch_table_row(cursor, "meters", "meter_id", meter_id),
        "account": _fetch_table_row(cursor, "accounts", "account_number", account_number),
        "meter_active_assignment": _fetch_active_assignment(cursor, meter_id=meter_id),
        "account_active_assignment": _fetch_active_assignment(cursor, account_number=account_number),
    }
    if related_meter_ids:
        related: dict[str, Any] = {}
        for related_meter_id in related_meter_ids:
            if not related_meter_id:
                continue
            related[related_meter_id] = {
                "meter": _fetch_table_row(cursor, "meters", "meter_id", related_meter_id),
                "active_assignment": _fetch_active_assignment(cursor, meter_id=related_meter_id),
            }
        if related:
            snapshot["related_meters"] = related
    return snapshot


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
    user: CurrentUser = Depends(CC_METER_OPERATE_GATE),
):
    """Atomically assign a meter and account to an existing customer."""
    customer_identifier = str(req.customer_identifier or "").strip()
    meter_id = str(req.meter_id or "").strip()
    thing_name = str(req.thing_name or "").strip()
    platform = _normalise_platform(req.platform)
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
    if platform == "prototype" and not thing_name:
        raise HTTPException(
            status_code=400,
            detail="Select a provisioned 1Meter gateway; its meter serial must come from live telemetry.",
        )
    if platform == "sparkmeter" and thing_name:
        raise HTTPException(status_code=400, detail="A provisioned gateway can only be assigned as 1Meter")
    if req.activate_1meter_billing and platform != "prototype":
        raise HTTPException(status_code=400, detail="1Meter billing can only be enabled for a 1Meter gateway")

    account_sequence = _parse_account_sequence(account_number)
    now = datetime.now(timezone.utc).isoformat()

    with _get_connection() as conn:
        cursor = conn.cursor()
        try:
            customer = _resolve_customer_for_assignment(cursor, customer_identifier)
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")

            customer_pg_id = customer.get("id")
            if customer_pg_id is None:
                raise HTTPException(status_code=500, detail="Resolved customer is missing id")

            # Serialize assignments to the same account before deciding which
            # meter is primary. This keeps two near-simultaneous operator
            # submissions from both becoming primary.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (account_number,))
            cursor.execute(
                "SELECT customer_id, meter_id FROM accounts "
                "WHERE account_number = %s FOR UPDATE",
                (account_number,),
            )
            account_row = cursor.fetchone()

            gateway = None
            if thing_name:
                meter_id, gateway = _lock_provisioned_gateway_for_assignment(
                    cursor,
                    thing_name=thing_name,
                    requested_meter_id=meter_id,
                    community=community,
                    account_number=account_number,
                )

            # The first active meter is primary. Every additional meter starts
            # as the operator-facing "secondary" role (stored as the legacy
            # `check` value so consumption reports continue to exclude it).
            # Explicit 1Meter billing promotion is the sole assignment-time
            # override and atomically demotes the prior primary.
            cursor.execute(
                "SELECT meter_id FROM meters "
                "WHERE account_number = %s AND role = 'primary' "
                "AND (status = 'active' OR status IS NULL) AND meter_id <> %s "
                "ORDER BY meter_id LIMIT 1 FOR UPDATE",
                (account_number, meter_id),
            )
            primary_row = cursor.fetchone()
            existing_primary_id = str(primary_row[0]) if primary_row else None
            target_role = _assignment_role(existing_primary_id, req.activate_1meter_billing)

            before_state = _snapshot_meter_lifecycle_state(cursor, meter_id, account_number)

            if account_row:
                existing_customer_id = account_row[0]
                if existing_customer_id and int(existing_customer_id) != int(customer_pg_id):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Account {account_number} is already linked to another customer",
                    )
                if target_role == "primary":
                    cursor.execute(
                        "UPDATE accounts SET meter_id = %s, community = %s WHERE account_number = %s",
                        (meter_id, community, account_number),
                    )
                else:
                    cursor.execute(
                        "UPDATE accounts SET community = %s WHERE account_number = %s",
                        (community, account_number),
                    )
            else:
                current_meter_id = meter_id if target_role == "primary" else existing_primary_id
                cursor.execute(
                    "INSERT INTO accounts "
                    "(account_number, customer_id, meter_id, community, account_sequence, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (account_number, customer_pg_id, current_meter_id, community, account_sequence, user.user_id),
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

            if target_role == "primary":
                cursor.execute(
                    """
                    UPDATE meters
                       SET role = 'check', updated_at = NOW()
                     WHERE account_number = %s
                       AND role = 'primary'
                       AND meter_id <> %s
                    """,
                    (account_number, meter_id),
                )
                cursor.execute(
                    "UPDATE accounts SET billing_meter_priority = %s WHERE account_number = %s",
                    ("1m" if platform == "prototype" else "sm", account_number),
                )

            cursor.execute(
                """
                UPDATE meters
                   SET platform = %s, role = %s, status = 'active',
                       account_number = %s, community = %s, updated_at = NOW()
                 WHERE meter_id = %s
                """,
                (platform, target_role, account_number, community, meter_id),
            )

            cursor.execute(
                "SELECT 1 FROM meter_assignments "
                "WHERE meter_id = %s AND account_number = %s AND removed_at IS NULL",
                (meter_id, account_number),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "UPDATE meter_assignments SET removed_at = %s, removal_reason = %s "
                    "WHERE removed_at IS NULL AND meter_id = %s AND account_number <> %s",
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

            if gateway is not None:
                cursor.execute(
                    """
                    UPDATE meter_provisioning
                       SET account_number = %s,
                           status = 'commissioned',
                           commissioned_at = COALESCE(commissioned_at, NOW()),
                           updated_at = NOW()
                     WHERE thing_name = %s
                    """,
                    (account_number, thing_name),
                )

            after_state = _snapshot_meter_lifecycle_state(cursor, meter_id, account_number)
            log_mutation(
                user,
                "assign",
                "meters",
                meter_id,
                old_values=before_state,
                new_values=after_state,
                metadata={
                    "customer_identifier": customer_identifier,
                    "thing_name": thing_name or None,
                    "source": "gateway_telemetry" if thing_name else "manual_meter_entry",
                    "platform": platform,
                    "role": _operator_role(target_role),
                    "activate_1meter_billing": bool(req.activate_1meter_billing),
                },
                conn=conn,
            )
            conn.commit()

            # Auto-exit: a metered account is no longer on unmetered service.
            # Best-effort after the assignment commit — the monthly accrual
            # job's active-meter guard reconciles anything missed here.
            try:
                from unmetered_service import end_unmetered_service
                if end_unmetered_service(conn, account_number, "meter_assigned", user.user_id):
                    conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.warning(
                    "unmetered-service exit hook failed for %s (accrual guard will reconcile): %s",
                    account_number, exc,
                )
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Assign failed: {e}")

        sm_sync = None
        if platform == "sparkmeter":
            try:
                full_name = " ".join(
                    filter(None, [customer.get("first_name"), customer.get("last_name")])
                )
                sm_sync = sync_sparkmeter_customer_and_meter(
                    account_number=account_number,
                    name=full_name.strip() or account_number,
                    meter_serial=meter_id,
                    phone=None,
                )
            except Exception as e:
                logger.warning("SM customer sync on assign failed for %s: %s", account_number, e)
                sm_sync = {"success": False, "error": str(e)}

        result = {
            "message": f"Meter {meter_id} assigned to account {account_number}",
            "meter_id": meter_id,
            "account_number": account_number,
            "customer_id_legacy": customer.get("customer_id_legacy"),
            "thing_name": thing_name or None,
            "platform": platform,
            "role": _operator_role(target_role),
            "billing_meter_priority": ("1m" if platform == "prototype" else "sm") if target_role == "primary" else None,
        }
        if sm_sync:
            result["sm_sync"] = sm_sync
        return result


@router.patch("/{meter_id}/assignment")
def update_meter_assignment(
    meter_id: str,
    req: MeterAssignmentUpdate,
    user: CurrentUser = Depends(CC_METER_OPERATE_GATE),
):
    """Correct an existing meter's platform or primary/secondary role.

    The account pointer, billing source, and any prior primary are updated in
    the same transaction so the UI cannot leave an account with two primaries.
    """
    meter_id = str(meter_id or "").strip()
    platform = _normalise_platform(req.platform)
    target_role = _normalise_assignment_role(req.role)

    with _get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT account_number, platform, role, status FROM meters "
                "WHERE meter_id = %s FOR UPDATE",
                (meter_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Meter not found: {meter_id}")

            account_number = str(row[0] or "").strip().upper()
            old_values = {
                "account_number": account_number or None,
                "platform": row[1],
                "role": _operator_role(str(row[2] or "")),
                "status": row[3],
            }

            other_primary_id = None
            if account_number:
                cursor.execute(
                    "SELECT meter_id FROM meters "
                    "WHERE account_number = %s AND role = 'primary' AND meter_id <> %s "
                    "AND (status = 'active' OR status IS NULL) "
                    "ORDER BY meter_id LIMIT 1 FOR UPDATE",
                    (account_number, meter_id),
                )
                other_primary = cursor.fetchone()
                other_primary_id = str(other_primary[0]) if other_primary else None

            if target_role == "check" and account_number and not other_primary_id:
                raise HTTPException(
                    status_code=409,
                    detail="This is the account's only primary meter. Promote another meter before making it secondary.",
                )

            demoted_count = 0
            if target_role == "primary" and account_number:
                cursor.execute(
                    "UPDATE meters SET role = 'check', updated_at = NOW() "
                    "WHERE account_number = %s AND role = 'primary' AND meter_id <> %s",
                    (account_number, meter_id),
                )
                demoted_count = cursor.rowcount

            cursor.execute(
                "UPDATE meters SET platform = %s, role = %s, updated_at = NOW() WHERE meter_id = %s",
                (platform, target_role, meter_id),
            )

            if account_number:
                primary_meter_id = meter_id if target_role == "primary" else other_primary_id
                primary_platform = platform
                if target_role == "check" and other_primary_id:
                    cursor.execute(
                        "SELECT platform FROM meters WHERE meter_id = %s",
                        (other_primary_id,),
                    )
                    platform_row = cursor.fetchone()
                    primary_platform = str(platform_row[0] or "sparkmeter") if platform_row else "sparkmeter"
                cursor.execute(
                    "UPDATE accounts SET meter_id = %s, billing_meter_priority = %s "
                    "WHERE account_number = %s",
                    (
                        primary_meter_id,
                        "1m" if primary_platform == "prototype" else "sm",
                        account_number,
                    ),
                )

            new_values = {
                "account_number": account_number or None,
                "platform": platform,
                "role": _operator_role(target_role),
                "status": row[3],
            }
            log_mutation(
                user,
                "update_assignment",
                "meters",
                meter_id,
                old_values=old_values,
                new_values=new_values,
                metadata={
                    "note": str(req.note or "").strip() or None,
                    "demoted_count": demoted_count,
                },
                conn=conn,
            )
            conn.commit()
            return {
                "message": f"Meter {meter_id} assignment updated",
                "meter_id": meter_id,
                "account_number": account_number or None,
                "platform": platform,
                "role": _operator_role(target_role),
                "demoted_count": demoted_count,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Assignment update failed: {e}")


@router.post("/{meter_id}/decommission")
def decommission_meter(
    meter_id: str,
    req: DecommissionRequest,
    user: CurrentUser = Depends(CC_METER_OPERATE_GATE),
):
    """Mark a meter as faulty/test/decommissioned and optionally assign a replacement."""
    valid_reasons = ("faulty", "test", "decommissioned", "retired")
    if req.reason.lower() not in valid_reasons:
        raise HTTPException(status_code=400, detail=f"reason must be one of: {', '.join(valid_reasons)}")

    with _get_connection() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        try:
            cursor.execute("SELECT account_number, community FROM meters WHERE meter_id = %s", (meter_id,))
            meter_row = cursor.fetchone()
            if not meter_row:
                raise HTTPException(status_code=404, detail=f"Meter {meter_id} not found")

            account_number, community = meter_row[0], meter_row[1]
            before_state = _snapshot_meter_lifecycle_state(
                cursor,
                meter_id,
                account_number,
                related_meter_ids=[req.replacement_meter_id] if req.replacement_meter_id else None,
            )

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

            after_state = _snapshot_meter_lifecycle_state(
                cursor,
                meter_id,
                account_number,
                related_meter_ids=[req.replacement_meter_id] if req.replacement_meter_id else None,
            )
            log_mutation(
                user,
                "decommission",
                "meters",
                meter_id,
                old_values=before_state,
                new_values=after_state,
                metadata={"reason": req.reason.lower(), "replacement_meter_id": req.replacement_meter_id},
                conn=conn,
            )
            conn.commit()
            return result
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Decommission failed: {e}")


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
    user: CurrentUser = Depends(CC_METER_OPERATE_GATE),
):
    """Batch update meter statuses (for processing spreadsheet annotations).

    Body: [{ "meter_id": "SMRSD-...", "status": "faulty", "notes": "..." }, ...]
    """
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
            cursor.execute("SAVEPOINT batch_status_item")
            try:
                before_state = _snapshot_meter_lifecycle_state(cursor, mid)
                if before_state.get("meter") is None:
                    cursor.execute("RELEASE SAVEPOINT batch_status_item")
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
                after_state = _snapshot_meter_lifecycle_state(cursor, mid)
                log_mutation(
                    user,
                    "batch_status",
                    "meters",
                    mid,
                    old_values=before_state,
                    new_values=after_state,
                    metadata={"requested_status": new_status, "notes": notes},
                    conn=conn,
                )
                cursor.execute("RELEASE SAVEPOINT batch_status_item")
                results["updated"] += 1
            except Exception as e:
                cursor.execute("ROLLBACK TO SAVEPOINT batch_status_item")
                cursor.execute("RELEASE SAVEPOINT batch_status_item")
                results["errors"].append(f"{mid}: {e}")
        conn.commit()

    return results
