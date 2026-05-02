"""
Admin endpoints for superadmin role management.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from models import (
    CCRole, CurrentUser, RoleAssignment, RoleAssignmentResponse,
    DepartmentMapping, DepartmentMappingResponse,
)
from middleware import require_role
from db_auth import (
    delete_department_mapping,
    delete_employee_role,
    get_employee_role,
    list_department_mappings,
    list_employee_roles,
    set_department_mapping,
    set_employee_role,
)
from auth import lookup_employee
from mutations import try_log_mutation

logger = logging.getLogger("acdb-api.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _enrich_with_hr(role_row: dict) -> RoleAssignmentResponse:
    """Add name/email from HR portal to a role assignment."""
    emp = lookup_employee(role_row["employee_id"])
    return RoleAssignmentResponse(
        employee_id=role_row["employee_id"],
        cc_role=role_row["cc_role"],
        assigned_by=role_row.get("assigned_by", ""),
        assigned_at=role_row.get("assigned_at", ""),
        name=emp.get("name") if emp else None,
        email=emp.get("email") if emp else None,
    )


@router.get("/roles", response_model=List[RoleAssignmentResponse])
def list_roles(user: CurrentUser = Depends(require_role(CCRole.superadmin))):
    """List all employee CC role assignments, enriched with HR portal names."""
    roles = list_employee_roles()
    return [_enrich_with_hr(r) for r in roles]


@router.post("/roles", response_model=RoleAssignmentResponse, status_code=201)
def assign_role(
    req: RoleAssignment,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Assign a CC role to an employee. Enriches with HR portal data if available."""
    existing_role = get_employee_role(req.employee_id)
    emp = lookup_employee(req.employee_id)
    if not emp:
        # Allow assignment even if HR portal is unreachable — employee ID
        # is validated at login time, not role assignment time.
        logger.warning("Employee %s not found in HR portal; assigning role anyway", req.employee_id)
        emp = {"employee_id": req.employee_id, "name": req.employee_id, "email": ""}

    set_employee_role(req.employee_id, req.cc_role.value, user.user_id)
    try_log_mutation(
        user,
        "create" if existing_role is None else "update",
        "cc_employee_roles",
        req.employee_id,
        old_values={"cc_role": existing_role} if existing_role is not None else None,
        new_values={
            "employee_id": req.employee_id,
            "cc_role": req.cc_role.value,
            "assigned_by": user.user_id,
        },
        metadata={"origin": "admin_role_assignment"},
    )
    logger.info("Role %s assigned to %s by %s", req.cc_role.value, req.employee_id, user.user_id)

    return RoleAssignmentResponse(
        employee_id=req.employee_id,
        cc_role=req.cc_role.value,
        assigned_by=user.user_id,
        assigned_at="",  # Will be set by SQLite
        name=emp.get("name"),
        email=emp.get("email"),
    )


@router.put("/roles/{employee_id}", response_model=RoleAssignmentResponse)
def update_role(
    employee_id: str,
    req: RoleAssignment,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Update an employee's CC role."""
    existing = get_employee_role(employee_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No role assignment for '{employee_id}'")

    set_employee_role(employee_id, req.cc_role.value, user.user_id)
    try_log_mutation(
        user,
        "update",
        "cc_employee_roles",
        employee_id,
        old_values={"employee_id": employee_id, "cc_role": existing},
        new_values={
            "employee_id": employee_id,
            "cc_role": req.cc_role.value,
            "assigned_by": user.user_id,
        },
        metadata={"origin": "admin_role_assignment"},
    )
    logger.info("Role updated to %s for %s by %s", req.cc_role.value, employee_id, user.user_id)

    emp = lookup_employee(employee_id)
    return RoleAssignmentResponse(
        employee_id=employee_id,
        cc_role=req.cc_role.value,
        assigned_by=user.user_id,
        assigned_at="",
        name=emp.get("name") if emp else None,
        email=emp.get("email") if emp else None,
    )


@router.delete("/roles/{employee_id}")
def remove_role(
    employee_id: str,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Remove an employee's CC role assignment (reverts to generic)."""
    existing = get_employee_role(employee_id)
    deleted = delete_employee_role(employee_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No role assignment for '{employee_id}'")

    try_log_mutation(
        user,
        "delete",
        "cc_employee_roles",
        employee_id,
        old_values={"employee_id": employee_id, "cc_role": existing},
        new_values={"employee_id": employee_id, "cc_role": CCRole.generic.value},
        metadata={"origin": "admin_role_assignment", "manual_assignment": False},
    )
    logger.info("Role removed for %s by %s", employee_id, user.user_id)
    return {"message": f"Role removed for {employee_id}. They will default to 'generic'."}


# ---------------------------------------------------------------------------
# Employee email mappings (for PR department lookup fallback)
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class EmployeeEmailMapping(BaseModel):
    employee_id: str
    email: str


@router.get("/employee-emails")
def list_employee_emails(user: CurrentUser = Depends(require_role(CCRole.superadmin))):
    """List all employee_id → email mappings stored locally."""
    from pr_lookup import get_employee_email
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(__file__), "cc_auth.db")
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT employee_id, email FROM cc_employee_emails ORDER BY employee_id").fetchall()
        conn.close()
        return [{"employee_id": r[0], "email": r[1]} for r in rows]
    except Exception:
        return []


@router.post("/employee-emails", status_code=201)
def set_employee_email_mapping(
    req: EmployeeEmailMapping,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Store or update an employee_id → email mapping for PR department lookup."""
    from pr_lookup import set_employee_email
    set_employee_email(req.employee_id, req.email)
    logger.info("Email mapping set: %s → %s by %s", req.employee_id, req.email, user.user_id)
    return {"employee_id": req.employee_id, "email": req.email.lower().strip()}


@router.post("/employee-emails/bulk", status_code=201)
def bulk_set_employee_emails(
    mappings: List[EmployeeEmailMapping],
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Bulk store employee_id → email mappings."""
    from pr_lookup import set_employee_email
    results = []
    for m in mappings:
        set_employee_email(m.employee_id, m.email)
        results.append({"employee_id": m.employee_id, "email": m.email.lower().strip()})
    logger.info("Bulk email mapping: %d entries set by %s", len(results), user.user_id)
    return {"count": len(results), "mappings": results}


# ---------------------------------------------------------------------------
# Department → role mappings (auto-role from PR department)
# ---------------------------------------------------------------------------

@router.get("/department-mappings", response_model=List[DepartmentMappingResponse])
def get_department_mappings(
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """List all department→role auto-mappings."""
    return [DepartmentMappingResponse(**r) for r in list_department_mappings()]


@router.post("/department-mappings", response_model=DepartmentMappingResponse, status_code=201)
def add_department_mapping(
    req: DepartmentMapping,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Create or update a department→role mapping."""
    set_department_mapping(
        req.department_key, req.cc_role.value, req.label, user.user_id,
    )
    from pr_lookup import reload_department_mappings
    reload_department_mappings()

    try_log_mutation(
        user, "create", "cc_department_role_mappings", req.department_key,
        new_values={"department_key": req.department_key, "cc_role": req.cc_role.value, "label": req.label},
        metadata={"origin": "admin_department_mapping"},
    )
    logger.info(
        "Department mapping set: %s → %s by %s", req.department_key, req.cc_role.value, user.user_id,
    )
    return DepartmentMappingResponse(
        department_key=req.department_key.lower().strip(),
        cc_role=req.cc_role.value,
        label=req.label,
        added_by=user.user_id,
        added_at="",
    )


@router.delete("/department-mappings/{department_key}")
def remove_department_mapping(
    department_key: str,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Delete a department→role mapping."""
    deleted = delete_department_mapping(department_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No mapping for '{department_key}'")

    from pr_lookup import reload_department_mappings
    reload_department_mappings()

    try_log_mutation(
        user, "delete", "cc_department_role_mappings", department_key,
        old_values={"department_key": department_key},
        metadata={"origin": "admin_department_mapping"},
    )
    logger.info("Department mapping deleted: %s by %s", department_key, user.user_id)
    return {"message": f"Mapping removed for '{department_key}'."}


@router.get("/pr-departments")
def list_pr_departments(
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Return all departments from the PR Firestore for the admin UI."""
    from pr_lookup import get_all_pr_departments
    return get_all_pr_departments()


# ---------------------------------------------------------------------------
# Monthly staff-PIN broadcast (manual trigger)
# ---------------------------------------------------------------------------

class _BroadcastPinRequest(BaseModel):
    """Optional knobs for the manual PIN broadcast."""
    countries: Optional[List[str]] = None  # e.g. ["LS"]; default: every active
    include_next_month: bool = True


@router.get("/auth/pin-preview")
def auth_pin_preview(
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Preview the message that the monthly PIN broadcast would send.

    Returns the rendered text + the active countries it would target. The
    PIN itself is included since the requester is already authenticated as
    superadmin (same trust level as anyone who can pull the PIN from the
    server's env or run the systemd unit).
    """
    from auth_pin_broadcast import compose_pin_message
    from country_config import _REGISTRY  # type: ignore[attr-defined]
    from datetime import datetime, timezone

    when = datetime.now(timezone.utc)
    msg = compose_pin_message(when.year, when.month, include_next_month=True)
    targets = sorted(c for c, cfg in _REGISTRY.items() if cfg.active)
    return {
        "year": when.year,
        "month": when.month,
        "active_countries": targets,
        "message": msg,
    }


@router.post("/auth/broadcast-pin")
def auth_broadcast_pin(
    req: _BroadcastPinRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Manually trigger the monthly PIN WhatsApp broadcast.

    Mirrors the systemd-timer-driven path
    (``scripts/ops/broadcast_monthly_pin.py``) so ops have an in-portal
    fallback when the timer is down or when the team needs a re-send.
    """
    from auth_pin_broadcast import broadcast_pin_for_active_countries

    results = broadcast_pin_for_active_countries(
        only=req.countries,
        include_next_month=req.include_next_month,
    )
    try_log_mutation(
        user, "broadcast_pin", "auth_pin_broadcast", "monthly",
        new_values={"results": results},
        metadata={"origin": "admin_manual_trigger"},
    )
    logger.info(
        "Manual PIN broadcast by %s -- countries=%s ok=%d failed=%d",
        user.user_id,
        [r["country_code"] for r in results],
        sum(1 for r in results if r["ok"]),
        sum(1 for r in results if not r["ok"]),
    )
    return {"results": results}
