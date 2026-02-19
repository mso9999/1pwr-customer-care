"""
kWh Balance Engine for 1PDB.

The single source of truth for customer balance computation.
Balance is always in kWh, matching the legacy ACCDB VBA logic:
  - Payments credit kWh:  balance += payment_amount / tariff_rate
  - Consumption debits kWh: balance -= kwh_consumed

Balance = SUM(payment kWh from transactions)
        - SUM(consumption kWh from hourly_consumption)
        - SUM(consumption kWh from accdb transaction rows where is_payment=false)

This is a full-history computation — no running totals or seeds needed.
For reconciliation, kWh can be converted to currency via tariff rate.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("cc-api.balance")


def get_balance_kwh(conn, account_number: str) -> tuple[float, datetime | None]:
    """Compute the current kWh balance for an account.

    Returns (balance_kwh, as_of_timestamp).

    Uses a dual-source approach:
    1. Payment credits from transactions (is_payment=true → kwh_value added)
    2. Consumption debits from:
       a) hourly_consumption table (Koios/ThunderCloud imports)
       b) transactions where is_payment=false (legacy ACCDB consumption rows)
    """
    cur = conn.cursor()

    # Total kWh credited via payments
    cur.execute("""
        SELECT COALESCE(SUM(kwh_value), 0)
        FROM transactions
        WHERE account_number = %s AND is_payment = true
    """, (account_number,))
    total_payment_kwh = float(cur.fetchone()[0])

    # Total kWh consumed from hourly_consumption (Koios/ThunderCloud live data)
    cur.execute("""
        SELECT COALESCE(SUM(kwh), 0)
        FROM hourly_consumption
        WHERE account_number = %s
    """, (account_number,))
    total_live_consumption = float(cur.fetchone()[0])

    # Total kWh consumed from ACCDB consumption rows in transactions
    # (accdb stores consumed kWh in transaction_amount for is_payment=false)
    cur.execute("""
        SELECT COALESCE(SUM(transaction_amount), 0)
        FROM transactions
        WHERE account_number = %s AND is_payment = false
    """, (account_number,))
    total_accdb_consumption = float(cur.fetchone()[0])

    balance = round(total_payment_kwh - total_live_consumption - total_accdb_consumption, 4)
    return balance, datetime.now(timezone.utc)


def record_payment_kwh(
    conn,
    account_number: str,
    meter_id: str,
    amount_currency: float,
    rate: float,
    kwh_override: float | None = None,
    source: str = "portal",
    timestamp: datetime | None = None,
) -> tuple[int, float, float]:
    """Record a payment and return (txn_id, kwh_vended, new_balance_kwh).

    Computes the current balance from full history, adds the new
    payment's kWh, and stores the snapshot in current_balance.
    """
    cur = conn.cursor()
    ts = timestamp or datetime.now(timezone.utc)

    kwh_vended = kwh_override if kwh_override is not None else (
        round(amount_currency / rate, 4) if rate > 0 else 0.0
    )

    prev_balance, _ = get_balance_kwh(conn, account_number)
    new_balance = round(prev_balance + kwh_vended, 4)

    cur.execute("""
        INSERT INTO transactions
            (account_number, meter_id, transaction_date,
             transaction_amount, rate_used, kwh_value,
             is_payment, current_balance, source)
        VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s)
        RETURNING id
    """, (
        account_number, meter_id, ts,
        amount_currency, rate, kwh_vended,
        new_balance, source,
    ))
    txn_id = cur.fetchone()[0]

    logger.info(
        "Payment: txn=%d acct=%s %s%.2f -> %.4f kWh @ %.2f  bal=%.4f kWh",
        txn_id, account_number, "M" if source != "koios" else "",
        amount_currency, kwh_vended, rate, new_balance,
    )

    return txn_id, kwh_vended, new_balance


def balance_to_currency(balance_kwh: float, rate: float) -> float:
    """Convert a kWh balance to currency equivalent at a given rate."""
    return round(balance_kwh * rate, 4)
