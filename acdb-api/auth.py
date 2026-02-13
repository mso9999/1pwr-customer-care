"""
Authentication endpoints for the Customer Care Portal.

Two login modes:
  - Employee: employee_id + date-based password, name from HR portal
  - Customer: account_number + self-set password, data from ACCDB

Customer identity:
  Customers identify by their **account number** (e.g. 0045MAK), which is the
  same identifier they use when making mobile money payments. The format is
  4 digits + 3-letter site code (NNNNXXX). We also accept the reversed format
  (XXXNNNN) and normalise to NNNNXXX for storage.
"""

import os
import re
import logging
from datetime import datetime
from typing import Optional

import bcrypt as _bcrypt
import requests
from fastapi import APIRouter, Depends, HTTPException, status

from models import (
    CCRole,
    CurrentUser,
    CustomerChangePasswordRequest,
    CustomerLoginRequest,
    CustomerRegisterRequest,
    EmployeeLoginRequest,
    ROLE_PERMISSIONS,
    TokenResponse,
    UserType,
)
from middleware import create_token, get_current_user
from db_auth import (
    customer_is_registered,
    get_customer_password_hash,
    get_employee_role,
    set_customer_password,
)

logger = logging.getLogger("acdb-api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# HR Portal integration
# ---------------------------------------------------------------------------

HR_PORTAL_URL = os.environ.get("HR_PORTAL_URL", "https://13.246.55.153")
HR_PORTAL_API_KEY = os.environ.get("HR_PORTAL_API_KEY", "")


def lookup_employee(employee_id: str) -> Optional[dict]:
    """
    Look up an employee in the HR portal by employee_id.
    Returns dict with employee_id, name, email, role or None.
    Accepts bare numbers (e.g. "137") -- the HR portal normalises to "1PWR137".
    """
    if not HR_PORTAL_URL:
        logger.warning("HR_PORTAL_URL not configured, skipping employee lookup")
        return None

    url = f"{HR_PORTAL_URL}/api/employees/lookup/{employee_id}"
    headers = {}
    if HR_PORTAL_API_KEY:
        headers["X-API-Key"] = HR_PORTAL_API_KEY

    try:
        resp = requests.get(url, headers=headers, timeout=5, verify=False)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            return None
        else:
            logger.warning("HR portal returned %d for employee %s", resp.status_code, employee_id)
            return None
    except requests.RequestException as e:
        logger.error("HR portal request failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Date-based password
# ---------------------------------------------------------------------------

def generate_date_password() -> str:
    """
    Compute the date-based password for the current month.
    Formula: YYYYMM / reverse(YYYYMM), first 4 significant digits.
    """
    now = datetime.utcnow()
    yyyymm = now.strftime("%Y%m")
    reversed_str = yyyymm[::-1]
    numerator = int(yyyymm)
    denominator = int(reversed_str)
    if denominator == 0:
        return "0000"
    result = numerator / denominator
    # Extract first 4 significant digits
    result_str = f"{result:.10f}".replace(".", "").lstrip("0")
    return result_str[:4] if len(result_str) >= 4 else result_str.ljust(4, "0")


# ---------------------------------------------------------------------------
# Employee login
# ---------------------------------------------------------------------------

@router.post("/employee-login", response_model=TokenResponse)
def employee_login(req: EmployeeLoginRequest):
    """
    Employee login with employee_id + date-based password.
    Cross-references HR portal for name/email.
    """
    # Validate date-based password
    expected_password = generate_date_password()
    if req.password != expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Look up employee in HR portal
    emp = lookup_employee(req.employee_id)
    if not emp:
        # Allow login even if HR portal is down, but with limited info
        logger.warning("Employee %s not found in HR portal, allowing with limited info", req.employee_id)
        emp = {"employee_id": req.employee_id, "name": req.employee_id, "email": "", "role": "user"}

    name = emp.get("name", req.employee_id)
    email = emp.get("email", "")

    # Cache HR portal email in SQLite for future PR lookups
    from pr_lookup import get_cc_role_for_email, get_cc_role_for_employee_id, set_employee_email
    if email:
        set_employee_email(req.employee_id, email)

    # Determine CC role: manual SQLite override > PR department auto-map > generic
    manual_role = get_employee_role(req.employee_id)
    if manual_role:
        cc_role = manual_role
    else:
        # Try email-based lookup first, then employee_id fallback
        pr_role = get_cc_role_for_email(email) if email else None
        if pr_role is None:
            pr_role = get_cc_role_for_employee_id(req.employee_id)
        cc_role = pr_role or CCRole.generic.value

    # Create JWT
    token, expires_in = create_token(
        user_type=UserType.employee.value,
        user_id=req.employee_id,
        role=cc_role,
        name=name,
        email=email,
    )

    permissions = ROLE_PERMISSIONS.get(CCRole(cc_role), ROLE_PERMISSIONS[CCRole.generic])

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user={
            "user_type": "employee",
            "employee_id": req.employee_id,
            "name": name,
            "email": email,
            "cc_role": cc_role,
            "hr_role": emp.get("role", ""),
            "permissions": permissions,
        },
    )


# ---------------------------------------------------------------------------
# Account number helpers
# ---------------------------------------------------------------------------

# Canonical DB format: NNNNXXX  (e.g. 0045MAK)
_RE_DB_FMT = re.compile(r"^(\d{4})([A-Za-z]{3})$")       # 0045MAK
_RE_REVERSE_FMT = re.compile(r"^([A-Za-z]{3})(\d{4})$")   # MAK0045


def normalize_account_number(raw: str) -> str:
    """
    Normalise any common account-number format to the canonical NNNNXXX
    form stored in the ACCDB (e.g. '0045MAK').

    Accepts:
      0045MAK  -> 0045MAK  (already canonical)
      MAK0045  -> 0045MAK  (reversed)
      mak0045  -> 0045MAK  (case-insensitive)
    """
    raw = raw.strip()
    m = _RE_DB_FMT.match(raw)
    if m:
        return m.group(1) + m.group(2).upper()
    m = _RE_REVERSE_FMT.match(raw)
    if m:
        return m.group(2) + m.group(1).upper()
    # Not a recognised format — return as-is (will fail validation)
    return raw


# ---------------------------------------------------------------------------
# Customer login & registration
# ---------------------------------------------------------------------------

def _validate_customer_exists(customer_id: str) -> dict:
    """Check that a customer_id exists in the ACCDB tblcustomer. Returns customer data or raises 404."""
    from customer_api import get_connection, _row_to_dict, _normalize_customer

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?", (customer_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Customer ID '{customer_id}' not found")
            return _normalize_customer(_row_to_dict(cursor, row))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ACCDB lookup for customer %s failed: %s", customer_id, e)
        raise HTTPException(status_code=500, detail="Database error during customer validation")


def _validate_account_exists(account_number: str) -> dict:
    """
    Validate that an account number exists in tblaccounthistory1.
    Returns a dict with account_number, customer_id (if found), and name.
    """
    from customer_api import get_connection, _row_to_dict, _normalize_customer

    acct = normalize_account_number(account_number)

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Check account exists in transaction history
            cursor.execute(
                "SELECT TOP 1 [accountnumber], [meterid] "
                "FROM [tblaccounthistory1] WHERE [accountnumber] = ?",
                (acct,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Account '{acct}' not found. "
                           f"Enter your account number as it appears on your payment receipt (e.g. 0045MAK).",
                )

            result = {"account_number": acct, "customer_id": None, "name": acct}

            # Try to resolve to a tblcustomer record via Copy Of tblmeter
            try:
                cursor.execute(
                    "SELECT [customer id] FROM [Copy Of tblmeter] WHERE [accountnumber] = ?",
                    (acct,),
                )
                meter_row = cursor.fetchone()
                if meter_row and meter_row[0]:
                    cust_id = str(meter_row[0])
                    result["customer_id"] = cust_id
                    cursor.execute(
                        "SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?",
                        (cust_id,),
                    )
                    cust_row = cursor.fetchone()
                    if cust_row:
                        cust = _normalize_customer(_row_to_dict(cursor, cust_row))
                        fname = cust.get("first_name", "")
                        lname = cust.get("last_name", "")
                        result["name"] = f"{fname} {lname}".strip() or acct
                        result["customer"] = cust
            except Exception as e:
                logger.debug("Could not resolve account %s to customer: %s", acct, e)

            return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("ACCDB lookup for account %s failed: %s", account_number, e)
        raise HTTPException(status_code=500, detail="Database error during account validation")


@router.post("/customer-register")
def customer_register(req: CustomerRegisterRequest):
    """
    Register a customer account. Validates account number exists in ACCDB
    transaction history, then sets their password for future logins.
    The customer_id field is treated as an account number (e.g. 0045MAK or MAK0045).
    """
    acct = normalize_account_number(req.customer_id)

    # Check account exists in ACCDB
    info = _validate_account_exists(acct)

    # Check not already registered
    if customer_is_registered(acct):
        raise HTTPException(status_code=409, detail="Account already registered. Use login instead.")

    # Hash and store password (keyed by normalised account number)
    hashed = _bcrypt.hashpw(req.password.encode(), _bcrypt.gensalt()).decode()
    set_customer_password(acct, hashed)

    return {
        "message": "Registration successful. You can now log in.",
        "customer_id": acct,
        "name": info.get("name", acct),
    }


@router.post("/customer-login", response_model=TokenResponse)
def customer_login(req: CustomerLoginRequest):
    """Customer login with account number + password."""
    acct = normalize_account_number(req.customer_id)

    # Check registered
    stored_hash = get_customer_password_hash(acct)
    if not stored_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not registered. Please register first.",
        )

    # Verify password
    if not _bcrypt.checkpw(req.password.encode(), stored_hash.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Resolve name
    try:
        info = _validate_account_exists(acct)
        name = info.get("name", acct)
    except Exception:
        name = acct

    token, expires_in = create_token(
        user_type=UserType.customer.value,
        user_id=acct,
        role="customer",
        name=name,
        email="",
    )

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user={
            "user_type": "customer",
            "customer_id": acct,
            "name": name,
            "role": "customer",
        },
    )


@router.post("/customer-change-password")
def customer_change_password(
    req: CustomerChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Change customer password (requires current password)."""
    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Only customers can change their password here")

    stored_hash = get_customer_password_hash(user.user_id)
    if not stored_hash or not _bcrypt.checkpw(req.old_password.encode(), stored_hash.encode()):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    new_hash = _bcrypt.hashpw(req.new_password.encode(), _bcrypt.gensalt()).decode()
    set_customer_password(user.user_id, new_hash)
    return {"message": "Password changed successfully"}


# ---------------------------------------------------------------------------
# Current user info
# ---------------------------------------------------------------------------

@router.get("/me")
def get_me(user: CurrentUser = Depends(get_current_user)):
    """Return current user profile from JWT."""
    result = {
        "user_type": user.user_type.value,
        "user_id": user.user_id,
        "role": user.role,
        "name": user.name,
        "email": user.email,
        "permissions": user.permissions,
    }

    # If customer, also fetch their ACCDB record via account number
    if user.user_type == UserType.customer:
        try:
            info = _validate_account_exists(user.user_id)
            if "customer" in info:
                result["customer"] = info["customer"]
            result["account_number"] = info.get("account_number", user.user_id)
        except Exception:
            pass

    return result
