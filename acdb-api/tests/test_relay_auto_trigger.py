"""Country auto-cutoff switch: live config, scope, and mutation log."""

import importlib
import os
import sys
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

if "boto3" not in sys.modules:
    sys.modules["boto3"] = types.ModuleType("boto3")

if isinstance(sys.modules.get("relay_control"), types.SimpleNamespace):
    del sys.modules["relay_control"]
import customer_api  # noqa: E402,F401
rc = importlib.import_module("relay_control")

from fastapi import HTTPException  # noqa: E402

from models import CurrentUser, UserType  # noqa: E402


def _user(role, scope=None, roles=None):
    return CurrentUser(
        user_type=UserType.employee,
        user_id="emp-1",
        role=role,
        roles=roles if roles is not None else [role],
        name="Test User",
        scope_countries=list(scope or []),
    )


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "INSERT INTO system_config" in sql and params:
            self.row = (params[1],)

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, row):
        self.cur = FakeCursor(row)
        self.committed = False
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rollbacks += 1


@contextmanager
def _connect(conn):
    yield conn


class RelayAutoTriggerReaderTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("RELAY_AUTO_TRIGGER_ENABLED", None)
        os.environ.pop("RELAY_AUTO_TRIGGER_FORCE_OFF", None)

    def tearDown(self):
        self._env.stop()

    def test_missing_row_follows_env_default_off(self):
        self.assertFalse(rc.relay_auto_trigger_enabled(FakeConn(None)))

    def test_stored_on_ignores_env_off(self):
        self.assertTrue(rc.relay_auto_trigger_enabled(FakeConn(("1",))))

    def test_stored_off_ignores_env_on(self):
        os.environ["RELAY_AUTO_TRIGGER_ENABLED"] = "1"
        self.assertFalse(rc.relay_auto_trigger_enabled(FakeConn(("0",))))

    def test_missing_row_follows_env_on(self):
        os.environ["RELAY_AUTO_TRIGGER_ENABLED"] = "1"
        self.assertTrue(rc.relay_auto_trigger_enabled(FakeConn(None)))

    def test_force_off_beats_stored_on(self):
        os.environ["RELAY_AUTO_TRIGGER_FORCE_OFF"] = "1"
        state = rc.relay_auto_trigger_state(FakeConn(("1",)))
        self.assertTrue(state["force_off"])
        self.assertFalse(state["enabled"])

    def test_read_error_stays_off_and_releases_the_transaction(self):
        class Broken(FakeConn):
            def cursor(self):
                cur = super().cursor()

                def execute(sql, params=None):
                    cur.statements.append((sql, params))
                    if "SELECT value" in sql:
                        raise RuntimeError("db down")

                cur.execute = execute
                return cur

        conn = Broken(None)
        self.assertFalse(rc.relay_auto_trigger_enabled(conn))
        self.assertTrue(any("ROLLBACK TO SAVEPOINT" in sql for sql, _ in conn.cur.statements))

    def test_auto_open_is_a_noop_when_the_switch_is_off(self):
        self.assertIsNone(rc.maybe_auto_open_relay(FakeConn(None), "0003KOT"))


class RelayAutoTriggerScopeTests(unittest.TestCase):
    def test_superadmin_empty_scope_can_edit_any_lane(self):
        with mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN")):
            self.assertTrue(rc.user_may_edit_relay_auto_trigger(_user("superadmin", [])))

    def test_onm_benin_alias_can_edit_bn(self):
        with mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN")):
            self.assertTrue(rc.user_may_edit_relay_auto_trigger(_user("onm_team", ["BJ"])))

    def test_onm_other_country_cannot_edit(self):
        with mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN")):
            self.assertFalse(rc.user_may_edit_relay_auto_trigger(_user("onm_team", ["LS"])))

    def test_finance_empty_scope_cannot_edit(self):
        with mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN")):
            self.assertFalse(rc.user_may_edit_relay_auto_trigger(_user("finance_team", [])))

    def test_put_denies_out_of_country_before_writing(self):
        with mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN")):
            with self.assertRaises(HTTPException) as caught:
                rc.put_relay_auto_trigger(
                    rc.RelayAutoTriggerBody(enabled=True),
                    _user("onm_team", ["LS"]),
                )
        self.assertEqual(caught.exception.status_code, 403)


class RelayAutoTriggerMutationTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("RELAY_AUTO_TRIGGER_ENABLED", None)
        os.environ.pop("RELAY_AUTO_TRIGGER_FORCE_OFF", None)
        self.country = mock.patch.object(rc, "COUNTRY", SimpleNamespace(code="BN"))
        self.country.start()

    def tearDown(self):
        self.country.stop()
        self._env.stop()

    def _put(self, conn, enabled, user=None):
        user = user or _user("onm_team", ["BN"])
        with mock.patch.object(rc, "get_connection", return_value=_connect(conn)):
            with mock.patch.object(rc, "log_mutation", return_value=7) as log:
                result = rc.put_relay_auto_trigger(rc.RelayAutoTriggerBody(enabled=enabled), user)
        return result, log

    def test_change_writes_mutation_in_the_same_transaction(self):
        conn = FakeConn(None)
        result, log = self._put(conn, True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["enabled"])
        self.assertTrue(conn.committed)
        log.assert_called_once()
        kwargs = log.call_args.kwargs
        self.assertIs(kwargs["conn"], conn)
        self.assertEqual(kwargs["old_values"], {"enabled": False, "country": "BN"})
        self.assertEqual(kwargs["new_values"], {"enabled": True, "country": "BN"})
        self.assertEqual(log.call_args.args[1], "update")
        self.assertEqual(log.call_args.args[2], "system_config")
        self.assertEqual(log.call_args.args[3], "relay_auto_trigger_enabled")
        self.assertTrue(any("INSERT INTO system_config" in sql for sql, _ in conn.cur.statements))

    def test_unchanged_value_does_not_log(self):
        conn = FakeConn(("0",))
        result, log = self._put(conn, False)
        self.assertEqual(result["status"], "noop")
        self.assertFalse(conn.committed)
        log.assert_not_called()
        self.assertFalse(any("INSERT INTO system_config" in sql for sql, _ in conn.cur.statements))

    def test_failed_audit_rolls_back_the_save(self):
        conn = FakeConn(None)
        user = _user("superadmin", [])
        with mock.patch.object(rc, "get_connection", return_value=_connect(conn)):
            with mock.patch.object(rc, "log_mutation", side_effect=RuntimeError("audit down")):
                with self.assertRaises(HTTPException) as caught:
                    rc.put_relay_auto_trigger(rc.RelayAutoTriggerBody(enabled=True), user)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertFalse(conn.committed)
        self.assertGreater(conn.rollbacks, 0)

    def test_force_off_refuses_the_save(self):
        os.environ["RELAY_AUTO_TRIGGER_FORCE_OFF"] = "1"
        conn = FakeConn(("0",))
        with mock.patch.object(rc, "get_connection", return_value=_connect(conn)):
            with self.assertRaises(HTTPException) as caught:
                rc.put_relay_auto_trigger(
                    rc.RelayAutoTriggerBody(enabled=True),
                    _user("superadmin", []),
                )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertFalse(conn.committed)


if __name__ == "__main__":
    unittest.main()
