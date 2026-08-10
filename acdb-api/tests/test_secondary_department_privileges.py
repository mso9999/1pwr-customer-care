import os
import asyncio

os.environ.setdefault("CC_JWT_SECRET", "test-only-secret")

from models import CCRole, CurrentUser, UserType
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from middleware import create_token, decode_token, privilege_denial_detail, require_action, require_role
from auth import verify_auth
import hr_directory


def test_secondary_membership_contributes_cc_role(monkeypatch):
    mapping = {
        "finance": "finance_team",
        "exploitation et maintenance": "onm_team",
    }
    monkeypatch.setattr(
        hr_directory,
        "_map_department_to_role",
        lambda department: mapping.get(department.lower()),
    )

    roles = hr_directory._cc_roles_from_record({
        "department": "Finance",
        "memberships": [
            {"department_name": "Finance", "is_primary": True},
            {"department_name": "Exploitation et Maintenance", "is_primary": False},
        ],
    })

    assert roles == ["finance_team", "onm_team"]


def test_require_role_accepts_any_composed_role():
    user = CurrentUser(
        user_type=UserType.employee,
        user_id="1PWR0501",
        role="onm_team",
        roles=["onm_team", "finance_team"],
    )

    assert require_role(CCRole.finance_team)(user) is user
    assert require_role(CCRole.onm_team)(user) is user
    assert require_role(["superadmin", "onm_team"])(user) is user


def test_scalar_department_remains_backward_compatible(monkeypatch):
    monkeypatch.setattr(
        hr_directory,
        "_map_department_to_role",
        lambda department: "engineering" if department == "Engineering" else None,
    )

    assert hr_directory._cc_roles_from_record({"department": "Engineering"}) == ["engineering"]


def test_denial_explains_assigned_required_and_role_crud_owners():
    user = CurrentUser(
        user_type=UserType.employee,
        user_id="1PWR0501",
        role="generic",
        roles=["generic", "engineering"],
    )
    detail = privilege_denial_detail(user, [CCRole.onm_team, CCRole.superadmin], "assign a meter")

    assert detail["code"] == "privilege_denied"
    assert detail["assigned_roles"] == ["generic", "engineering"]
    assert detail["required_roles"] == ["onm_team", "superadmin"]
    assert {owner["owner"] for owner in detail["role_crud_owners"]} == {
        "Your organization’s country HR team",
        "Nexus/IS&T User Administrator",
        "Customer Care Superadmin",
    }

    try:
        require_role(CCRole.onm_team, action="assign a meter")(user)
        assert False, "expected a structured privilege denial"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail["action"] == "assign a meter"


def test_cc_jwt_preserves_signed_nexus_privilege():
    privilege = {
        "level": "C",
        "actions": ["view_customer_operations", "operate_customer_care"],
        "scopeCountries": ["BJ"],
        "scopeOrganizations": ["1PWR-BJ"],
        "roleCrudOwners": ["Benin HR team", "Nexus/IS&T User Administrator"],
    }
    token, _ = create_token(
        user_type="employee",
        user_id="1PWR0501",
        role="generic",
        privilege_system="cc",
        effective_privilege=privilege,
        privilege_version="2026.08.10.2",
    )

    payload = decode_token(token)
    assert payload["privilege_system"] == "cc"
    assert payload["effective_privilege"] == privilege
    assert payload["privilege_version"] == "2026.08.10.2"


def test_require_action_prefers_signed_nexus_grant_over_local_role():
    allowed = CurrentUser(
        user_type=UserType.employee,
        user_id="1PWR0501",
        role="generic",
        roles=["generic"],
        privilege_system="cc",
        privilege_level="C",
        privilege_actions=["operate_customer_care"],
        privilege_version="2026.08.10.2",
        role_crud_owners=["Benin HR team"],
    )
    gate = require_action(
        "operate_customer_care",
        system="cc",
        action="provision gateways",
        required_level="C",
        fallback_roles=[CCRole.superadmin, CCRole.onm_team],
    )
    assert gate(allowed) is allowed

    denied = allowed.model_copy(update={"privilege_actions": []})
    try:
        gate(denied)
        assert False, "expected signed action denial"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail["assigned_level"] == "C"
        assert exc.detail["required_level"] == "C"
        assert exc.detail["role_crud_owners"][0]["owner"] == "Benin HR team"


def test_require_action_keeps_emergency_role_login_compatible():
    legacy = CurrentUser(
        user_type=UserType.employee,
        user_id="1PWR0001",
        role="superadmin",
        roles=["superadmin"],
    )
    gate = require_action(
        "administer_cc",
        system="cc",
        action="manage CC roles",
        required_level="A",
        fallback_roles=[CCRole.superadmin],
    )
    assert gate(legacy) is legacy


def _auth_request(method: str = "", uri: str = "") -> Request:
    headers = []
    if method:
        headers.append((b"x-original-method", method.encode()))
    if uri:
        headers.append((b"x-original-uri", uri.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/auth/verify",
        "headers": headers,
    })


def _om_credentials(actions: list[str], level: str = "D") -> HTTPAuthorizationCredentials:
    token, _ = create_token(
        user_type="employee",
        user_id="1PWR0501",
        role="generic",
        privilege_system="om",
        effective_privilege={"level": level, "actions": actions},
        privilege_version="2026.08.10.2",
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_om_proxy_gate_uses_original_method_and_signed_action():
    view = _om_credentials(["view_tickets"])
    response = asyncio.run(verify_auth(
        _auth_request("GET", "/api/om/tickets"),
        view,
    ))
    assert response.status_code == 204

    try:
        asyncio.run(verify_auth(
            _auth_request("POST", "/api/om/tickets"),
            view,
        ))
        assert False, "expected O&M write denial"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail["action"] == "operate_tickets"

    operate = _om_credentials(["view_tickets", "operate_tickets"], "C")
    response = asyncio.run(verify_auth(
        _auth_request("PUT", "/api/om/tickets/OM-1"),
        operate,
    ))
    assert response.status_code == 204


def test_om_proxy_gate_fails_closed_without_original_request_headers():
    try:
        asyncio.run(verify_auth(_auth_request(), _om_credentials(["view_tickets"])))
        assert False, "expected proxy configuration denial"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "fail-closed" in str(exc.detail)
