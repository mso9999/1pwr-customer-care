"""
kWh Balance Engine for 1PDB.

The single source of truth for customer balance computation.
Balance is always in kWh, matching the legacy ACCDB VBA logic:
  - Payments credit kWh:  balance += payment_amount / tariff_rate
  - Consumption debits kWh: balance -= kwh_consumed

The balance is derived from two tables:
  - transactions.current_balance: kWh snapshot at time of each payment/ACCDB row
  - hourly_consumption: kWh consumed per hour (from Koios/ThunderCloud imports)

True live balance = last_txn_balance - SUM(consumption since that transaction)

For reconciliation, kWh can be converted to currency via tariff rate at any point.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("cc-api.balance")


def get_balance_kwh(conn, account_number: str) -> tuple[float, datetime | None]:
    """Compute the current kWh balance for an account.

    Returns (balance_kwh, as_of_timestamp).

    Combines the last transaction snapshot with any consumption
    recorded in hourly_consumption since that snapshot.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT current_balance, transaction_date
        FROM transactions
        WHERE account_number = %s AND current_balance IS NOT NULL
        ORDER BY transaction_date DESC, id DESC
        LIMIT 1
    """, (account_number,))
    row = cur.fetchone()

    if not row:
        return 0.0, None

    last_txn_balance = float(row[0])
    last_txn_date = row[1]

    cur.execute("""
        SELECT COALESCE(SUM(kwh), 0)
        FROM hourly_consumption
        WHERE account_number = %s
          AND reading_hour > %s
    """, (account_number, last_txn_date))
    consumption_since = float(cur.fetchone()[0])

    balance = round(last_txn_balance - consumption_since, 4)
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

    The kWh balance is computed from the last transaction snapshot
    minus any consumption since, then the new kWh are added.
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
