"""Unit tests for fee debt allocation (``fee_debt.compute_fee_then_advance_split``)."""

from fee_debt import compute_fee_then_advance_split


def _adv(outstanding: float, fraction: float = 0.5, adv_id: int = 1):
    return {
        "id": adv_id,
        "account_number": "0001MAK",
        "advance_type": "connection",
        "original_amount": 1000,
        "outstanding": outstanding,
        "currency": "LSL",
        "repayment_fraction": fraction,
        "monthly_fee_pct": 0,
        "status": "active",
    }


def test_fee_half_cap_connection_first_then_advance():
    debts = {
        "fee_debt_connection_remaining": 100.0,
        "fee_debt_readyboard_remaining": 50.0,
        "acquires_1pwr_readyboard": True,
    }
    # amount 200: half cap 100 -> all to connection fee bucket
    r = compute_fee_then_advance_split(200.0, debts, _adv(500))
    assert r["fee_repayment_portion"] == 100.0
    assert r["fee_to_connection"] == 100.0
    assert r["fee_to_readyboard"] == 0.0
    assert r["advance_portion"] == 50.0  # half of remainder 100
    assert r["electricity_portion"] == 50.0


def test_fee_split_pays_readyboard_after_connection():
    debts = {
        "fee_debt_connection_remaining": 30.0,
        "fee_debt_readyboard_remaining": 80.0,
        "acquires_1pwr_readyboard": True,
    }
    # amount 100: fee cap min(50, 110)=50 -> 30 conn + 20 rb
    r = compute_fee_then_advance_split(100.0, debts, None)
    assert r["fee_repayment_portion"] == 50.0
    assert r["fee_to_connection"] == 30.0
    assert r["fee_to_readyboard"] == 20.0
    assert r["advance_portion"] == 0.0
    assert r["electricity_portion"] == 50.0


def test_no_advance_no_debt_full_electricity():
    debts = {
        "fee_debt_connection_remaining": 0.0,
        "fee_debt_readyboard_remaining": 0.0,
        "acquires_1pwr_readyboard": False,
    }
    r = compute_fee_then_advance_split(99.5, debts, None)
    assert r["fee_repayment_portion"] == 0.0
    assert r["electricity_portion"] == 99.5


def test_lump_sum_that_covers_all_fee_debt_is_fee_first():
    debts = {
        "fee_debt_connection_remaining": 501.0,
        "fee_debt_readyboard_remaining": 499.0,
        "acquires_1pwr_readyboard": True,
    }
    r = compute_fee_then_advance_split(1000.0, debts, None)
    assert r["fee_repayment_portion"] == 1000.0
    assert r["fee_to_connection"] == 501.0
    assert r["fee_to_readyboard"] == 499.0
    assert r["advance_portion"] == 0.0
    assert r["electricity_portion"] == 0.0
