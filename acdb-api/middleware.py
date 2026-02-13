"""
JWT auth middleware and role-based permission helpers.
"""

import os
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from models import CCRole, CurrentUser, ROLE_PERMISSIONS, TRANSACTION_TABLES, UserType

logger = logging.getLogger("acdb-api.middleware")

# ---------------------------------------------------------------------------
# JWT config
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("CC_JWT_SECRET", "cc-portal-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("CC_JWT_EXPIRY_HOURS", "8"))

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def create_token(user_type: str, user_id: str, role: str, name: str = "", email: str = "") -> tuple[str, int]:
    """Create a JWT. Returns (token_string, expires_in_seconds)."""
    expires = timedelta(hours=JWT_EXPIRY_HOURS)
    payload = {
        "sub": user_id,
        "user_type": user_type,
        "role": role,
        "name": name,
        "email": email,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + expires,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, int(expires.total_seconds())


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """Extract and validate JWT from Authorization header or cookie."""
    token = None

    # Try Bearer header first
    if credentials:
        token = credentials.credentials

    # Fallback: cookie
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(token)
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    role_str = payload.get("role", "generic")
    permissions = {}
    if payload.get("user_type") == "employee":
        try:
            cc_role = CCRole(role_str)
            permissions = ROLE_PERMISSIONS.get(cc_role, ROLE_PERMISSIONS[CCRole.generic])
        except ValueError:
            permissions = ROLE_PERMISSIONS[CCRole.generic]

    return CurrentUser(
        user_type=UserType(payload.get("user_type", "customer")),
        user_id=payload.get("sub", ""),
        role=role_str,
        name=payload.get("name", ""),
        email=payload.get("email", ""),
        permissions=permissions,
    )


def require_employee(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Require the user to be an employee (any role)."""
    if user.user_type != UserType.employee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee access required")
    return user


def require_role(*roles: CCRole):
    """Dependency factory: require user to have one of the specified CC roles."""
    def dependency(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
        if user.role not in [r.value for r in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {[r.value for r in roles]}",
            )
        return user
    return dependency


def can_write_table(user: CurrentUser, table_name: str) -> bool:
    """Check if user can write to a specific table."""
    if user.user_type != UserType.employee:
        return False
    perms = user.permissions
    if perms.get("write_customers"):
        return True  # superadmin / onm can write anything
    if perms.get("write_transactions") and table_name.lower() in TRANSACTION_TABLES:
        return True  # finance can write transaction tables
    return False
