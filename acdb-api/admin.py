"""
Admin endpoints for superadmin role management.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from models import CCRole, CurrentUser, RoleAssignment, RoleAssignmentResponse
from middleware import require_role
from db_auth import (
    delete_employee_role,
    get_employee_role,
    list_employee_roles,
    set_employee_role,
)
from auth import lookup_employee

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
    emp = lookup_employee(req.employee_id)
    if not emp:
        # Allow assignment even if HR portal is unreachable — employee ID
        # is validated at login time, not role assignment time.
        logger.warning("Employee %s not found in HR portal; assigning role anyway", req.employee_id)
        emp = {"employee_id": req.employee_id, "name": req.employee_id, "email": ""}

    set_employee_role(req.employee_id, req.cc_role.value, user.user_id)
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
    deleted = delete_employee_role(employee_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No role assignment for '{employee_id}'")

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
