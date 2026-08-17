"""
Pydantic models for the Customer Care Portal API.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------

class UserType(str, Enum):
    customer = "customer"
    employee = "employee"
    # Committee / field registrar: non-employee identity that may ONLY register
    # new customers (MGF018 tablet-primary flow). Credentials live in the local
    # auth SQLite store (cc_field_registrars), not HR.
    registrar = "registrar"


class CCRole(str, Enum):
    superadmin = "superadmin"
    onm_team = "onm_team"
    finance_team = "finance_team"
    engineering = "engineering"
    generic = "generic"
    # Not grantable to employees via role admin; carried by registrar JWTs.
    field_registrar = "field_registrar"


# Permission matrix: role -> (can_write_customers, can_write_transactions, can_manage_roles)
# `engineering` (R&D) is least-privilege like `generic` for customer/finance data;
# its purpose is 1Meter provisioning access, which is gated separately in
# meter_provisioning.py (PROVISIONING_ROLES).
# `field_registrar` gets NO generic CRUD writes — registration is allowed via a
# dedicated gate in registration.py, not the generic table-write permissions.
ROLE_PERMISSIONS = {
    CCRole.superadmin:      {"write_customers": True,  "write_transactions": True,  "manage_roles": True},
    CCRole.onm_team:        {"write_customers": True,  "write_transactions": True,  "manage_roles": False},
    CCRole.finance_team:    {"write_customers": False, "write_transactions": True,  "manage_roles": False},
    CCRole.engineering:     {"write_customers": False, "write_transactions": False, "manage_roles": False},
    CCRole.generic:         {"write_customers": False, "write_transactions": False, "manage_roles": False},
    CCRole.field_registrar: {"write_customers": False, "write_transactions": False, "manage_roles": False},
}

# Tables considered "transaction" tables (finance can write these)
TRANSACTION_TABLES = {"accounts", "payments", "transactions", "monthly_transactions"}


class EmployeeLoginRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Employee ID from HR portal")
    password: str = Field(..., min_length=1, description="Date-based password")


class CustomerLoginRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Customer ID")
    password: str = Field(..., min_length=1, description="Customer password")


class CustomerRegisterRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Customer ID")
    password: str = Field(..., min_length=6, description="New password (min 6 chars)")


class CustomerChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


class RegistrarLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Registrar username (issued by CC admin)")
    password: str = Field(..., min_length=1, description="Registrar password")


class RegistrarCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, description="Unique login name, e.g. 'mak-committee-1'")
    password: str = Field(..., min_length=6, description="Initial password (min 6 chars)")
    display_name: str = Field(..., min_length=1, description="Person or committee name shown in audit logs")
    site_code: Optional[str] = Field(
        default=None,
        description="Optional site binding (e.g. MAK). When set, this registrar may only register customers in that site.",
    )


class RegistrarUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    site_code: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6, description="Reset password")


class RegistrarResponse(BaseModel):
    username: str
    display_name: str
    site_code: Optional[str]
    active: bool
    created_by: str
    created_at: str
    updated_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, Any]


class CurrentUser(BaseModel):
    """Decoded JWT payload representing the current user."""
    user_type: UserType
    user_id: str  # employee_id or customer_id
    role: str     # CCRole value or "customer"
    roles: List[str] = Field(default_factory=list)
    name: str = ""
    email: str = ""
    permissions: Dict[str, bool] = Field(default_factory=dict)
    privilege_system: str = ""
    privilege_level: str = ""
    privilege_actions: List[str] = Field(default_factory=list)
    privilege_version: str = ""
    scope_countries: List[str] = Field(default_factory=list)
    scope_organizations: List[str] = Field(default_factory=list)
    role_crud_owners: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Admin models
# ---------------------------------------------------------------------------

class RoleAssignment(BaseModel):
    employee_id: str = Field(..., min_length=1)
    cc_role: CCRole


class RoleAssignmentResponse(BaseModel):
    employee_id: str
    cc_role: str
    assigned_by: str
    assigned_at: str
    # From HR portal
    name: Optional[str] = None
    email: Optional[str] = None


class DepartmentMapping(BaseModel):
    department_key: str = Field(..., min_length=1)
    cc_role: CCRole
    label: str = ""


class DepartmentMappingResponse(BaseModel):
    department_key: str
    cc_role: str
    label: str
    added_by: str
    added_at: str


# ---------------------------------------------------------------------------
# CRUD / Schema models
# ---------------------------------------------------------------------------

class TableInfo(BaseModel):
    name: str
    row_count: int
    column_count: int


class ColumnInfo(BaseModel):
    name: str
    type_name: str
    nullable: bool
    size: Optional[int] = None


class PaginatedResponse(BaseModel):
    rows: List[Dict[str, Any]]
    total: int
    page: int
    limit: int
    pages: int


class RecordCreateRequest(BaseModel):
    data: Dict[str, Any]


class RecordUpdateRequest(BaseModel):
    data: Dict[str, Any]
