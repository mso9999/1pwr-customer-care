"""Field registrar (committee login) store + registration gate tests."""
import os
from unittest.mock import patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import bcrypt
import pytest
from fastapi import HTTPException

import db_auth

# registration.py and customer_api.py import each other; loading the app
# entrypoint first resolves the cycle (same order as production uvicorn).
import customer_api  # noqa: F401
import registration

from models import CCRole, CurrentUser, UserType


@pytest.fixture()
def auth_db(tmp_path):
    db_path = tmp_path / "cc_auth.db"
    with patch.object(db_auth, "AUTH_DB_PATH", str(db_path)):
        db_auth.init_auth_db()
        yield db_path


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def test_registrar_crud_roundtrip(auth_db):
    db_auth.create_registrar(
        "mak-committee-1", _hash("secret1"), "Ha Makebe Committee", "MAK", "1PWR00"
    )

    record = db_auth.get_registrar("mak-committee-1")
    assert record is not None
    assert record["display_name"] == "Ha Makebe Committee"
    assert record["site_code"] == "MAK"
    assert record["active"] is True
    assert record["created_by"] == "1PWR00"
    assert "password_hash" not in record  # never leak hashes via the safe reader

    with_hash = db_auth.get_registrar("mak-committee-1", include_hash=True)
    assert bcrypt.checkpw(b"secret1", with_hash["password_hash"].encode())

    # Username lookup is case-insensitive
    assert db_auth.get_registrar("MAK-Committee-1") is not None

    listed = db_auth.list_registrars()
    assert [r["username"] for r in listed] == ["mak-committee-1"]

    assert db_auth.update_registrar("mak-committee-1", active=False)
    assert db_auth.get_registrar("mak-committee-1")["active"] is False

    assert db_auth.update_registrar("mak-committee-1", site_code="")  # unbind
    assert db_auth.get_registrar("mak-committee-1")["site_code"] is None

    assert db_auth.update_registrar("ghost-user", active=True) is False


def test_registrar_gate_admits_active_registrar(auth_db):
    db_auth.create_registrar("ket-1", _hash("pw12345"), "KET Committee", "KET", "admin")
    user = CurrentUser(
        user_type=UserType.registrar,
        user_id="ket-1",
        role=CCRole.field_registrar.value,
        name="KET Committee",
    )
    assert registration.require_registration_capable(user) is user
    assert registration._registrar_site_binding(user) == "KET"


def test_registrar_gate_rejects_deactivated_and_unknown(auth_db):
    db_auth.create_registrar("seh-1", _hash("pw12345"), "SEH Committee", None, "admin")
    db_auth.update_registrar("seh-1", active=False)

    deactivated = CurrentUser(
        user_type=UserType.registrar, user_id="seh-1",
        role=CCRole.field_registrar.value, name="SEH",
    )
    with pytest.raises(HTTPException) as exc:
        registration.require_registration_capable(deactivated)
    assert exc.value.status_code == 403

    unknown = CurrentUser(
        user_type=UserType.registrar, user_id="nobody",
        role=CCRole.field_registrar.value, name="?",
    )
    with pytest.raises(HTTPException):
        registration.require_registration_capable(unknown)


def test_registrar_gate_rejects_customers(auth_db):
    customer = CurrentUser(
        user_type=UserType.customer, user_id="0045MAK", role="customer", name="Cust",
    )
    with pytest.raises(HTTPException) as exc:
        registration.require_registration_capable(customer)
    assert exc.value.status_code == 403


def test_site_binding_none_for_employees(auth_db):
    employee = CurrentUser(
        user_type=UserType.employee, user_id="1PWR00",
        role=CCRole.onm_team.value, name="Staff",
    )
    assert registration._registrar_site_binding(employee) is None
