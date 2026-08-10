from unittest.mock import patch

import db_auth


def test_comfort_receives_country_operator_role_when_hr_membership_is_missing(tmp_path):
    auth_db = tmp_path / "cc_auth.db"

    with patch.object(db_auth, "AUTH_DB_PATH", str(auth_db)):
        db_auth.init_auth_db()
        with db_auth.get_auth_db() as conn:
            row = conn.execute(
                """SELECT cc_role, assigned_by
                     FROM cc_employee_roles
                    WHERE employee_id = ?""",
                ("1PWR0501",),
            ).fetchone()

    assert row["cc_role"] == "onm_team"
    assert row["assigned_by"] == "system:provisioning-operator"
