"""Energy debt: kWh drawn after prepaid credit hit zero, before cutoff.

The balance engine is the source of truth (payment kWh minus metered use).
When that result is negative, the magnitude is energy debt. A later
electricity purchase — including a MoMo top-up — lifts the balance and
pays the debt down. This module records that movement. It does not take
a second slice of the payment.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("cc-api.energy-debt")

_EPS = 0.0001


def energy_debt_kwh_from_balance(raw_balance_kwh: float) -> float:
    """kWh owed when the prepaid balance has gone below zero."""
    return round(max(0.0, -float(raw_balance_kwh)), 4)


def energy_debt_delta(previous_kwh: float, new_kwh: float) -> Optional[tuple[str, float]]:
    """Return (entry_type, kWh) when the debt position moved, else None."""
    delta = round(float(new_kwh) - float(previous_kwh), 4)
    if abs(delta) < _EPS:
        return None
    if delta > 0:
        return ("accrual", delta)
    return ("repayment", round(-delta, 4))


def sync_energy_debt(conn, account_number: str, *, source: str) -> dict:
    """Write the ledger when metered use or a payment changes the overshoot.

    Callers own the transaction. A missing customer row is skipped so a
    reading is never dropped.
    """
    from balance_engine import get_balance_kwh

    raw, _ = get_balance_kwh(conn, account_number)
    new_debt = energy_debt_kwh_from_balance(raw)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, COALESCE(c.energy_debt_kwh, 0)
          FROM accounts a
          JOIN customers c ON c.id = a.customer_id
         WHERE a.account_number = %s
         LIMIT 1
        """,
        (account_number,),
    )
    row = cur.fetchone()
    if not row:
        return {"energy_debt_kwh": new_debt, "posted": False, "reason": "no_customer"}

    customer_id, previous = int(row[0]), float(row[1] or 0)
    change = energy_debt_delta(previous, new_debt)
    if change is None:
        return {"energy_debt_kwh": new_debt, "posted": False}

    entry_type, kwh = change
    rate = _tariff_for_account(cur, account_number)
    currency = round(kwh * rate, 2) if rate else None
    cur.execute(
        "UPDATE customers SET energy_debt_kwh = %s WHERE id = %s",
        (new_debt, customer_id),
    )
    cur.execute(
        """
        INSERT INTO energy_debt_ledger (
            account_number, customer_id, entry_type, kwh, debt_after_kwh,
            tariff_rate, currency_amount, source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            account_number,
            customer_id,
            entry_type,
            kwh,
            new_debt,
            rate,
            currency,
            source,
        ),
    )
    logger.info(
        "energy debt %s acct=%s kwh=%.4f debt_after=%.4f source=%s",
        entry_type,
        account_number,
        kwh,
        new_debt,
        source,
    )
    return {
        "energy_debt_kwh": new_debt,
        "posted": True,
        "entry_type": entry_type,
        "kwh": kwh,
    }


def _tariff_for_account(cur, account_number: str) -> Optional[float]:
    try:
        cur.execute(
            "SELECT community FROM meters WHERE account_number = %s AND community IS NOT NULL LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        site = str(row[0]).strip().upper() if row and row[0] else ""
        if not site:
            tail = "".join(ch for ch in account_number.upper() if ch.isalpha())
            site = tail[-4:] if tail else ""
        from country_config import get_tariff_rate_for_site

        rate = float(get_tariff_rate_for_site(site))
        return rate if rate > 0 else None
    except Exception:
        logger.exception("energy debt tariff lookup failed for %s", account_number)
        return None
