"""
Authentication endpoints for the Customer Care Portal.

Two login modes:
  - Employee: employee_id + date-based password, name from HR portal
  - Customer: account_number + self-set password, data from the CC database

Customer identity:
  Customers identify by their **account number** (e.g. 0045MAK), which is the
  same identifier they use when making mobile money payments. The format is
  4 digits + 3-letter site code (NNNNXXX). We also accept the reversed format
  (XXXNNNN) and normalise to NNNNXXX for storage.
"""

import re
import logging
from datetime import datetime
from typing import Optional

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

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
from middleware import create_token, get_current_user, require_employee
from db_auth import (
    customer_is_registered,
    get_customer_password_hash,
    get_employee_role,
    get_whats_new_seen,
    mark_whats_new_seen,
    set_customer_password,
)
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# HR Portal integration
# ---------------------------------------------------------------------------
# Employee records + department associations are sourced from the HR portal via
# hr_directory (canonical). Sign-in validation uses HR /lookup; department is
# resolved from the HR directory cache. See hr_directory.py for config/env vars.

def lookup_employee(employee_id: str) -> Optional[dict]:
    """
    Minimal sign-in lookup in the HR portal by employee_id.
    Returns dict with employee_id, name, email, role or None.
    Accepts bare numbers (e.g. "137") -- the HR portal normalises to "1PWR137".
    """
    from hr_directory import lookup_employee_minimal
    return lookup_employee_minimal(employee_id)


# ---------------------------------------------------------------------------
# Date-based password (monthly staff PIN, defense-in-depth on top of HR auth)
# ---------------------------------------------------------------------------

def date_password_for(year: int, month: int) -> str:
    """Return the staff PIN for an arbitrary ``(year, month)``.

    Formula: ``int(YYYYMM) / int(reverse(YYYYMM))``, take the first 4
    significant digits. Pure function -- exposed so we can compute next
    month's PIN for the broadcast and unit-test fixed values.
    """
    yyyymm = f"{year:04d}{month:02d}"
    reversed_str = yyyymm[::-1]
    numerator = int(yyyymm)
    denominator = int(reversed_str)
    if denominator == 0:
        return "0000"
    result = numerator / denominator
    result_str = f"{result:.10f}".replace(".", "").lstrip("0")
    return result_str[:4] if len(result_str) >= 4 else result_str.ljust(4, "0")


def generate_date_password() -> str:
    """
    Compute the date-based password for the current month (UTC).
    Thin wrapper around :func:`date_password_for` kept for callers / tests.
    """
    now = datetime.utcnow()
    return date_password_for(now.year, now.month)


# ---------------------------------------------------------------------------
# Employee login
# ---------------------------------------------------------------------------

def _employee_token_response(employee_id: str, name: str, email: str, hr_role: str = "") -> TokenResponse:
    """
    Issue the CC employee JWT + user payload for an already-authenticated
    employee. Shared by employee-login (PIN) and the Nexus SSO receiver —
    role/department resolution is identical for both paths.
    """
    # Cache HR email in SQLite (diagnostic continuity; HR is keyed by employee_id)
    from pr_lookup import (
        get_cc_role_for_email,
        get_cc_role_for_employee_id,
        get_department_for_email,
        get_department_for_employee_id,
        set_employee_email,
    )
    if email:
        set_employee_email(employee_id, email)

    # Determine CC role: manual SQLite override > HR department auto-map > generic
    manual_role = get_employee_role(employee_id)
    if manual_role:
        cc_role = manual_role
    else:
        # Try email-based lookup first, then employee_id fallback
        hr_role_mapped = get_cc_role_for_email(email) if email else None
        if hr_role_mapped is None:
            hr_role_mapped = get_cc_role_for_employee_id(employee_id)
        cc_role = hr_role_mapped or CCRole.generic.value

    # Readable HR department affiliation (for display/self-diagnosis in CC).
    # Shown regardless of whether it maps to a role, so staff can see what HR
    # has them as when their access is wrong.
    department = (get_department_for_email(email) if email else None) \
        or get_department_for_employee_id(employee_id) or ""

    # Create JWT
    token, expires_in = create_token(
        user_type=UserType.employee.value,
        user_id=employee_id,
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
            "employee_id": employee_id,
            "name": name,
            "email": email,
            "cc_role": cc_role,
            "hr_role": hr_role,
            "department": department,
            "permissions": permissions,
        },
    )


@router.post("/employee-login", response_model=TokenResponse)
def employee_login(req: EmployeeLoginRequest):
    """
    Employee login with employee_id + date-based password.
    Cross-references HR portal for name/email.
    """
    # Validate date-based password (the monthly staff PIN -- defense-in-depth
    # gate on top of HR-portal validation; rotates at 00:00 UTC on the 1st of
    # every month, see ``date_password_for``).
    expected_password = generate_date_password()
    if req.password != expected_password:
        # If we're in the first week of a month, the most likely cause is
        # that the PIN just rotated and the staff member is still using last
        # month's value. Surface the actionable hint -- without leaking the
        # actual PIN.
        from auth_pin_broadcast import is_first_week_of_month
        if is_first_week_of_month():
            detail = (
                "Invalid PIN. The 1PWR staff PIN rotates on the 1st of every "
                "month -- check the Customer Care WhatsApp group for this "
                f"month's PIN, or ask your manager."
            )
        else:
            detail = "Invalid credentials"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )

    # Look up employee in HR portal
    emp = lookup_employee(req.employee_id)
    if not emp:
        # Allow login even if HR portal is down, but with limited info
        logger.warning("Employee %s not found in HR portal, allowing with limited info", req.employee_id)
        emp = {"employee_id": req.employee_id, "name": req.employee_id, "email": "", "role": "user"}

    return _employee_token_response(
        employee_id=req.employee_id,
        name=emp.get("name", req.employee_id),
        email=emp.get("email", ""),
        hr_role=emp.get("role", ""),
    )


# ---------------------------------------------------------------------------
# Nexus SSO receiver
# ---------------------------------------------------------------------------
# Nexus (nexus.1pwrafrica.com) is the central IdP. Its /sso/authorize flow
# redirects the browser to the CC frontend /auth/sso route with a Firebase
# custom token (?sso_token=), which the frontend POSTs here. We verify the
# token offline against Google's x509 certs for the pr-system-4ea55 service
# account and issue the normal CC employee JWT. The monthly staff PIN is
# enforced by Nexus before it mints the token (pinRequired on the cc tool).

NEXUS_SA_EMAIL = "firebase-adminsdk-f3uff@pr-system-4ea55.iam.gserviceaccount.com"
NEXUS_CERT_URL = (
    "https://www.googleapis.com/service_accounts/v1/metadata/x509/" + NEXUS_SA_EMAIL
)
# Fixed audience of all Firebase custom tokens (not project-specific).
NEXUS_EXPECTED_AUD = (
    "https://identitytoolkit.googleapis.com/"
    "google.identity.identitytoolkit.v1.IdentityToolkit"
)
_nexus_certs_cache: dict = {"fetched_at": 0.0, "certs": {}}


def _fetch_nexus_signing_certs() -> dict:
    """Fetch + cache (1h) Google's x509 certs for the Nexus service account."""
    import time

    import requests as _requests

    now = time.time()
    if _nexus_certs_cache["certs"] and now - _nexus_certs_cache["fetched_at"] < 3600:
        return _nexus_certs_cache["certs"]
    try:
        resp = _requests.get(NEXUS_CERT_URL, timeout=10)
        resp.raise_for_status()
        certs = resp.json()
        if isinstance(certs, dict) and certs:
            _nexus_certs_cache["certs"] = certs
            _nexus_certs_cache["fetched_at"] = now
    except Exception as exc:  # keep stale cache on network failure
        logger.warning("Nexus SSO: could not refresh Google certs: %s", exc)
    return _nexus_certs_cache["certs"]


def _verify_nexus_custom_token(token: str) -> dict:
    """
    Verify a Firebase custom token minted by Nexus. Returns the decoded
    payload (with ``uid`` and nested ``claims``). Raises HTTPException(401)
    on any validation failure.

    Custom tokens carry no ``kid`` header, so we try each Google cert until
    one validates the RS256 signature (same approach as the HR receiver).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509 import load_pem_x509_certificate
    from jose import jwt as jose_jwt

    certs = _fetch_nexus_signing_certs()
    if not certs:
        raise HTTPException(status_code=503, detail="SSO signing certs unavailable. Try again.")

    payload = None
    for pem in certs.values():
        try:
            cert = load_pem_x509_certificate(pem.encode())
            public_pem = cert.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            payload = jose_jwt.decode(
                token,
                public_pem.decode(),
                algorithms=["RS256"],
                audience=NEXUS_EXPECTED_AUD,
            )
            break
        except Exception:
            continue

    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired SSO token.")
    if payload.get("iss") != NEXUS_SA_EMAIL:
        raise HTTPException(status_code=401, detail="Invalid SSO token issuer.")
    return payload


class NexusSsoRequest(BaseModel):
    sso_token: str


@router.post("/sso", response_model=TokenResponse)
def nexus_sso_login(req: NexusSsoRequest):
    """
    Exchange a Nexus-minted Firebase custom token for a CC employee JWT.
    The Nexus identity's email is matched against the HR directory to find
    the employee_id; role/department resolution is then identical to
    employee-login.
    """
    payload = _verify_nexus_custom_token(req.sso_token)

    claims = payload.get("claims") or {}
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="SSO token has no email identity.")

    from hr_directory import get_employee_by_email

    emp = get_employee_by_email(email)
    if not emp or not emp.get("employee_id"):
        logger.warning("Nexus SSO: no HR employee for email=%s", email)
        raise HTTPException(
            status_code=403,
            detail="No 1PWR staff record is linked to your Nexus account. Contact IT.",
        )

    logger.info("Nexus SSO: employee %s (%s) signed in", emp["employee_id"], email)
    return _employee_token_response(
        employee_id=str(emp["employee_id"]),
        name=str(emp.get("name") or email),
        email=email,
        hr_role=str(emp.get("role") or ""),
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
    form stored in the CC database (e.g. '0045MAK').

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
    """Check that a customer_id exists in the customers table. Returns customer data or raises 404."""
    from customer_api import get_connection, _row_to_dict, _normalize_customer

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM customers WHERE customer_id_legacy = %s", (int(customer_id),))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Customer ID '{customer_id}' not found")
            return _normalize_customer(_row_to_dict(cursor, row))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Customer lookup for %s failed: %s", customer_id, e)
        raise HTTPException(status_code=500, detail="Database error during customer validation")


def _validate_account_exists(account_number: str) -> dict:
    """
    Validate that an account number exists in the database.
    Returns a dict with account_number, customer_id (if found), and name.
    """
    from customer_api import get_connection, _row_to_dict, _normalize_customer

    acct = normalize_account_number(account_number)

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Check account exists in transactions or accounts table
            cursor.execute(
                "SELECT account_number, meter_id "
                "FROM transactions WHERE account_number = %s LIMIT 1",
                (acct,),
            )
            row = cursor.fetchone()
            if not row:
                # Also try the accounts table
                cursor.execute(
                    "SELECT account_number FROM accounts WHERE account_number = %s",
                    (acct,),
                )
                row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Account '{acct}' not found. "
                           f"Enter your account number as it appears on your payment receipt (e.g. 0045MAK).",
                )

            result = {"account_number": acct, "customer_id_legacy": None, "name": acct}

            # Resolve to a customer record via accounts table
            try:
                cursor.execute(
                    "SELECT c.* FROM accounts a "
                    "JOIN customers c ON a.customer_id = c.id "
                    "WHERE a.account_number = %s LIMIT 1",
                    (acct,),
                )
                cust_row = cursor.fetchone()
                if cust_row:
                    cust = _normalize_customer(_row_to_dict(cursor, cust_row))
                    result["customer_id_legacy"] = cust.get("customer_id_legacy")
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
        logger.error("CC database lookup for account %s failed: %s", account_number, e)
        raise HTTPException(status_code=500, detail="Database error during account validation")


@router.post("/customer-register")
def customer_register(req: CustomerRegisterRequest):
    """
    Register a customer account. Validates account number exists in CC
    transaction history, then sets their password for future logins.
    The customer_id field is treated as an account number (e.g. 0045MAK or MAK0045).
    """
    acct = normalize_account_number(req.customer_id)

    # Check account exists in the CC database
    info = _validate_account_exists(acct)

    # Check not already registered
    if customer_is_registered(acct):
        raise HTTPException(status_code=409, detail="Account already registered. Use login instead.")

    # Hash and store password (keyed by normalised account number)
    hashed = _bcrypt.hashpw(req.password.encode(), _bcrypt.gensalt()).decode()
    set_customer_password(acct, hashed)
    try_log_mutation(
        CurrentUser(
            user_type=UserType.customer,
            user_id=acct,
            role="customer",
            name=info.get("name", acct),
        ),
        "password_registered",
        "cc_customer_passwords",
        acct,
        new_values={"password_state": "registered"},
        metadata={"origin": "customer_self_service"},
    )

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
    try_log_mutation(
        user,
        "password_changed",
        "cc_customer_passwords",
        user.user_id,
        old_values={"password_state": "registered"},
        new_values={"password_state": "changed"},
        metadata={"origin": "customer_self_service"},
    )
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

    # Employees: re-derive HR department from the cached email/id so it survives
    # token refreshes (it isn't stored in the JWT). Best-effort. Also surface the
    # What's-new seen-at so the frontend can prime the login popup only when
    # there are unseen feature updates.
    if user.user_type == UserType.employee:
        try:
            from pr_lookup import get_department_for_email, get_department_for_employee_id
            dept = (get_department_for_email(user.email) if user.email else None) \
                or get_department_for_employee_id(user.user_id)
            result["department"] = dept or ""
        except Exception:
            result["department"] = ""
        try:
            result["whats_new_seen_at"] = get_whats_new_seen(user.user_id)
        except Exception:
            result["whats_new_seen_at"] = None

    # If customer, also fetch their CC record via account number
    if user.user_type == UserType.customer:
        try:
            info = _validate_account_exists(user.user_id)
            if "customer" in info:
                result["customer"] = info["customer"]
            result["account_number"] = info.get("account_number", user.user_id)
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# What's new primer — mark seen
# ---------------------------------------------------------------------------

@router.post("/whats-new/seen")
def mark_whats_new_seen_endpoint(
    user: CurrentUser = Depends(require_employee),
):
    """Acknowledge that the employee has seen the What's new primer through now.
    Suppresses the popup for entries already shipped at/ before this moment."""
    seen_at = datetime.utcnow().isoformat()
    mark_whats_new_seen(user.user_id, seen_at)
    return {"seen_at": seen_at}
