"""Regression tests for the fleet map's handling of linked-but-never-reported
1Meters — the 0068MAK / meter 23024497 report.

Bug: assign-PTB records meter_gateway_link with gateway_thing NULL until the
gateway first reports (commit 2b2877f), but the fleet-map SQL tested
``gl.gateway_thing IS NOT NULL`` for the ``linked`` flag — so those meters
rendered as ordinary unlinked dots with no Thing name. The flag now tests the
link row's presence (``gl.meter_serial IS NOT NULL``).

These tests stub 1PDB (psycopg2 connection) and DynamoDB (meter_last_seen);
the SQL-semantics test runs the captured query against in-memory SQLite, whose
ltrim(x, '0') / IS NOT NULL / LEFT JOIN semantics match Postgres for this
statement.
"""

import os
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_provisioning as mp

COLS = [
    "meter_id", "account_number", "community", "village_name",
    "latitude", "longitude", "status", "platform", "linked", "prov_thing",
    "link_thing", "link_pole",
]


class _BoolOr:
    """SQLite stand-in for the Postgres BOOL_OR aggregate."""

    def __init__(self):
        self.val = False

    def step(self, x):
        if x:
            self.val = True

    def finalize(self):
        return 1 if self.val else 0

# The reported meter: assigned to a pole/PTB, gateway not yet known, never reported.
METER_ID = "23024497"
ACCOUNT = "0068MAK"


class _FakeCursor:
    """Minimal psycopg2 cursor stand-in: records SQL, returns preset rows."""

    def __init__(self, rows):
        self._rows = rows
        self.executed_sql = []
        self.description = [(c,) for c in COLS]

    def execute(self, sql, params=None):
        self.executed_sql.append(sql)

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_fleet_map(rows, ddb_items):
    """Call mp.fleet_map with stubbed 1PDB + DynamoDB. Returns (result, sql)."""
    cur = _FakeCursor(rows)
    fake_ca = types.ModuleType("customer_api")
    fake_ca.get_connection = lambda: _FakeConn(cur)

    ddb = MagicMock()
    ddb.scan.return_value = {"Items": ddb_items}

    with patch.dict(sys.modules, {"customer_api": fake_ca}), \
         patch.object(mp, "_client", return_value=ddb):
        result = mp.fleet_map(site=None, _user=MagicMock())
    return result, cur.executed_sql[0]


class TestFleetMapLinkedFlagSql(unittest.TestCase):
    """Run the captured fleet-map SQL against SQLite to prove the semantics."""

    def _sqlite_rows(self, sql):
        db = sqlite3.connect(":memory:")
        db.create_aggregate("BOOL_OR", 1, _BoolOr)
        db.executescript(
            """
            CREATE TABLE meters (
                meter_id TEXT, meter_number TEXT, account_number TEXT,
                community TEXT, village_name TEXT,
                latitude REAL, longitude REAL, status TEXT, platform TEXT);
            CREATE TABLE meter_provisioning (meter_serial TEXT, thing_name TEXT);
            CREATE TABLE meter_gateway_link (
                meter_serial TEXT, gateway_thing TEXT, pole_id TEXT);
            """
        )
        # 0068MAK's meter: linked via PTB with gateway_thing NULL (never reported).
        db.execute(
            "INSERT INTO meters VALUES (?, NULL, ?, 'MAK', 'Ha Makebe', -29.1, 27.5, 'installed', 'prototype')",
            (METER_ID, ACCOUNT),
        )
        db.execute("INSERT INTO meter_gateway_link VALUES (?, NULL, NULL)", (METER_ID,))
        # Control 1: gateway-linked via provisioning (primary meter of a gateway).
        db.execute(
            "INSERT INTO meters VALUES ('23022628', NULL, '0005MAK', 'MAK', 'Ha Makebe', -29.1, 27.5, 'installed', 'prototype')"
        )
        db.execute(
            "INSERT INTO meter_provisioning VALUES ('23022628', 'MAK-GW-0001')"
        )
        # Control 2: plain SparkMeter, no link anywhere.
        db.execute(
            "INSERT INTO meters VALUES ('58431', NULL, '0025MAK', 'MAK', 'Ha Makebe', -29.1, 27.5, 'installed', 'sparkmeter')"
        )
        rows = db.execute(sql).fetchall()
        db.close()
        return {r[0]: r for r in rows}

    def test_null_gateway_link_row_still_counts_as_linked(self):
        _result, sql = _run_fleet_map(rows=[], ddb_items=[])
        # Pin the regression at the source: the flag must test the link row's
        # presence, not the (nullable) gateway name.
        self.assertIn("gl.meter_serial IS NOT NULL", sql)
        self.assertNotIn("gl.gateway_thing IS NOT NULL) AS linked", sql)

        rows = self._sqlite_rows(sql)
        self.assertTrue(rows[METER_ID][8], "NULL-gateway link row must read as linked")
        self.assertIsNone(rows[METER_ID][9], "no Thing name until the unit reports")
        self.assertTrue(rows["23022628"][8], "provisioned gateway meter stays linked")
        self.assertEqual(rows["23022628"][9], "MAK-GW-0001")
        self.assertFalse(rows["58431"][8], "unlinked SparkMeter stays unlinked")


class TestFleetMapNeverReportedMeter(unittest.TestCase):
    """End-to-end assembly: linked meter with no meter_last_seen row."""

    def test_linked_meter_without_last_seen_renders_linked_and_offline(self):
        # Row as the fixed SQL returns it: linked=True, prov_thing/link_thing=None.
        rows = [(METER_ID, ACCOUNT, "MAK", "Ha Makebe", -29.1, 27.5,
                 "installed", "prototype", True, None, None, None)]
        result, _sql = _run_fleet_map(rows=rows, ddb_items=[])  # never reported

        self.assertEqual(result["total"], 1)
        m = result["meters"][0]
        self.assertEqual(m["meter_id"], METER_ID)
        self.assertEqual(m["account_number"], ACCOUNT)
        self.assertTrue(m["linked"])
        self.assertFalse(m["online"])
        self.assertIsNone(m["thing_name"])
        self.assertIsNone(m["last_seen"])
        self.assertEqual(result["online"], 0)
        self.assertEqual(result["offline"], 1)

    def test_reporting_meter_gets_thing_name_and_online(self):
        rows = [(METER_ID, ACCOUNT, "MAK", "Ha Makebe", -29.1, 27.5,
                 "installed", "prototype", True, None, None, None)]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = [{
            "meterId": {"S": METER_ID},
            "thingName": {"S": "MAK-GW-0010"},
            "last_seen": {"S": ts},
        }]
        result, _sql = _run_fleet_map(rows=rows, ddb_items=items)

        m = result["meters"][0]
        self.assertTrue(m["linked"])
        self.assertTrue(m["online"])
        self.assertEqual(m["thing_name"], "MAK-GW-0010")
        self.assertEqual(m["last_seen"], ts)
        self.assertEqual(result["online"], 1)


if __name__ == "__main__":
    unittest.main()
