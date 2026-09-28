"""Energy debt is the prepaid overshoot, recorded once, paid down by later kWh."""

import os
import unittest
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from energy_debt import energy_debt_delta, energy_debt_kwh_from_balance, sync_energy_debt  # noqa: E402


class EnergyDebtMathTests(unittest.TestCase):
    def test_positive_balance_is_not_debt(self):
        self.assertEqual(energy_debt_kwh_from_balance(3.1), 0.0)

    def test_negative_balance_is_debt(self):
        self.assertEqual(energy_debt_kwh_from_balance(-0.1237), 0.1237)

    def test_growth_is_an_accrual(self):
        self.assertEqual(energy_debt_delta(0.0, 0.13), ("accrual", 0.13))

    def test_momo_filling_the_hole_is_a_repayment(self):
        self.assertEqual(energy_debt_delta(0.13, 0.0), ("repayment", 0.13))

    def test_unchanged_posts_nothing(self):
        self.assertIsNone(energy_debt_delta(0.13, 0.13002))


class _Cur:
    def __init__(self, customer_row):
        self.customer_row = customer_row
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        sql = self.statements[-1][0]
        if "FROM accounts" in sql:
            return self.customer_row
        if "FROM meters" in sql:
            return ("KOT",)
        return None


class _Conn:
    def __init__(self, customer_row):
        self.cur = _Cur(customer_row)

    def cursor(self):
        return self.cur


class EnergyDebtSyncTests(unittest.TestCase):
    def test_consumption_accrues_once(self):
        conn = _Conn((9, 0))
        with mock.patch("balance_engine.get_balance_kwh", return_value=(-0.13, None)):
            result = sync_energy_debt(conn, "0003KOT", source="meter_reading")
        self.assertTrue(result["posted"])
        self.assertEqual(result["entry_type"], "accrual")
        self.assertEqual(result["kwh"], 0.13)
        inserts = [sql for sql, _ in conn.cur.statements if "INSERT INTO energy_debt_ledger" in sql]
        self.assertEqual(len(inserts), 1)

    def test_repeat_sync_does_not_double_post(self):
        conn = _Conn((9, 0.13))
        with mock.patch("balance_engine.get_balance_kwh", return_value=(-0.13, None)):
            result = sync_energy_debt(conn, "0003KOT", source="meter_reading")
        self.assertFalse(result["posted"])
        self.assertFalse(any("INSERT INTO energy_debt_ledger" in sql for sql, _ in conn.cur.statements))

    def test_momo_repayment_when_balance_recovers(self):
        conn = _Conn((9, 0.13))
        with mock.patch("balance_engine.get_balance_kwh", return_value=(2.9, None)):
            result = sync_energy_debt(conn, "0003KOT", source="payment")
        self.assertEqual(result["entry_type"], "repayment")
        self.assertEqual(result["kwh"], 0.13)
        self.assertEqual(result["energy_debt_kwh"], 0.0)
