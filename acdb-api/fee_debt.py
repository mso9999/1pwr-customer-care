"""
Fee debt allocation for electricity-classified payments.

Stage A: up to 50% of payment toward total fee debt, connection bucket first
then readyboard. Stage B: ``compute_advance_split`` on the remainder.

See migration ``029_customer_fee_debt.sql`` and CONTEXT.md fee section.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger("cc-api.fee-debt")

_FEE_EPS = Decimal("0.005")
_ONBOARDING_FEE_DEBT_ZERO_SETS_PAID = (
    os.environ.get("ONBOARDING_FEE_DEBT_ZERO_SETS_PAID", "1").strip().lower()
    not in ("0", "false", "no", "off")
)


def get_customer_id_for_account(conn, account_number: str) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT customer_id FROM accounts WHERE account_number = %s LIMIT 1",
        (account_number.strip(),),
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def fetch_fee_debts(conn, customer_id: int, *, for_update: bool = False) -> dict[str, Any]:
    """Return fee debt snapshot for one customer (row must exist)."""
    cur = conn.cursor()
    lock = " FOR UPDATE" if for_update else ""
    cur.execute(
        f"""
        SELECT fee_debt_connection_remaining, fee_debt_readyboard_remaining,
               COALESCE(acquires_1pwr_readyboard, false)
          FROM customers
         WHERE id = %s
        {lock}
        """,
        (customer_id,),
    )
    row = cur.fetchone()
    if not row:
        return {
            "fee_debt_connection_remaining": 0.0,
            "fee_debt_readyboard_remaining": 0.0,
            "acquires_1pwr_readyboard": False,
        }
    return {
        "fee_debt_connection_remaining": float(row[0] or 0),
        "fee_debt_readyboard_remaining": float(row[1] or 0),
        "acquires_1pwr_readyboard": bool(row[2]),
    }


def total_fee_debt_for_advance_block(conn, account_number: str) -> float:
    """Sum of remaining fee debt for the account's customer (for advance gating)."""
    cid = get_customer_id_for_account(conn, account_number)
    if cid is None:
        return 0.0
    d = fetch_fee_debts(conn, cid, for_update=False)
    return float(d["fee_debt_connection_remaining"]) + float(
        d["fee_debt_readyboard_remaining"]
    )


def _dec(x: float) -> Decimal:
    return Decimal(str(round(float(x), 2)))


def compute_fee_then_advance_split(
    amount: float,
    fee_debts: dict[str, Any],
    advance: Optional[dict],
) -> dict[str, Any]:
    """Split an electricity payment: fee cap (50%), then advance on remainder.

    Returns keys:
      fee_repayment_portion, advance_portion, electricity_portion,
      advance_id, fee_to_connection, fee_to_readyboard
    """
    from advances import compute_advance_split  # noqa: WPS433 — avoid import cycle with advances

    amt = _dec(amount)
    if amt <= 0:
        adv = compute_advance_split(advance, 0.0)
        return {
            "fee_repayment_portion": 0.0,
            "advance_portion": adv["advance_portion"],
            "electricity_portion": adv["electricity_portion"],
            "advance_id": adv["advance_id"],
            "fee_to_connection": 0.0,
            "fee_to_readyboard": 0.0,
        }

    conn_rem = _dec(fee_debts.get("fee_debt_connection_remaining") or 0)
    rb_rem = _dec(fee_debts.get("fee_debt_readyboard_remaining") or 0)
    total_debt = conn_rem + rb_rem
    half_cap = (amt * Decimal("0.5")).quantize(Decimal("0.01"))
    fee_portion = min(half_cap, total_debt).quantize(Decimal("0.01"))

    fee_to_conn = Decimal("0")
    fee_to_rb = Decimal("0")
    left = fee_portion
    if left > 0 and conn_rem > 0:
        take = min(left, conn_rem)
        fee_to_conn = take
        left -= take
    if left > 0 and rb_rem > 0:
        take = min(left, rb_rem)
        fee_to_rb = take
        left -= take

    remainder = (amt - fee_portion).quantize(Decimal("0.01"))
    adv_split = compute_advance_split(advance, float(remainder))

    return {
        "fee_repayment_portion": float(fee_portion),
        "advance_portion": adv_split["advance_portion"],
        "electricity_portion": adv_split["electricity_portion"],
        "advance_id": adv_split["advance_id"],
        "fee_to_connection": float(fee_to_conn),
        "fee_to_readyboard": float(fee_to_rb),
    }


def apply_fee_debt_reduction(
    conn,
    customer_id: int,
    connection_delta: float,
    readyboard_delta: float,
) -> None:
    """Subtract paid amounts from customer fee debt (non-negative)."""
    if connection_delta <= 0 and readyboard_delta <= 0:
        return
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE customers
           SET fee_debt_connection_remaining = GREATEST(
                   0, COALESCE(fee_debt_connection_remaining, 0) - %s),
               fee_debt_readyboard_remaining = GREATEST(
                   0, COALESCE(fee_debt_readyboard_remaining, 0) - %s)
         WHERE id = %s
        """,
        (connection_delta, readyboard_delta, customer_id),
    )


def apply_fee_payment_category_to_debt(
    conn,
    account_number: str,
    category: str,
    amount: float,
) -> None:
    """After a classified fee transaction, decrement the matching debt bucket."""
    if category not in ("connection_fee", "readyboard_fee"):
        return
    cid = get_customer_id_for_account(conn, account_number)
    if cid is None:
        return
    if category == "connection_fee":
        apply_fee_debt_reduction(conn, cid, amount, 0.0)
    else:
        apply_fee_debt_reduction(conn, cid, 0.0, amount)
    maybe_sync_commissioning_flags_from_fee_debt(conn, cid)


def maybe_sync_commissioning_flags_from_fee_debt(conn, customer_id: int) -> None:
    """When fee debt hits zero, optionally set pipeline fee-paid flags."""
    if not _ONBOARDING_FEE_DEBT_ZERO_SETS_PAID:
        return
    cur = conn.cursor()
    cur.execute(
        """
        SELECT fee_debt_connection_remaining, fee_debt_readyboard_remaining,
               COALESCE(acquires_1pwr_readyboard, false)
          FROM customers WHERE id = %s
        """,
        (customer_id,),
    )
    row = cur.fetchone()
    if not row:
        return
    conn_d = _dec(row[0] or 0)
    rb_d = _dec(row[1] or 0)
    acquires = bool(row[2])
    sets: list[str] = []
    params: list[Any] = []
    if conn_d <= _FEE_EPS:
        sets.append("connection_fee_paid = TRUE")
    if acquires and rb_d <= _FEE_EPS:
        sets.append("readyboard_fee_paid = TRUE")
    if not sets:
        return
    sql = f"UPDATE customers SET {', '.join(sets)} WHERE id = %s"
    params.append(customer_id)
    try:
        cur.execute(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "maybe_sync_commissioning_flags_from_fee_debt skipped for customer %s: %s",
            customer_id,
            exc,
        )
