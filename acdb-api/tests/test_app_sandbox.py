import unittest
from unittest.mock import patch

import app_sandbox
import app_bff


class TestSandbox(unittest.TestCase):
    def test_status_reports_disabled_by_default(self):
        with patch.dict("os.environ", {"APP_SANDBOX": "0"}):
            body = app_sandbox.sandbox_status()
        self.assertFalse(body["enabled"])
        self.assertIsNone(body["account_number"])

    def test_seed_404_when_disabled(self):
        with patch.dict("os.environ", {"APP_SANDBOX": "0"}):
            with self.assertRaises(Exception):
                app_sandbox.sandbox_seed(app_sandbox.SandboxSeedRequest())

    def test_seed_refuses_production_db(self):
        # Sandbox enabled but DATABASE_URL resolves to a prod DB name -> 400.
        with patch.dict(
            "os.environ",
            {"APP_SANDBOX": "1", "DATABASE_URL": "postgresql://cc_api@localhost:5432/onepower_cc"},
        ):
            with patch.object(app_sandbox, "_get_connection") as gc:
                with self.assertRaises(Exception) as ctx:
                    app_sandbox.sandbox_seed(app_sandbox.SandboxSeedRequest())
            self.assertEqual(ctx.exception.status_code, 400)
            gc.assert_not_called()

    def test_seed_allowed_against_sandbox_db(self):
        # Sandbox enabled + a non-prod DB name -> guard passes, seeding proceeds.
        created = {}

        class _Cur:
            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return None

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.dict(
            "os.environ",
            {"APP_SANDBOX": "1", "DATABASE_URL": "postgresql://cc_api@localhost:5432/onepower_cc_sandbox"},
        ):
            with patch.object(app_sandbox, "_get_connection", return_value=_Conn()):
                with patch.object(app_sandbox, "_ensure_dummy_customer", lambda conn: None):
                    with patch.object(app_sandbox, "_seed_payments", lambda conn, n, amount, rate: n):
                        body = app_sandbox.sandbox_seed(app_sandbox.SandboxSeedRequest())
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["payments_created"], 12)

    def test_seed_allow_prod_db_with_override(self):
        with patch.dict(
            "os.environ",
            {
                "APP_SANDBOX": "1",
                "APP_SANDBOX_ALLOW_PROD_DB": "1",
                "DATABASE_URL": "postgresql://cc_api@localhost:5432/onepower_cc",
            },
        ):
            with patch.object(app_sandbox, "_get_connection") as gc:
                with patch.object(app_sandbox, "_ensure_dummy_customer", lambda conn: None):
                    with patch.object(app_sandbox, "_seed_payments", lambda conn, n, amount, rate: n):
                        body = app_sandbox.sandbox_seed(app_sandbox.SandboxSeedRequest())
        self.assertEqual(body["status"], "ok")
        gc.assert_called_once()

    def test_auth_bridge_sandbox_shortcut_mints_token(self):
        class _FakeToken:
            def __call__(self, *a, **k):
                return ("tok", 3600)

        with patch.dict("os.environ", {"APP_SANDBOX": "1"}):
            with patch.object(app_bff, "_sandbox_enabled", return_value=True):
                with patch("middleware.create_token", side_effect=lambda *a, **k: ("tok", 3600)):
                    with patch("auth.normalize_account_number", return_value="0000SBX"):
                        req = app_bff.AppAuthRequest(client_code="0000SBX", pin="sandbox")
                        body = app_bff.app_auth_session(req)
        self.assertEqual(body["access_token"], "tok")
        self.assertTrue(body["sandbox"])
        self.assertEqual(body["client"]["code"], "0000SBX")

    def test_auth_bridge_ignores_sandbox_shortcut_when_disabled(self):
        # When sandbox disabled, pin=="sandbox" must NOT short-circuit; it
        # would fall through to the legacy proxy. We assert the shortcut
        # branch is not taken by ensuring the legacy proxy is invoked.
        called = {"proxy": False}

        def _fake_proxy(url, body, timeout=15.0):
            called["proxy"] = True
            return {"success": False}

        with patch.object(app_bff, "_sandbox_enabled", return_value=False):
            with patch.object(app_bff, "_proxy_json_post", side_effect=_fake_proxy):
                with patch("auth.normalize_account_number", return_value="0001SAM"):
                    req = app_bff.AppAuthRequest(client_code="0001SAM", pin="sandbox")
                    with self.assertRaises(Exception):
                        app_bff.app_auth_session(req)
        self.assertTrue(called["proxy"])


if __name__ == "__main__":
    unittest.main()
