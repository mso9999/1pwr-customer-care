"""
Low-balance customer SMS using 1PDB balances (balance_engine) + gateway send.

Legacy SMSComms ``customer_info.json`` / SparkMeter snapshot logic is replaced by:

- Thresholds in ``system_config`` (editable by O&M under Tariff → country fees card):
  ``low_balance_kwh_threshold``, ``low_balance_kwh_clear`` — resolved via
  ``country_fees.get_low_balance_thresholds()`` (per-country defaults in ``country_config``).
- Max alerts per local calendar day: ``low_balance_alert_max_per_day`` (default 2),
  ``country_fees.get_sms_rate_limit_settings()``.
- Per-account state: ``accounts.low_balance_alert_sent_at``,
  ``low_balance_alerts_local_date``, ``low_balance_alerts_sent_today``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from balance_live import get_display_balance
from country_config import COUNTRY
from country_fees import get_low_balance_thresholds, get_sms_rate_limit_settings
from customer_api import get_connection
from sms_outbound import send_gateway_sms

try:  # Phase 3: mirror SMS into the app inbox + FCM (best-effort).
    from app_notifications import mirror_to_app
except Exception:  # noqa: BLE001
    def mirror_to_app(*a, **k):  # type: ignore[misc]
        return None

logger = logging.getLogger("cc-api.low-balance-alerts")


def format_alert_message(account_number: str, balance_kwh: float, balance_currency: float) -> str:
    cur_code = COUNTRY.currency
    if COUNTRY.code == "BN":
        return (
            f"Alerte: solde bas pour {account_number}. "
            f"Reste env. {balance_currency:.0f} {cur_code} "
            f"({balance_kwh:.1f} kWh). Rechargez vite."
        )
    # Lesotho Sesotho (wording corrected per O&M request 2026-06-09)
    return (
        f"Motlakase oa ntlo ea {account_number} o se o ka fela haufi. "
        f"Ho setse motlakase oa boleng ba M{balance_currency:.2f} ({balance_kwh:.1f} kWh)."
    )


def _exempt_uncommissioned(conn) -> bool:
    """Whether to skip not-yet-commissioned customers (system_config, default ON).

    Per O&M (2026-06-10, 0286SHG): customers who have not been commissioned must not
    receive low-balance SMS. Default exempt. Benin keeps the legacy behavior via
    ``low_balance_exempt_uncommissioned = 0`` because its ``customer_commissioned``
    flags are not yet maintained (ALL BN customers are False); remove that override
    once BN commissioning data is backfilled.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM system_config WHERE key = 'low_balance_exempt_uncommissioned' LIMIT 1"
    )
    row = cur.fetchone()
    if not row or row[0] is None or str(row[0]).strip() == "":
        return True
    return str(row[0]).strip().lower() not in ("0", "false", "no", "off")


def low_balance_tick(conn, *, dry_run: bool = False) -> dict[str, Any]:
    """Scan active meter accounts; send/clear low-balance alerts. Returns stats."""
    from payments import _get_tariff_rate as tariff_for_account

    cur = conn.cursor()
    warn_kwh, clear_kwh = get_low_balance_thresholds(conn)
    limits = get_sms_rate_limit_settings(conn)
    max_per_day = int(limits["low_balance_alert_max_per_day"])
    tz = ZoneInfo(COUNTRY.timezone)
    today_local = datetime.now(tz).date()
    exempt_uncommissioned = _exempt_uncommissioned(conn)

    commissioned_filter = (
        "AND COALESCE(c.customer_commissioned, FALSE)" if exempt_uncommissioned else ""
    )
    cur.execute(
        f"""
        SELECT a.account_number,
               a.low_balance_alert_sent_at,
               COALESCE(
                   NULLIF(TRIM(c.cell_phone_1), ''),
                   NULLIF(TRIM(c.phone), ''),
                   NULLIF(TRIM(c.cell_phone_2), '')
               ) AS phone,
               a.low_balance_alerts_local_date,
               COALESCE(a.low_balance_alerts_sent_today, 0) AS sent_today
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE EXISTS (
            SELECT 1 FROM meters m
            WHERE m.account_number = a.account_number AND m.status = 'active'
        )
        {commissioned_filter}
        """
    )
    rows = cur.fetchall()

    stats: dict[str, Any] = {
        "warn_kwh": warn_kwh,
        "clear_kwh": clear_kwh,
        "exempt_uncommissioned": exempt_uncommissioned,
        "low_balance_alert_max_per_day": max_per_day,
        "accounts_seen": len(rows),
        "cleared": 0,
        "sent": 0,
        "would_send": 0,
        "skipped_no_phone": 0,
        "skipped_daily_cap": 0,
        "dry_run": dry_run,
    }

    for (
        account_number,
        sent_at,
        phone,
        alerts_local_date,
        sent_today_db,
    ) in rows:
        account_number = str(account_number).strip()
        phone_digits = "".join(c for c in str(phone or "") if c.isdigit())
        if len(phone_digits) < 8:
            stats["skipped_no_phone"] += 1
            continue

        # Read the freshest balance we already have (live cache when the tiered
        # scheduler / activity has refreshed it) WITHOUT forcing a per-account
        # SparkMeter pull here — scanning every account would blow the Koios daily
        # budget. refresh=False falls back to the engine balance when no live value.
        bal_kwh, _ = get_display_balance(conn, account_number, refresh=False)
        rate = tariff_for_account(conn, account_number)
        bal_currency = bal_kwh * rate

        if bal_kwh >= clear_kwh:
            if sent_at is not None or alerts_local_date is not None or (sent_today_db or 0) > 0:
                if not dry_run:
                    cur.execute(
                        """
                        UPDATE accounts SET
                            low_balance_alert_sent_at = NULL,
                            low_balance_alerts_local_date = NULL,
                            low_balance_alerts_sent_today = 0
                        WHERE account_number = %s
                        """,
                        (account_number,),
                    )
                    conn.commit()
                stats["cleared"] += 1
            continue

        if bal_kwh > warn_kwh:
            continue

        if alerts_local_date is None or alerts_local_date != today_local:
            effective_sent_today = 0
        else:
            effective_sent_today = int(sent_today_db or 0)

        if effective_sent_today >= max_per_day:
            stats["skipped_daily_cap"] += 1
            continue

        msg = format_alert_message(account_number, bal_kwh, round(bal_currency, 2))
        if dry_run:
            stats["would_send"] += 1
            logger.info("dry-run: would SMS %s (%s)", account_number, phone)
            continue

        ok = send_gateway_sms(
            phone_digits,
            msg,
            sms_type="balance",
            account_number=account_number,
            trigger="low_balance_alert",
        )
        if ok:
            new_count = effective_sent_today + 1
            cur.execute(
                """
                UPDATE accounts SET
                    low_balance_alert_sent_at = NOW(),
                    low_balance_alerts_local_date = %s,
                    low_balance_alerts_sent_today = %s
                WHERE account_number = %s
                """,
                (today_local, new_count, account_number),
            )
            conn.commit()
            stats["sent"] += 1
            mirror_to_app(
                account_number,
                "low_balance",
                "1PWR",
                msg,
                {"balance_kwh": bal_kwh, "balance_currency": bal_currency},
            )
        else:
            logger.warning("SMS send failed for %s — not updating counters", account_number)

    return stats
