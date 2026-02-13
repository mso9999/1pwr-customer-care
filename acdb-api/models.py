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


class CCRole(str, Enum):
    superadmin = "superadmin"
    onm_team = "onm_team"
    finance_team = "finance_team"
    generic = "generic"


# Permission matrix: role -> (can_write_customers, can_write_transactions, can_manage_roles)
ROLE_PERMISSIONS = {
    CCRole.superadmin:   {"write_customers": True,  "write_transactions": True,  "manage_roles": True},
    CCRole.onm_team:     {"write_customers": True,  "write_transactions": True,  "manage_roles": False},
    CCRole.finance_team: {"write_customers": False, "write_transactions": True,  "manage_roles": False},
    CCRole.generic:      {"write_customers": False, "write_transactions": False, "manage_roles": False},
}

# Tables considered "transaction" tables (finance can write these)
TRANSACTION_TABLES = {"tblaccountnumbers", "tblbilling", "tblpayments", "tbltransactions", "tblinvoices", "tblaccounthistory1", "tblaccounthistoryOriginal"}


class EmployeeLoginRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Employee ID from HR portal")
    password: str = Field(..., min_length=1, description="Date-based password")


class CustomerLoginRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Customer ID from ACCDB")
    password: str = Field(..., min_length=1, description="Customer password")


class CustomerRegisterRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Customer ID from ACCDB")
    password: str = Field(..., min_length=6, description="New password (min 6 chars)")


class CustomerChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


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
    name: str = ""
    email: str = ""
    permissions: Dict[str, bool] = {}


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
