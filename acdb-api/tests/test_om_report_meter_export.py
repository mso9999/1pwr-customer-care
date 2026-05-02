"""
Regression test for the /api/om-report/meter-export dedup bug.

RCA (2026-05-01): the previous implementation tracked
``raw_accounts_covered: Set[account_number]`` from the ``meter_readings``
pull, then in the ``hourly_consumption`` fallback skipped EVERY hourly row
for any account that ever appeared in ``meter_readings`` -- regardless of
the time bucket. The result was that any account with even one
``meter_readings`` row had its entire ``hourly_consumption`` history
hidden.

Concrete production impact: 201 MAK accounts had ~83K rows of January 2026
hourly data shadowed by Feb-2026-onwards ``meter_readings`` rows (live-feed
import (`import_tc_live.py`) only started populating
``meter_readings_2026`` from 2026-02-17). The "January 2026 import gap"
documented in ``docs/ops/jan-2026-thundercloud-import-gap.md`` was
actually this dedup bug, not a missing import.

The fix: dedup at ``(account_number, hour_bucket)`` granularity so an
account's ``hourly_consumption`` rows are only shadowed for hours that
``meter_readings`` actually covers. This file pins the new behaviour.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


def _install_stubs():
    if "customer_api" not in sys.modules:
        m = types.ModuleType("customer_api")
        m.get_connection = lambda: None  # type: ignore[attr-defined]
        m._row_to_dict = lambda *a, **k: {}  # type: ignore[attr-defined]
        m._normalize_customer = lambda d: d  # type: ignore[attr-defined]
        sys.modules["customer_api"] = m


_install_stubs()

import om_report  # noqa: E402  (after stubs)


def _make_cursor(query_responses):
    """Return a cursor MagicMock whose .execute(sql, params) records the
    SQL fragment, .fetchall() returns the next pre-canned result list, and
    .description returns column descriptors compatible with the prod code.

    ``query_responses`` is a list of dicts:
        {"match": "<substring of SQL>", "rows": [...], "description": [...]}
    matched in order. A non-matching execute() returns the empty fixture.
    """
    cur = MagicMock()
    state = {"i": 0, "current": None}

    def execute(sql, params=None):
        # Match the next response whose ``match`` substring is in sql.
        for j in range(state["i"], len(query_responses)):
            if query_responses[j]["match"] in sql:
                state["current"] = query_responses[j]
                state["i"] = j + 1
                return None
        state["current"] = {"rows": [], "description": []}
        return None

    def fetchall():
        return list(state["current"]["rows"]) if state["current"] else []

    cur.execute.side_effect = execute
    cur.fetchall.side_effect = fetchall
    type(cur).description = property(
        lambda self: state["current"]["description"] if state["current"] else []
    )
    return cur


def _fake_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor

    class _CtxMgr:
        def __enter__(self_inner):
            return conn

        def __exit__(self_inner, *a):
            return False

    return _CtxMgr()


def _stub_user(monkeypatched_module):
    """Bypass the ``require_employee`` dependency by stubbing the module
    function the route resolves it through.
    """
    return MagicMock(user_type="employee", user_id="test", role="superadmin", name="t", email="")


class MeterExportDedupTests(unittest.TestCase):
    def setUp(self):
        # Common fixture: 1 account in MAK with type "HH" and 1 active meter.
        self.acct = "0001MAK"
        self.meter_id = "SMRSD-04-00000001"
        # meter_readings rows ONLY exist for 2026-02-17 onwards (mirrors prod).
        # hourly_consumption has rows for 2026-01 (the deficit window) AND 2026-02.
        self.meter_readings_rows = [
            # (meter_id, reading_time, power_kw, account_number, source, community)
            (self.meter_id, datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc), 0.5, self.acct, "iot", "MAK"),
            (self.meter_id, datetime(2026, 2, 17, 11, 0, tzinfo=timezone.utc), 0.6, self.acct, "iot", "MAK"),
        ]
        self.hourly_jan_row = (
            # (account_number, meter_id, reading_hour, kwh, community, source)
            self.acct, "8721", datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc), 0.42, "MAK", "thundercloud",
        )
        self.hourly_feb_overlap_row = (
            # Same hour as a meter_readings row -- MUST be deduped.
            self.acct, "8721", datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc), 0.55, "MAK", "thundercloud",
        )
        self.hourly_feb_distinct_row = (
            # Different hour from any meter_readings row -- MUST survive.
            self.acct, "8721", datetime(2026, 2, 18, 9, 0, tzinfo=timezone.utc), 0.33, "MAK", "thundercloud",
        )

        self.responses = [
            # 1. customer_type lookup
            {
                "match": "FROM accounts a JOIN customers c",
                "rows": [(self.acct, "HH")],
                "description": [("account_number",), ("customer_type",)],
            },
            # 2. meters table
            {
                "match": "FROM meters",
                "rows": [(self.meter_id, self.acct, "MAK", "primary", "active")],
                "description": [("meter_id",), ("account_number",), ("community",), ("role",), ("status",)],
            },
            # 3. meter_readings query
            {
                "match": "FROM meter_readings",
                "rows": self.meter_readings_rows,
                "description": [("meter_id",), ("reading_time",), ("power_kw",), ("account_number",), ("source",), ("community",)],
            },
            # 4. hourly_consumption query
            {
                "match": "FROM hourly_consumption",
                "rows": [self.hourly_jan_row, self.hourly_feb_overlap_row, self.hourly_feb_distinct_row],
                "description": [("account_number",), ("meter_id",), ("reading_hour",), ("kwh",), ("community",), ("source",)],
            },
        ]

    def _call(self, monkeypatch_get_connection=True):
        cur = _make_cursor(self.responses)
        ctx = _fake_conn(cur)
        if monkeypatch_get_connection:
            om_report._get_connection = MagicMock(return_value=ctx)
        return om_report.meter_data_export(
            customer_type="HH",
            site="MAK",
            start_date="2026-01-01",
            end_date="2026-02-28",
            user=_stub_user(om_report),
        )

    def test_january_hourly_row_survives_dedup(self):
        """The headline regression: account in meter_readings (Feb only)
        must NOT shadow that account's January hourly_consumption rows.
        """
        result = self._call()
        # The January row should be in the output, sourced from hourly_consumption.
        jan_rows = [
            r for r in result["readings"]
            if r["timestamp"].startswith("2026-01-15") and r["source_table"] == "hourly_consumption"
        ]
        self.assertEqual(len(jan_rows), 1, f"Expected 1 January hourly row, got: {result['readings']}")

    def test_overlap_hour_is_deduped(self):
        """When meter_readings and hourly_consumption have rows for the
        SAME (account, hour), the meter_readings row wins and the hourly
        row is skipped (correct higher-resolution preference).
        """
        result = self._call()
        feb17_10am = [r for r in result["readings"] if r["timestamp"] == "2026-02-17 10:00:00"]
        # Exactly one row at that timestamp -- from meter_readings.
        self.assertEqual(len(feb17_10am), 1)
        self.assertEqual(feb17_10am[0]["source_table"], "meter_readings")
        self.assertEqual(result["meta"]["skipped_hourly_already_covered"], 1)

    def test_distinct_hour_survives(self):
        """A hourly row at a different hour from any meter_readings row
        must survive even when the same account is otherwise covered.
        """
        result = self._call()
        feb18_9am = [r for r in result["readings"] if r["timestamp"] == "2026-02-18 09:00:00"]
        self.assertEqual(len(feb18_9am), 1)
        self.assertEqual(feb18_9am[0]["source_table"], "hourly_consumption")

    def test_total_row_count(self):
        """Sanity: 2 from meter_readings + 2 from hourly_consumption (Jan +
        Feb-distinct) = 4 in the output. The Feb-overlap hourly row is the
        one that's deduped.
        """
        result = self._call()
        self.assertEqual(len(result["readings"]), 4)
        self.assertEqual(result["meta"]["source_rows"]["meter_readings"], 2)
        self.assertEqual(result["meta"]["source_rows"]["hourly_consumption"], 2)


if __name__ == "__main__":
    unittest.main()
