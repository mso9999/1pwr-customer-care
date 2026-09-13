"""Unit tests for the uGridPREDICT integration-key forecast series."""
import os
import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")
os.environ["CC_INTEGRATION_KEY"] = "forecast-test-key"

# integration.py / integration_forecast.py only need get_connection as a name.
# Stub it so CI/dev shells do not import the full FastAPI app (boto3, xhtml2pdf).
_stub = types.ModuleType("customer_api")

def _stub_get_connection():  # pragma: no cover
    raise RuntimeError("customer_api.get_connection should be patched in tests")

_stub.get_connection = _stub_get_connection  # type: ignore[attr-defined]
sys.modules.setdefault("customer_api", _stub)

import country_config
import integration
import integration_forecast as forecast


class MonthHelperTests(unittest.TestCase):
    def test_parse_year_month(self):
        self.assertEqual(forecast.parse_year_month("2026-08", "date_from"), date(2026, 8, 1))

    def test_parse_rejects_bad(self):
        with self.assertRaises(HTTPException) as ctx:
            forecast.parse_year_month("2026-13", "date_from")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_default_window_is_36_months(self):
        start, end = forecast.month_window(None, None, today=date(2026, 9, 13))
        self.assertEqual(end, date(2026, 9, 1))
        self.assertEqual(start, date(2023, 10, 1))

    def test_window_order(self):
        with self.assertRaises(HTTPException) as ctx:
            forecast.month_window("2026-09", "2026-01")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_site_must_be_three_letters(self):
        self.assertEqual(forecast.normalize_site("mas"), "MAS")
        self.assertIsNone(forecast.normalize_site("ALL"))
        with self.assertRaises(HTTPException):
            forecast.normalize_site("MASHAI")


class SeriesMathTests(unittest.TestCase):
    def test_kwh_per_connection_uses_commissioned_stock(self):
        rows = forecast.attach_kwh_per_connection(
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "HH",
                    "customer_commissioned": 100,
                }
            ],
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "HH",
                    "kwh": 250.0,
                }
            ],
        )
        self.assertEqual(rows[0]["kwh_per_connection"], 2.5)

    def test_kwh_per_connection_null_when_stock_zero(self):
        rows = forecast.attach_kwh_per_connection(
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "SME",
                    "customer_commissioned": 0,
                }
            ],
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "SME",
                    "kwh": 12.0,
                }
            ],
        )
        self.assertIsNone(rows[0]["kwh_per_connection"])
        self.assertEqual(rows[0]["kwh"], 12.0)

    def test_prepaid_billed_equals_collected_plus_invoiced(self):
        rows = forecast.merge_collections(
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "HH",
                    "collected_local": 1000.0,
                }
            ],
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_class": "HH",
                    "billed_local": 80.0,
                    "collected_local": 50.0,
                }
            ],
        )
        self.assertEqual(rows[0]["billed_local"], 1080.0)
        self.assertEqual(rows[0]["collected_local"], 1050.0)
        self.assertNotIn("arpu", rows[0])

    def test_month_totals_weighted_kwh_per_connection(self):
        totals = forecast.month_totals(
            [
                {
                    "year_month": "2026-08",
                    "site_code": "MAS",
                    "customer_commissioned": 10,
                    "kwh": 40,
                },
                {
                    "year_month": "2026-08",
                    "site_code": "MAK",
                    "customer_commissioned": 30,
                    "kwh": 60,
                },
            ],
            ("customer_commissioned", "kwh"),
        )
        self.assertEqual(totals[0]["customer_commissioned"], 40)
        self.assertEqual(totals[0]["kwh"], 100)
        self.assertEqual(totals[0]["kwh_per_connection"], 2.5)


class _Cursor:
    def __init__(self, plan):
        self._plan = plan
        self._rows = []
        self.description = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self._rows, self.description = self._plan(normalized, params)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else (None,)


class _Conn:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _Cursor(self._plan)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stock_plan(sql, params):
    if "from months" in sql or "generate_series" in sql:
        return (
            [("2026-08", "MAS", "HH", 1388, 0, 1500)],
            [
                ("year_month",),
                ("site_code",),
                ("customer_class",),
                ("customer_commissioned",),
                ("commissioned_missing_date",),
                ("service_connected",),
            ],
        )
    return [], []


class AuthAndRouteTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(country_config, "COUNTRY", country_config.LESOTHO)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn_patcher = patch.object(
            forecast, "get_connection", lambda: _Conn(_stock_plan)
        )
        conn_patcher.start()
        self.addCleanup(conn_patcher.stop)

    def test_rejects_bad_key(self):
        with self.assertRaises(HTTPException) as ctx:
            forecast.forecast_connections(
                site="MAS",
                date_from="2026-08",
                date_to="2026-08",
                x_cc_integration_key="wrong",
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_connections_uses_customer_commissioned(self):
        body = forecast.forecast_connections(
            site="MAS",
            date_from="2026-08",
            date_to="2026-08",
            x_cc_integration_key="forecast-test-key",
        )
        self.assertEqual(body["measure"], "customer_commissioned")
        self.assertEqual(body["join_key"], "site_code")
        self.assertEqual(body["rows"][0]["customer_commissioned"], 1388)
        self.assertEqual(body["rows"][0]["service_connected"], 1500)
        self.assertIn("service_connected", body["definitions"])
        self.assertNotIn("arpu", body)

    def test_existing_om_overview_payload_untouched(self):
        """The new module must not change the older integration contract."""
        self.assertTrue(hasattr(integration, "integration_overview"))
        self.assertTrue(forecast.router.prefix.startswith("/api/integration/forecast"))


def _consumption_plan(sql, params):
    if "from monthly_consumption" in sql:
        return ([("2026-08", "MAS", "HH", 250.0)], [])
    if "from months" in sql or "generate_series" in sql:
        return (
            [("2026-08", "MAS", "HH", 100, 0, 120)],
            [
                ("year_month",),
                ("site_code",),
                ("customer_class",),
                ("customer_commissioned",),
                ("commissioned_missing_date",),
                ("service_connected",),
            ],
        )
    return [], []


def _collections_plan(sql, params):
    if "to_regclass" in sql:
        return ([(None,)], [])
    if "from monthly_transactions" in sql:
        return ([("2026-08", "MAS", "HH", 1000.0)], [])
    return [], []


class ConsumptionAndCollectionsRouteTests(unittest.TestCase):
    def test_consumption_denominator_is_commissioned(self):
        with patch.object(country_config, "COUNTRY", country_config.LESOTHO):
            with patch.object(forecast, "get_connection", lambda: _Conn(_consumption_plan)):
                body = forecast.forecast_consumption(
                    site="MAS",
                    date_from="2026-08",
                    date_to="2026-08",
                    x_cc_integration_key="forecast-test-key",
                )
        self.assertEqual(body["rows"][0]["kwh_per_connection"], 2.5)
        self.assertNotIn("arpu", body)

    def test_collections_omits_arpu(self):
        with patch.object(country_config, "COUNTRY", country_config.LESOTHO):
            with patch.object(forecast, "get_connection", lambda: _Conn(_collections_plan)):
                body = forecast.forecast_collections(
                    site="MAS",
                    date_from="2026-08",
                    date_to="2026-08",
                    x_cc_integration_key="forecast-test-key",
                )
        self.assertEqual(body["arpu"], None)
        self.assertEqual(body["rows"][0]["billed_local"], 1000.0)
        self.assertEqual(body["rows"][0]["collected_local"], 1000.0)


class DisabledKeyTests(unittest.TestCase):
    def test_unset_key_is_503(self):
        with patch.dict(os.environ, {"CC_INTEGRATION_KEY": ""}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                integration._require_key("anything")
            self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
