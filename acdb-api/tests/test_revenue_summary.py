"""Dashboard portfolio revenue must read the live transactions ledger."""

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from monthly_aggregates import REBUILD_SQL
from stats import _country_monthly_revenue


class TestCountryMonthlyRevenue(unittest.TestCase):
    def test_reads_transactions_not_monthly_aggregate(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchall.return_value = [
            ("2026-07", 105834.46, 788),
            ("2026-08", 223709.32, 935),
            ("2026-09", 153938.01, 840),
        ]

        rows = _country_monthly_revenue(conn, "LS", "LSL", 12)

        sql = cursor.execute.call_args[0][0]
        self.assertIn("FROM transactions", sql)
        self.assertNotIn("monthly_transactions", sql)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["month"], "2026-09")
        self.assertEqual(rows[-1]["paying_customers"], 840)
        self.assertEqual(rows[-1]["revenue_local"], 153938.01)

    def test_rebuild_sql_joins_meter_by_serial_not_account(self):
        self.assertIn("FROM transactions t", REBUILD_SQL)
        self.assertIn("LEFT JOIN meters m ON t.meter_id = m.meter_id", REBUILD_SQL)
        self.assertNotIn("ON t.account_number = m.account_number", REBUILD_SQL)
        self.assertIn("COALESCE(t.meter_id, '')", REBUILD_SQL)


if __name__ == "__main__":
    unittest.main()
