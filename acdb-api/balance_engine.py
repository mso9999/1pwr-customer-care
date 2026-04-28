"""
kWh Balance Engine for 1PDB.

The single source of truth for customer balance computation.
Balance is always in kWh, matching the legacy VBA logic carried forward from ACCDB:
  - Payments credit kWh:  balance += payment_amount / tariff_rate
  - Consumption debits kWh: balance -= kwh_consumed

Balance = SUM(payment kWh from transactions)
        - SUM(consumption kWh from hourly_consumption [priority-aware])
        - SUM(consumption kWh from legacy transaction rows where is_payment=false)

This is a full-history computation — no running totals or seeds needed.
For reconciliation, kWh can be converted to currency via tariff rate.

**Billing source primacy (1Meter migration test, see**
**``docs/ops/1meter-billing-migration-protocol.md``):**

For each (account, reading_hour) the balance engine picks ONE source — SM
(``thundercloud`` / ``koios``) or 1M (``iot``) — based on the resolved
priority for that account. Resolution precedence:

  1. ``accounts.billing_meter_priority`` (per-account override)
  2. ``system_config(key='billing_meter_priority')`` (fleet default)
  3. Hardcoded ``'sm'`` fallback

The non-primary source is *not* added to consumption; it can be inspected
via :func:`get_balance_kwh_what_if` (parallel "what-if" computation used
by the Check Meters page) but is never written back to ``transactions``.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("cc-api.balance")


VALID_PRIORITIES = ("sm", "1m")
DEFAULT_PRIORITY = "sm"

# hourly_consumption.source values that count as the SM (SparkMeter) primary.
SM_SOURCES = ("thundercloud", "koios")
# hourly_consumption.source value that counts as the 1M (1Meter) check.
M1_SOURCES = ("iot",)


def _resolve_billing_priority(cur, account_number: str) -> str:
    """Resolve which meter source is authoritative for *account_number*.

    Tries per-account override first, then fleet default, then falls back
    to ``'sm'``. Always returns a value in :data:`VALID_PRIORITIES`.
    """
    # 1. Per-account override
    try:
        cur.execute(
            "SELECT billing_meter_priority FROM accounts WHERE account_number = %s LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if row and row[0] in VALID_PRIORITIES:
            return row[0]
    except Exception:
        # accounts.billing_meter_priority may not exist on a stale DB; fall through.
        cur.connection.rollback()

    # 2. Fleet default
    try:
        cur.execute(
            "SELECT value FROM system_config WHERE key = 'billing_meter_priority' LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0] in VALID_PRIORITIES:
            return row[0]
    except Exception:
        cur.connection.rollback()

    return DEFAULT_PRIORITY


def _consumption_kwh(cur, account_number: str, priority: str) -> float:
    """Sum live consumption from ``hourly_consumption`` for *account_number*
    using the source-priority rule for *priority* (``'sm'`` or ``'1m'``).

    Per (account, reading_hour) the chosen source wins; the other only fills
    a genuine gap (no row from the chosen source for that hour). This
    replaces the old ``MAX(kwh)`` dedup which silently allowed either
    source to override the other.
    """
    if priority not in VALID_PRIORITIES:
        priority = DEFAULT_PRIORITY

    cur.execute(
        """
        WITH per_hour AS (
            SELECT reading_hour,
                MAX(kwh) FILTER (WHERE source = ANY(%s)) AS sm_kwh,
                MAX(kwh) FILTER (WHERE source = ANY(%s)) AS m1_kwh
            FROM hourly_consumption
            WHERE account_number = %s
            GROUP BY reading_hour
        )
        SELECT COALESCE(SUM(
            CASE
                WHEN %s = 'sm' THEN COALESCE(sm_kwh, m1_kwh)
                WHEN %s = '1m' THEN COALESCE(m1_kwh, sm_kwh)
                ELSE NULL
            END
        ), 0)
        FROM per_hour
        """,
        (list(SM_SOURCES), list(M1_SOURCES), account_number, priority, priority),
    )
    return float(cur.fetchone()[0])


def get_balance_kwh(
    conn,
    account_number: str,
    *,
    priority: str | None = None,
) -> tuple[float, datetime | None]:
    """Compute the current kWh balance for an account.

    Returns ``(balance_kwh, as_of_timestamp)``.

    *priority* (``'sm'``/``'1m'``/``None``) overrides the resolved billing
    primacy for diagnostics; production callers (payments, dashboards,
    auto-cutoff) leave it ``None`` so the per-account / fleet default is
    used.

    Components:

    1. Payment credits from ``transactions`` (``is_payment=true`` →
       ``kwh_value`` added).
    2. Consumption debits from ``hourly_consumption``, source-priority
       aware (see :func:`_consumption_kwh`).
    3. Legacy consumption rows in ``transactions`` (``is_payment=false``
       — historic imports that pre-date hourly_consumption).
    """
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN is_payment THEN kwh_value ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN NOT is_payment THEN transaction_amount ELSE 0 END), 0)
        FROM transactions
        WHERE account_number = %s
        """,
        (account_number,),
    )
    total_payment_kwh, total_legacy_consumption = (float(v) for v in cur.fetchone())

    resolved = priority if priority in VALID_PRIORITIES else _resolve_billing_priority(
        cur, account_number
    )
    total_live_consumption = _consumption_kwh(cur, account_number, resolved)

    balance = round(total_payment_kwh - total_live_consumption - total_legacy_consumption, 4)
    return balance, datetime.now(timezone.utc)


def get_balance_kwh_what_if(
    conn, account_number: str
) -> dict:
    """Parallel "what-if" balance for the migration test.

    Returns a dict with both the live (priority-resolved) balance and the
    balance that *would* have been computed under the *opposite* priority.
    Diagnostic only — never written to ``transactions``. Surfaces on the
    Check Meters page.

    ::

        {
            "actual_priority": "sm",
            "actual_balance_kwh": 12.34,
            "what_if_priority": "1m",
            "what_if_balance_kwh": 12.20,
            "implied_balance_delta_kwh": -0.14,  # what_if - actual
        }
    """
    cur = conn.cursor()
    actual_priority = _resolve_billing_priority(cur, account_number)
    what_if_priority = "1m" if actual_priority == "sm" else "sm"

    actual_balance, _ = get_balance_kwh(conn, account_number, priority=actual_priority)
    what_if_balance, _ = get_balance_kwh(conn, account_number, priority=what_if_priority)

    return {
        "actual_priority": actual_priority,
        "actual_balance_kwh": round(actual_balance, 4),
        "what_if_priority": what_if_priority,
        "what_if_balance_kwh": round(what_if_balance, 4),
        "implied_balance_delta_kwh": round(what_if_balance - actual_balance, 4),
    }


def record_payment_kwh(
    conn,
    account_number: str,
    meter_id: str,
    amount_currency: float,
    rate: float,
    kwh_override: float | None = None,
    source: str = "portal",
    timestamp: datetime | None = None,
    payment_reference: str | None = None,
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
             is_payment, current_balance, source, payment_reference)
        VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s, %s)
        RETURNING id
    """, (
        account_number, meter_id, ts,
        amount_currency, rate, kwh_vended,
        new_balance, source, payment_reference,
    ))
    txn_id = cur.fetchone()[0]

    logger.info(
        "Payment: txn=%d acct=%s %s%.2f -> %.4f kWh @ %.2f  bal=%.4f kWh",
        txn_id, account_number, "M" if source != "koios" else "",
        amount_currency, kwh_vended, rate, new_balance,
    )

    # Phase 2 auto-cutoff hook (gated by RELAY_AUTO_TRIGGER_ENABLED).
    # If a payment lands but the balance is still <= 0 *and* the account is
    # on 1M-primary, we close the relay so the customer doesn't continue
    # drawing without credit. No-op while the env flag is off.
    if new_balance <= 0:
        try:
            from relay_control import maybe_auto_open_relay  # function-local: avoids circular import
            maybe_auto_open_relay(conn, account_number, reason="zero_balance_after_payment")
        except Exception as exc:  # noqa: BLE001 - never break the payment path
            logger.warning(
                "auto-cutoff hook failed for %s after txn=%d: %s",
                account_number, txn_id, exc,
            )

    return txn_id, kwh_vended, new_balance


def balance_to_currency(balance_kwh: float, rate: float) -> float:
    """Convert a kWh balance to currency equivalent at a given rate."""
    return round(balance_kwh * rate, 4)
