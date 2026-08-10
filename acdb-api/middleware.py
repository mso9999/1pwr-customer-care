"""
JWT auth middleware and role-based permission helpers.
"""

import os
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from models import CCRole, CurrentUser, ROLE_PERMISSIONS, TRANSACTION_TABLES, UserType

logger = logging.getLogger("acdb-api.middleware")

# ---------------------------------------------------------------------------
# JWT config
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("CC_JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("CC_JWT_SECRET environment variable is required — refusing to start with no secret (fail-closed).")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("CC_JWT_EXPIRY_HOURS", "8"))

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def create_token(
    user_type: str,
    user_id: str,
    role: str,
    name: str = "",
    email: str = "",
    roles: Optional[list[str]] = None,
    privilege_system: str = "",
    effective_privilege: Optional[dict] = None,
    privilege_version: str = "",
) -> tuple[str, int]:
    """Create a JWT. Returns (token_string, expires_in_seconds)."""
    expires = timedelta(hours=JWT_EXPIRY_HOURS)
    payload = {
        "sub": user_id,
        "user_type": user_type,
        "role": role,
        "roles": roles or [role],
        "name": name,
        "email": email,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + expires,
    }
    if privilege_system:
        payload["privilege_system"] = privilege_system
    if effective_privilege:
        payload["effective_privilege"] = effective_privilege
    if privilege_version:
        payload["privilege_version"] = privilege_version
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
    role_values = payload.get("roles")
    if not isinstance(role_values, list):
        role_values = [role_str]
    role_values = [str(value) for value in role_values if value]
    if role_str not in role_values:
        role_values.append(role_str)
    if payload.get("user_type") == "employee":
        permissions = dict(ROLE_PERMISSIONS[CCRole.generic])
        for value in role_values:
            try:
                cc_role = CCRole(value)
            except ValueError:
                continue
            for permission, allowed in ROLE_PERMISSIONS.get(cc_role, {}).items():
                permissions[permission] = permissions.get(permission, False) or bool(allowed)

    effective_privilege = payload.get("effective_privilege")
    if not isinstance(effective_privilege, dict):
        effective_privilege = {}
    actions = effective_privilege.get("actions")
    scope_countries = effective_privilege.get("scopeCountries")
    scope_organizations = effective_privilege.get("scopeOrganizations")
    role_crud_owners = effective_privilege.get("roleCrudOwners")

    return CurrentUser(
        user_type=UserType(payload.get("user_type", "customer")),
        user_id=payload.get("sub", ""),
        role=role_str,
        roles=role_values,
        name=payload.get("name", ""),
        email=payload.get("email", ""),
        permissions=permissions,
        privilege_system=str(payload.get("privilege_system") or ""),
        privilege_level=str(effective_privilege.get("level") or ""),
        privilege_actions=[str(value) for value in actions if value] if isinstance(actions, list) else [],
        privilege_version=str(payload.get("privilege_version") or ""),
        scope_countries=[str(value) for value in scope_countries if value] if isinstance(scope_countries, list) else [],
        scope_organizations=[str(value) for value in scope_organizations if value] if isinstance(scope_organizations, list) else [],
        role_crud_owners=[str(value) for value in role_crud_owners if value] if isinstance(role_crud_owners, list) else [],
    )


def require_employee(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Require the user to be an employee (any role)."""
    if user.user_type != UserType.employee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee access required")
    return user


def effective_roles(user: CurrentUser) -> list[str]:
    """Return all composed CC roles, preserving a stable display order."""
    values = user.roles if isinstance(user.roles, (list, tuple, set)) and user.roles else [user.role]
    return list(dict.fromkeys(str(value) for value in values if value))


def privilege_denial_detail(
    user: CurrentUser,
    required_roles: Iterable[str | CCRole],
    action: str = "perform this action",
) -> dict:
    """Machine-readable denial contract shared by every CC role gate."""
    assigned = effective_roles(user) or [CCRole.generic.value]
    required = list(dict.fromkeys(
        role.value if isinstance(role, CCRole) else str(role)
        for role in required_roles
    ))
    return {
        "code": "privilege_denied",
        "system": "cc",
        "action": action,
        "assigned_roles": assigned,
        "required_roles": required,
        "message": (
            f"Your assigned CC role(s) are {', '.join(assigned)}. "
            f"To {action}, you need one of: {', '.join(required)}."
        ),
        "role_crud_owners": [
            {
                "owner": "Your organization’s country HR team",
                "manages": "Primary/secondary department assignments, Lead status, and assignment scope in HR.",
            },
            {
                "owner": "Nexus/IS&T User Administrator",
                "manages": "Explicit Customer Care access or denial in Nexus, within access policy.",
            },
            {
                "owner": "Customer Care Superadmin",
                "manages": "Manual CC roles and protected local CC actions.",
            },
        ],
        "resolution": (
            "Ask the appropriate owner to correct the assignment, then sign out and back in "
            "so Customer Care receives a fresh privilege claim."
        ),
    }


def raise_privilege_denied(
    user: CurrentUser,
    required_roles: Iterable[str | CCRole],
    action: str = "perform this action",
) -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=privilege_denial_detail(user, required_roles, action),
    )


def _normalize_required_roles(roles) -> list[str]:
    normalized: list[str] = []
    for role in roles:
        if isinstance(role, (list, tuple, set)):
            normalized.extend(_normalize_required_roles(role))
        else:
            normalized.append(role.value if isinstance(role, CCRole) else str(role))
    return list(dict.fromkeys(normalized))


def require_role(*roles: CCRole, action: str = "use this area"):
    """Dependency factory: require user to have one of the specified CC roles."""
    def dependency(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
        required = set(_normalize_required_roles(roles))
        effective = set(effective_roles(user))
        if not effective.intersection(required):
            raise_privilege_denied(user, required, action)
        return user
    return dependency


def privilege_action_denial_detail(
    user: CurrentUser,
    action_key: str,
    action: str,
    required_level: str,
    system: str,
) -> dict:
    """Explain a denial against the signed Nexus action grant."""
    owner_labels = user.role_crud_owners or [
        "Your organization’s country HR team",
        "Nexus/IS&T User Administrator",
        f"{system.upper()} Superadmin",
    ]
    return {
        "code": "privilege_denied",
        "system": system,
        "action": action_key,
        "action_label": action,
        "assigned_level": user.privilege_level or "NONE",
        "required_level": required_level,
        "assigned_actions": user.privilege_actions,
        "privilege_version": user.privilege_version,
        "scope_countries": user.scope_countries,
        "scope_organizations": user.scope_organizations,
        "message": (
            f"Your signed {system.upper()} privilege is Level {user.privilege_level or 'NONE'}. "
            f"To {action}, Level {required_level} with action '{action_key}' is required."
        ),
        "role_crud_owners": [
            {"owner": label, "manages": "Role or department assignments that contribute to this Nexus privilege."}
            for label in owner_labels
        ],
        "resolution": (
            "Ask the appropriate owner to correct the assignment, then sign out and back in "
            "so this app receives a fresh signed Nexus privilege claim."
        ),
    }


def require_action(
    action_key: str,
    *,
    system: str,
    action: str,
    required_level: str,
    fallback_roles: Iterable[str | CCRole],
):
    """Require a signed Nexus action, with role fallback for emergency login.

    Nexus SSO sessions always carry ``privilege_version`` and are evaluated
    strictly against their target-system action list. The local role fallback
    exists only for the documented direct-login contingency path.
    """
    fallback = _normalize_required_roles(fallback_roles)

    def dependency(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
        if user.privilege_version:
            if user.privilege_system != system or action_key not in user.privilege_actions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=privilege_action_denial_detail(
                        user, action_key, action, required_level, system
                    ),
                )
            return user

        if not set(effective_roles(user)).intersection(fallback):
            raise_privilege_denied(user, fallback, action)
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
