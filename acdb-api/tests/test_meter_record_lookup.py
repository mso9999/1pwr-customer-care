"""Meters are addressed by serial, which is numeric and is not meters.id."""

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from crud import _resolve_lookup_column


class _Cursor:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        kind, value = self.steps.pop(0)
        assert kind == "one", kind
        return value

    def fetchall(self):
        kind, value = self.steps.pop(0)
        assert kind == "all", kind
        return value


def _conn(steps):
    conn = MagicMock()
    cursor = _Cursor(steps)
    conn.cursor.return_value = cursor
    return conn, cursor


class TestMeterRecordLookup(unittest.TestCase):
    def test_numeric_serial_uses_meter_id_not_integer_pk(self):
        conn, cursor = _conn([("one", (1,))])
        column = _resolve_lookup_column(conn, "meters", "id", "23022628")
        self.assertEqual(column, "meter_id")
        self.assertIn("meter_id = %s", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], ("23022628",))

    def test_integer_primary_key_still_wins_when_that_row_exists(self):
        conn, _cursor = _conn([
            ("one", ("integer",)),
            ("one", (1,)),
        ])
        column = _resolve_lookup_column(conn, "customers", "id", "16452")
        self.assertEqual(column, "id")

    def test_numeric_value_falls_through_to_unique_column_when_pk_row_is_missing(self):
        conn, _cursor = _conn([
            ("one", ("integer",)),
            ("one", None),
            ("all", [("account_number",)]),
            ("one", (1,)),
        ])
        column = _resolve_lookup_column(conn, "accounts", "id", "23022628")
        self.assertEqual(column, "account_number")


if __name__ == "__main__":
    unittest.main()
