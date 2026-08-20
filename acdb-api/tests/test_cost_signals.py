"""Tests for the cost-signals driver endpoint."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")
os.environ["COST_SIGNALS_API_KEYS"] = "test-cost-key"

import country_config
import customer_api  # noqa: F401  (assemble app so router imports resolve)
import cost_signals


def _req(key: str = "test-cost-key"):
    return SimpleNamespace(headers={"X-API-Key": key})


class _Cursor:
    def __init__(self, plan):
        self._plan = plan
        self._rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self._rows = self._plan(normalized)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)


class _Conn:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _Cursor(self._plan)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _plan(sql):
    if "from customers" in sql:
        return [("MAK", None), ("MAK", "2026-01-01"), ("XXX", None)]
    if "from meters" in sql:
        return [(7,)]
    if "pg_database_size" in sql:
        return [(123456789,)]
    return []


class TestCostSignals(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(country_config, "COUNTRY", country_config.LESOTHO)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn_patcher = patch.object(cost_signals, "get_connection", lambda: _Conn(_plan))
        conn_patcher.start()
        self.addCleanup(conn_patcher.stop)
        sites_patcher = patch.object(cost_signals, "live_known_sites", lambda code=None: {"MAK"})
        sites_patcher.start()
        self.addCleanup(sites_patcher.stop)

    def test_returns_driver_values(self):
        body = cost_signals.cost_signals(_req())
        self.assertEqual(body["country_code"], "LS")
        # XXX is not a known site; one MAK customer is terminated
        self.assertEqual(body["active_customers"], 1)
        self.assertEqual(body["active_meters"], 7)
        self.assertEqual(body["db_size_bytes"], 123456789)
        self.assertIn("generated_at", body)

    def test_rejects_bad_key(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            cost_signals.cost_signals(_req("wrong"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_503_when_unconfigured(self):
        with patch.object(cost_signals, "_COST_SIGNAL_KEYS", []):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                cost_signals.cost_signals(_req())
            self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
