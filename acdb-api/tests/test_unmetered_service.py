"""Unit tests for unmetered service (connected, no meter → flat monthly fee).

Covers the payment split (``compute_service_fee_split`` + its integration into
``fee_debt.compute_fee_then_advance_split``), the monthly accrual (idempotency
+ active-meter auto-exit guard), repayment application, enrollment exit, and
the deploy-window graceful degradation when migration 068 has not run yet.
"""

import os
import unittest

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from fee_debt import compute_fee_then_advance_split
from unmetered_service import (
    apply_service_fee_payment,
    accrue_period,
    compute_service_fee_split,
    end_unmetered_service,
    get_active_enrollment,
)


def _enrollment(outstanding=100.0, fraction=0.5, service_id=7, account="0068MAK"):
    return {
        "id": service_id,
        "account_number": account,
        "customer_id": 42,
        "monthly_fee": 50.0,
        "repayment_fraction": fraction,
        "outstanding": outstanding,
        "currency": "LSL",
        "started_at": "2026-08-28T10:00:00+00:00",
    }


class TestComputeServiceFeeSplit(unittest.TestCase):
    def test_no_enrollment_passes_full_amount_through(self):
        r = compute_service_fee_split(None, 100.0)
        self.assertEqual(r["service_fee_portion"], 0.0)
        self.assertEqual(r["remainder_portion"], 100.0)
        self.assertIsNone(r["unmetered_service_id"])

    def test_fraction_of_amount(self):
        r = compute_service_fee_split(_enrollment(outstanding=1000.0), 100.0)
        self.assertEqual(r["service_fee_portion"], 50.0)
        self.assertEqual(r["remainder_portion"], 50.0)
        self.assertEqual(r["unmetered_service_id"], 7)

    def test_capped_at_outstanding(self):
        r = compute_service_fee_split(_enrollment(outstanding=30.0), 100.0)
        self.assertEqual(r["service_fee_portion"], 30.0)
        self.assertEqual(r["remainder_portion"], 70.0)

    def test_zero_outstanding_charges_nothing(self):
        r = compute_service_fee_split(_enrollment(outstanding=0.0), 100.0)
        self.assertEqual(r["service_fee_portion"], 0.0)
        self.assertEqual(r["remainder_portion"], 100.0)


class TestSplitChainOrdering(unittest.TestCase):
    """fee debt → service fee → advance → electricity, portions sum to amount."""

    def test_service_fee_between_fee_debt_and_advance(self):
        debts = {"fee_debt_connection_remaining": 100.0,
                 "fee_debt_readyboard_remaining": 0.0}
        advance = {"id": 3, "repayment_fraction": 0.5, "outstanding": 500.0}
        enr = _enrollment(outstanding=80.0, fraction=0.5)

        r = compute_fee_then_advance_split(200.0, debts, advance, enr)

        self.assertEqual(r["fee_repayment_portion"], 100.0)   # half cap vs 100 debt
        self.assertEqual(r["service_fee_portion"], 50.0)      # 0.5 × 100 remainder
        self.assertEqual(r["unmetered_service_id"], 7)
        self.assertEqual(r["advance_portion"], 25.0)          # 0.5 × 50 remainder
        self.assertEqual(r["electricity_portion"], 25.0)
        total = (r["fee_repayment_portion"] + r["service_fee_portion"]
                 + r["advance_portion"] + r["electricity_portion"])
        self.assertAlmostEqual(total, 200.0)

    def test_no_enrollment_matches_legacy_behavior(self):
        debts = {"fee_debt_connection_remaining": 0.0,
                 "fee_debt_readyboard_remaining": 0.0}
        advance = {"id": 3, "repayment_fraction": 0.5, "outstanding": 500.0}
        r = compute_fee_then_advance_split(100.0, debts, advance)
        self.assertEqual(r["service_fee_portion"], 0.0)
        self.assertIsNone(r["unmetered_service_id"])
        self.assertEqual(r["advance_portion"], 50.0)
        self.assertEqual(r["electricity_portion"], 50.0)

    def test_zero_amount_returns_all_zero(self):
        r = compute_fee_then_advance_split(0.0, {}, None, _enrollment())
        self.assertEqual(r["service_fee_portion"], 0.0)
        self.assertEqual(r["electricity_portion"], 0.0)


# ---------------------------------------------------------------------------
# DB-touching functions over a stateful fake connection
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self._one = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        self._rows = []
        self._one = None

        if s.startswith("select id, account_number, monthly_fee, outstanding, currency") \
                and "for update" in s:
            self._rows = [
                (e["id"], e["account_number"], e["monthly_fee"], e["outstanding"], e["currency"])
                for e in self.conn.enrollments if e["status"] == "active"
            ]
        elif "from meters" in s:
            self._one = (1,) if params[0] in self.conn.metered_accounts else None
        elif "select 1 from unmetered_service_ledger" in s:
            sid, period = params
            self._one = (1,) if any(
                l["service_id"] == sid and l["entry_type"] == "accrual"
                and l.get("accrual_period") == period
                for l in self.conn.ledger
            ) else None
        elif s.startswith("select outstanding from unmetered_service"):
            if "for update" in s:
                sid = params[0]
                e = self.conn._by_id(sid)
                self._one = (e["outstanding"],) if e and e["status"] == "active" else None
            else:
                sid = params[0]
                e = self.conn._by_id(sid)
                self._one = (e["outstanding"],) if e else None
        elif s.startswith("insert into unmetered_service_ledger"):
            # entry_type is a SQL literal, not a param; params differ per type.
            if "'accrual'" in s:
                row = {"service_id": params[0], "entry_type": "accrual",
                       "amount": params[1], "balance_after": params[2],
                       "accrual_period": params[3]}
            elif "'repayment'" in s:
                row = {"service_id": params[0], "entry_type": "repayment",
                       "amount": params[1], "balance_after": params[2]}
            else:
                row = {"service_id": params[0], "entry_type": "adjustment",
                       "amount": 0, "balance_after": params[1]}
            self.conn.ledger.append(row)
        elif s.startswith("update unmetered_service set outstanding"):
            new_out, sid = params
            self.conn._by_id(sid)["outstanding"] = new_out
        elif s.startswith("update unmetered_service set status = 'ended'"):
            reason, acct = params
            e = next((x for x in self.conn.enrollments
                      if x["account_number"] == acct and x["status"] == "active"), None)
            if e:
                e["status"] = "ended"
                e["end_reason"] = reason
                self._one = (e["id"],)
        elif s.startswith("select id, account_number, customer_id, monthly_fee"):
            acct = params[0]
            e = next((x for x in self.conn.enrollments
                      if x["account_number"] == acct and x["status"] == "active"), None)
            self._one = (
                (e["id"], e["account_number"], e.get("customer_id"), e["monthly_fee"],
                 e["repayment_fraction"], e["outstanding"], e["currency"], None)
                if e else None
            )
        else:
            raise AssertionError(f"unexpected SQL in fake: {s[:120]}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _FakeConn:
    def __init__(self, enrollments, metered_accounts=()):
        self.enrollments = enrollments
        self.metered_accounts = set(metered_accounts)
        self.ledger = []
        self._cur = _FakeCursor(self)

    def _by_id(self, sid):
        return next((e for e in self.enrollments if e["id"] == sid), None)

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass


def _db_enrollment(service_id=7, account="0068MAK", outstanding=100.0, fee=50.0):
    return {
        "id": service_id, "account_number": account, "customer_id": 42,
        "monthly_fee": fee, "repayment_fraction": 0.5, "outstanding": outstanding,
        "currency": "LSL", "status": "active",
    }


class TestAccrual(unittest.TestCase):
    def test_accrues_monthly_fee(self):
        conn = _FakeConn([_db_enrollment()])
        summary = accrue_period(conn, "2026-09")
        self.assertEqual(summary["accruals_applied"], 1)
        self.assertEqual(summary["total_fee_amount"], 50.0)
        e = conn._by_id(7)
        self.assertEqual(e["outstanding"], 150.0)
        accruals = [l for l in conn.ledger if l["entry_type"] == "accrual"]
        self.assertEqual(len(accruals), 1)
        self.assertEqual(accruals[0]["accrual_period"], "2026-09")
        self.assertEqual(accruals[0]["balance_after"], 150.0)

    def test_idempotent_within_period(self):
        conn = _FakeConn([_db_enrollment()])
        accrue_period(conn, "2026-09")
        summary2 = accrue_period(conn, "2026-09")
        self.assertEqual(summary2["accruals_applied"], 0)
        self.assertEqual(summary2["accruals_skipped_duplicate"], 1)
        self.assertEqual(conn._by_id(7)["outstanding"], 150.0)

    def test_second_month_accrues_again(self):
        conn = _FakeConn([_db_enrollment()])
        accrue_period(conn, "2026-09")
        summary = accrue_period(conn, "2026-10")
        self.assertEqual(summary["accruals_applied"], 1)
        self.assertEqual(conn._by_id(7)["outstanding"], 200.0)

    def test_auto_ends_when_meter_appeared(self):
        conn = _FakeConn([_db_enrollment()], metered_accounts={"0068MAK"})
        summary = accrue_period(conn, "2026-09")
        self.assertEqual(summary["accruals_applied"], 0)
        self.assertEqual(summary["auto_ended_metered"], 1)
        e = conn._by_id(7)
        self.assertEqual(e["status"], "ended")
        self.assertEqual(e["end_reason"], "meter_assigned")
        self.assertEqual(e["outstanding"], 100.0, "no accrual after auto-exit")

    def test_dry_run_changes_nothing(self):
        conn = _FakeConn([_db_enrollment()])
        summary = accrue_period(conn, "2026-09", dry_run=True)
        self.assertEqual(summary["accruals_applied"], 1)
        self.assertEqual(conn._by_id(7)["outstanding"], 100.0)
        self.assertEqual(len(conn.ledger), 0)


class TestRepaymentAndExit(unittest.TestCase):
    def test_apply_service_fee_payment_decrements(self):
        conn = _FakeConn([_db_enrollment(outstanding=80.0)])
        new_out, applied = apply_service_fee_payment(conn, 7, 50.0, source_transaction_id=99)
        self.assertEqual(applied, 50.0)
        self.assertEqual(new_out, 30.0)
        self.assertEqual(conn._by_id(7)["outstanding"], 30.0)
        repay = [l for l in conn.ledger if l["entry_type"] == "repayment"]
        self.assertEqual(len(repay), 1)
        self.assertEqual(repay[0]["balance_after"], 30.0)

    def test_apply_capped_at_outstanding(self):
        conn = _FakeConn([_db_enrollment(outstanding=20.0)])
        new_out, applied = apply_service_fee_payment(conn, 7, 50.0)
        self.assertEqual(applied, 20.0)
        self.assertEqual(new_out, 0.0)

    def test_enrollment_stays_active_at_zero(self):
        conn = _FakeConn([_db_enrollment(outstanding=20.0)])
        apply_service_fee_payment(conn, 7, 50.0)
        self.assertEqual(conn._by_id(7)["status"], "active")

    def test_end_unmetered_service(self):
        conn = _FakeConn([_db_enrollment()])
        ended = end_unmetered_service(conn, "0068MAK", "commissioned", "user:1")
        self.assertTrue(ended)
        e = conn._by_id(7)
        self.assertEqual(e["status"], "ended")
        self.assertEqual(e["end_reason"], "commissioned")
        # second end is a no-op
        self.assertFalse(end_unmetered_service(conn, "0068MAK", "commissioned", "user:1"))


class _MissingTableCursor:
    def execute(self, sql, params=None):
        raise Exception('relation "unmetered_service" does not exist')

    def fetchone(self):
        return None


class _MissingTableConn:
    def __init__(self):
        self.rolled_back = False

    def cursor(self):
        return _MissingTableCursor()

    def rollback(self):
        self.rolled_back = True


class TestDeployWindowDegradation(unittest.TestCase):
    """APIs restart before migrations run — table may not exist yet."""

    def test_get_active_enrollment_missing_table_returns_none(self):
        conn = _MissingTableConn()
        self.assertIsNone(get_active_enrollment(conn, "0068MAK"))
        self.assertTrue(conn.rolled_back)

    def test_end_hook_missing_table_is_noop(self):
        conn = _MissingTableConn()
        self.assertFalse(end_unmetered_service(conn, "0068MAK", "meter_assigned", "user:1"))
        self.assertTrue(conn.rolled_back)


if __name__ == "__main__":
    unittest.main()
