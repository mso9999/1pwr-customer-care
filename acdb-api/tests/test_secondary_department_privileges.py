import os

os.environ.setdefault("CC_JWT_SECRET", "test-only-secret")

from models import CCRole, CurrentUser, UserType
from fastapi import HTTPException

from middleware import privilege_denial_detail, require_role
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
