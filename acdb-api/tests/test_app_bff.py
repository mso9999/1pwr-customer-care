"""Tests for the mobile app BFF (`/api/app/*`)."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException, Response

import app_bff
from app_packs import build_pack, supported_codes
from country_config import BENIN, LESOTHO


def _call_active_countries() -> tuple[dict, Response]:
    """Invoke the route handler directly with a fresh ``Response`` object."""
    response = Response()
    body = app_bff.active_countries(response)
    return body, response


def _call_country_config(code: str) -> tuple[dict, Response]:
    response = Response()
    body = app_bff.country_config(response, code)
    return body, response


class TestActiveCountries(unittest.TestCase):
    def test_returns_both_active_countries_with_expected_shape(self):
        body, _ = _call_active_countries()
        self.assertIn("countries", body)
        countries = body["countries"]
        codes = sorted(row["countryCode"] for row in countries)
        self.assertEqual(codes, ["BN", "LS"])
        for row in countries:
            self.assertIn("countryCode", row)
            self.assertIn("displayName", row)
            self.assertIn("active", row)
            self.assertTrue(row["active"])
            self.assertIsInstance(row["displayName"], str)
            self.assertGreater(len(row["displayName"]), 0)
            # Both BN and LS now have remote packs, so appConfigUrl is present.
            self.assertIn("appConfigUrl", row)
            self.assertTrue(row["appConfigUrl"].startswith("https://"))

    def test_display_name_uses_field_then_falls_back_to_name(self):
        bn_no_display = replace(BENIN, display_name=None)
        with patch.object(
            app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": bn_no_display}
        ):
            body, _ = _call_active_countries()
        rows = {r["countryCode"]: r for r in body["countries"]}
        # LS keeps its display_name
        self.assertEqual(rows["LS"]["displayName"], LESOTHO.display_name)
        # BN falls back to name when display_name is None
        self.assertEqual(rows["BN"]["displayName"], BENIN.name)

    def test_inactive_country_is_filtered_out(self):
        bn_inactive = replace(BENIN, active=False)
        with patch.object(
            app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": bn_inactive}
        ):
            body, _ = _call_active_countries()
        codes = [r["countryCode"] for r in body["countries"]]
        self.assertEqual(codes, ["LS"])

    def test_appconfigurl_included_when_remote_pack_configured(self):
        remote_urls = {"BN": "https://example.com/pack/BN", "LS": None}
        with patch.object(app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": BENIN}):
            with patch.object(app_bff, "_REMOTE_CONFIG_URLS", remote_urls):
                body, _ = _call_active_countries()
        rows = {r["countryCode"]: r for r in body["countries"]}
        self.assertEqual(rows["BN"]["appConfigUrl"], remote_urls["BN"])
        # LS mapped to None => URL suppressed
        self.assertNotIn("appConfigUrl", rows["LS"])

    def test_appconfigurl_suppressed_for_code_without_pack(self):
        # A registered country with no app pack and no explicit URL omits appConfigUrl.
        with patch.object(app_bff, "_REMOTE_CONFIG_URLS", {"BN": "", "LS": ""}):
            with patch.object(app_bff, "supported_codes", lambda: []):
                body, _ = _call_active_countries()
        for row in body["countries"]:
            self.assertNotIn("appConfigUrl", row)

    def test_response_is_cacheable(self):
        _, response = _call_active_countries()
        cache_control = response.headers.get("cache-control", "")
        self.assertIn("max-age=300", cache_control)
        self.assertIn("public", cache_control)

    def test_router_prefix_and_route_registered(self):
        self.assertEqual(app_bff.router.prefix, "/api/app")
        paths = [route.path for route in app_bff.router.routes]
        self.assertIn("/api/app/active-countries", paths)
        self.assertIn("/api/app/country-config/{code}", paths)


class TestCountryConfig(unittest.TestCase):
    def test_supported_codes_include_bn_and_ls(self):
        codes = supported_codes()
        self.assertIn("BN", codes)
        self.assertIn("LS", codes)

    def test_build_pack_returns_none_for_unknown_code(self):
        self.assertIsNone(build_pack("ZZ"))
        self.assertIsNone(build_pack("zm"))  # ZM has no app pack

    def test_build_pack_bn_shape(self):
        pack = build_pack("BN")
        self.assertEqual(pack["countryCode"], "BN")
        self.assertEqual(pack["displayName"], "Bénin")
        self.assertEqual(pack["apiBaseUrl"], "https://app.onepowerbenin.com/api")
        self.assertEqual(pack["currencyCode"], "XOF")
        self.assertEqual(pack["appTitle"], "1PWR")
        # New provisioning fields
        self.assertEqual(pack["kwhDivisor"], BENIN.default_tariff_rate)
        self.assertEqual(pack["tariffRate"], BENIN.default_tariff_rate)
        self.assertEqual(pack["quickRechargeAmounts"], [1000.0, 2000.0, 5000.0])
        self.assertEqual(pack["fees"]["onboardingFee"], 10000.0)
        self.assertEqual(pack["fees"]["startingKitFee"], 40000.0)
        self.assertEqual(pack["fees"]["connectionFee"], BENIN.default_connection_fee)
        self.assertEqual(pack["fees"]["readyboardFee"], BENIN.default_readyboard_fee)
        # Providers include Orange Money with an apiMethod
        provider_ids = [p["id"] for p in pack["paymentProviders"]]
        self.assertIn("mtn_momo", provider_ids)
        self.assertIn("orange_money", provider_ids)
        mtn = next(p for p in pack["paymentProviders"] if p["id"] == "mtn_momo")
        self.assertEqual(mtn["apiMethod"], "MTN MoMo")
        # Zones carry code + name; each row has both fields and codes are unique
        zone_codes = [z["code"] for z in pack["zones"]]
        self.assertEqual(len(zone_codes), len(set(zone_codes)))
        for z in pack["zones"]:
            self.assertIn("code", z)
            self.assertIn("name", z)
            self.assertGreater(len(z["name"]), 0)
        self.assertIn("SAM", zone_codes)
        self.assertIn("GBO", zone_codes)
        # Meter LAN present for BN
        self.assertIsNotNone(pack["meterLan"])
        self.assertIn("1PWR", pack["meterLan"]["softApSsidPrefixes"])

    def test_build_pack_ls_disables_starting_kit(self):
        pack = build_pack("LS")
        self.assertFalse(pack["features"]["startingKit"])
        self.assertEqual(pack["currencyCode"], "LSL")
        self.assertEqual(pack["kwhDivisor"], LESOTHO.default_tariff_rate)

    def test_build_pack_lowercase_code_normalised(self):
        self.assertEqual(build_pack("bn")["countryCode"], "BN")

    def test_live_fee_override_from_system_config(self):
        class _FakeCursor:
            def __init__(self, rows):
                self._rows = dict(rows)

            def execute(self, sql, params):
                self._last = params[0]

            def fetchone(self):
                return (str(self._rows[self._last]),) if self._last in self._rows else None

        class _FakeConn:
            def __init__(self, rows):
                self._rows = rows

            def cursor(self):
                return _FakeCursor(self._rows)

        rows = {
            "onboarding_fee_amount": 12000.0,
            "starting_kit_fee_amount": 45000.0,
            "tariff_rate": 175.0,
        }
        pack = build_pack("BN", conn=_FakeConn(rows))
        self.assertEqual(pack["fees"]["onboardingFee"], 12000.0)
        self.assertEqual(pack["fees"]["startingKitFee"], 45000.0)
        self.assertEqual(pack["tariffRate"], 175.0)
        self.assertEqual(pack["kwhDivisor"], 175.0)

    def test_country_config_endpoint_serves_bn_pack_without_db(self):
        # Endpoint falls back to the static pack when the DB import/conn fails.
        with patch.object(app_bff, "build_pack", side_effect=lambda c, **kw: build_pack(c)):
            body, response = _call_country_config("BN")
        self.assertEqual(body["countryCode"], "BN")
        self.assertIn("max-age=300", response.headers.get("cache-control", ""))

    def test_country_config_endpoint_404_for_unknown_code(self):
        with self.assertRaises(HTTPException) as ctx:
            _call_country_config("ZZ")
        self.assertEqual(ctx.exception.status_code, 404)


class TestAuthBridge(unittest.TestCase):
    def test_auth_session_mints_jwt_on_pin_verify_success(self):
        from middleware import decode_token
        import auth

        with patch.object(app_bff, "_proxy_json_post", return_value={"success": True}):
            with patch.object(auth, "_validate_account_exists", side_effect=HTTPException(status_code=404)):
                with patch.object(app_bff, "COUNTRY") as country:
                    country.code = "BN"
                    body = app_bff.app_auth_session(
                        app_bff.AppAuthRequest(client_code="0001sam", pin="1234")
                    )
        self.assertIn("access_token", body)
        self.assertGreater(body["expires_in"], 0)
        # Token decodes and is scoped to the normalised account.
        payload = decode_token(body["access_token"])
        self.assertEqual(payload["user_type"], "customer")
        self.assertEqual(payload["role"], "customer")
        self.assertEqual(payload["sub"], "0001SAM")
        self.assertEqual(body["client"]["code"], "0001SAM")

    def test_auth_session_rejects_invalid_pin(self):
        with patch.object(app_bff, "_proxy_json_post", return_value={"success": False}):
            with patch.object(app_bff, "COUNTRY") as country:
                country.code = "BN"
                with self.assertRaises(HTTPException) as ctx:
                    app_bff.app_auth_session(
                        app_bff.AppAuthRequest(client_code="0001SAM", pin="0000")
                    )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_session_requires_fields(self):
        with self.assertRaises(HTTPException) as ctx:
            app_bff.app_auth_session(app_bff.AppAuthRequest(client_code="", pin="1234"))
        self.assertEqual(ctx.exception.status_code, 400)


class TestFeesAndDashboard(unittest.TestCase):
    def test_app_fees_builds_schedule_and_debt(self):
        class _Cur:
            def execute(self, sql, params=None):
                if "count" in sql:
                    pass
                if "system_config" in sql:
                    self._last = params[0]
                else:
                    self._last = params[0] if params else None

            def fetchone(self):
                # system_config lookups + financing lookup + fee_debt lookup
                return None

        class _Conn:
            def cursor(self):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        schedule = {
            "currency": "XOF",
            "tariff_rate": 160.0,
            "connection_fee_amount": 10000.0,
            "readyboard_fee_amount": 40000.0,
            "low_balance_kwh_threshold": 5.0,
            "low_balance_kwh_clear": 12.0,
        }
        with patch.object(app_bff, "COUNTRY") as country:
            country.code = "BN"
            country.currency = "XOF"
            country.default_tariff_rate = 160.0
            with patch("customer_api.get_connection", return_value=_Conn()):
                with patch("country_fees.get_country_fees", return_value=schedule):
                    with patch("fee_debt.get_customer_id_for_account", return_value=None):
                        user = _fake_customer("0001SAM")
                        response = Response()
                        body = app_bff.app_fees(response, user=user)
        self.assertEqual(body["currency"], "XOF")
        self.assertEqual(body["schedule"]["connection_fee"], 10000.0)
        self.assertEqual(body["fee_debt"]["total_remaining"], 0.0)
        self.assertEqual(body["split_policy"]["fee_cap_fraction"], 0.5)

    def test_app_dashboard_reuses_my_dashboard_and_augments(self):
        fake_dashboard = {"balance_kwh": 12.5, "daily_7d": [], "daily_30d": [], "monthly_12m": []}
        with patch.object(app_bff, "COUNTRY") as country:
            country.code = "BN"
            with patch("crud.my_dashboard", return_value=fake_dashboard):
                with patch.object(app_bff, "_fee_debt_snapshot", return_value={"total_remaining": 0.0}):
                    with patch.object(app_bff, "_financing_snapshot", return_value={"has_financing": False}):
                        user = _fake_customer("0001SAM")
                        body, _ = _call_with_user(app_bff.app_dashboard, user)
        self.assertEqual(body["balance_kwh"], 12.5)
        self.assertIn("fee_debt", body)
        self.assertIn("financing", body)


class TestCareMessaging(unittest.TestCase):
    """Phase 2: GET /api/app/care/threads + POST /api/app/care/messages."""

    def _conn_returning(self, rows, fetchone_values=None):
        class _Cur:
            def __init__(self):
                self.calls = []
                self._fone = list(fetchone_values or [])

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

            def fetchall(self):
                return list(rows)

            def fetchone(self):
                return self._fone.pop(0) if self._fone else None

        cur = _Cur()

        class _Conn:
            def cursor(self):
                return cur

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Conn(), cur

    def test_care_threads_lists_customer_messages(self):
        rows = [
            (1, "Hello", "billing", "app", "sent", None, __import__("datetime").datetime(2026, 7, 2)),
        ]
        conn, _ = self._conn_returning(rows, fetchone_values=[(1,)])
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_bff, "_ensure_care_table"):
                user = _fake_customer("0001SAM")
                response = Response()
                body = app_bff.app_care_threads(response, limit=50, offset=0, user=user)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["threads"][0]["id"], 1)
        self.assertEqual(body["threads"][0]["status"], "sent")

    def test_care_create_inserts_and_notifies_bridge(self):
        # fetchone sequence: idempotency check -> None (no dup), INSERT RETURNING -> (42,)
        conn, _ = self._conn_returning([], fetchone_values=[None, (42,)])
        with patch("country_config.COUNTRY") as country:
            country.code = "BN"
            with patch("customer_api.get_connection", return_value=conn):
                with patch.object(app_bff, "_ensure_care_table"):
                    with patch("cc_bridge_notify.notify_cc_bridge") as notify:
                        user = _fake_customer("0001SAM")
                        body = app_bff.AppCareMessageCreate(text="My meter is offline")
                        result = app_bff.app_care_create_message(body, x_idempotency_key="k1", user=user)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["id"], 42)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["country_code"], "BN")

    def test_care_create_dedup_on_idempotency_key(self):
        conn, _ = self._conn_returning([], fetchone_values=[(99,)])  # existing id
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_bff, "_ensure_care_table"):
                with patch("cc_bridge_notify.notify_cc_bridge") as notify:
                    user = _fake_customer("0001SAM")
                    body = app_bff.AppCareMessageCreate(text="dup")
                    result = app_bff.app_care_create_message(body, x_idempotency_key="dup-key", user=user)
        self.assertTrue(result["duplicate"])
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_customer(account_number: str):
    from models import CurrentUser, UserType

    return CurrentUser(user_type=UserType.customer, user_id=account_number, role="customer", name="Test")


def _call_with_user(handler, user):
    response = Response()
    body = handler(response, user=user)
    return body, response


if __name__ == "__main__":
    unittest.main()
