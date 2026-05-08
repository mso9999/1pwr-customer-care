"""
Low-balance customer SMS using 1PDB balances (balance_engine) + gateway send.

Legacy SMSComms ``customer_info.json`` / SparkMeter snapshot logic is replaced by:

- Thresholds in ``system_config`` (editable by O&M under Tariff → country fees card):
  ``low_balance_kwh_threshold``, ``low_balance_kwh_clear`` — resolved via
  ``country_fees.get_low_balance_thresholds()`` (per-country defaults in ``country_config``).
- Per-account state: ``accounts.low_balance_alert_sent_at``
"""

from __future__ import annotations

import logging
from typing import Any

from balance_engine import get_balance_kwh
from country_config import COUNTRY
from country_fees import get_low_balance_thresholds
from customer_api import get_connection
from sms_outbound import send_gateway_sms

logger = logging.getLogger("cc-api.low-balance-alerts")


def format_alert_message(account_number: str, balance_kwh: float, balance_currency: float) -> str:
    cur_code = COUNTRY.currency
    if COUNTRY.code == "BN":
        return (
            f"Alerte: solde bas pour {account_number}. "
            f"Reste env. {balance_currency:.0f} {cur_code} "
            f"({balance_kwh:.1f} kWh). Rechargez vite."
        )
    # Lesotho Sesotho (aligned with legacy SMSComms wording)
    return (
        f"Motlakase oa ntlo ea {account_number} o se o k'a fela haufi. "
        f"O boloking bakeng sa M{balance_currency:.2f} ({balance_kwh:.1f} kWh)."
    )


def low_balance_tick(conn, *, dry_run: bool = False) -> dict[str, Any]:
    """Scan active meter accounts; send/clear low-balance alerts. Returns stats."""
    from payments import _get_tariff_rate as tariff_for_account

    cur = conn.cursor()
    warn_kwh, clear_kwh = get_low_balance_thresholds(conn)

    cur.execute(
        """
        SELECT a.account_number,
               a.low_balance_alert_sent_at,
               COALESCE(
                   NULLIF(TRIM(c.cell_phone_1), ''),
                   NULLIF(TRIM(c.phone), ''),
                   NULLIF(TRIM(c.cell_phone_2), '')
               ) AS phone
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE EXISTS (
            SELECT 1 FROM meters m
            WHERE m.account_number = a.account_number AND m.status = 'active'
        )
        """
    )
    rows = cur.fetchall()

    stats: dict[str, Any] = {
        "warn_kwh": warn_kwh,
        "clear_kwh": clear_kwh,
        "accounts_seen": len(rows),
        "cleared": 0,
        "sent": 0,
        "would_send": 0,
        "skipped_no_phone": 0,
        "skipped_already_warned": 0,
        "dry_run": dry_run,
    }

    for account_number, sent_at, phone in rows:
        account_number = str(account_number).strip()
        if not phone:
            stats["skipped_no_phone"] += 1
            continue

        bal_kwh, _ = get_balance_kwh(conn, account_number)
        rate = tariff_for_account(conn, account_number)
        bal_currency = bal_kwh * rate

        if bal_kwh >= clear_kwh:
            if sent_at is not None:
                if not dry_run:
                    cur.execute(
                        "UPDATE accounts SET low_balance_alert_sent_at = NULL "
                        "WHERE account_number = %s",
                        (account_number,),
                    )
                    conn.commit()
                stats["cleared"] += 1
            continue

        if bal_kwh > warn_kwh:
            continue

        if sent_at is not None:
            stats["skipped_already_warned"] += 1
            continue

        msg = format_alert_message(account_number, bal_kwh, round(bal_currency, 2))
        if dry_run:
            stats["would_send"] += 1
            logger.info("dry-run: would SMS %s (%s)", account_number, phone)
            continue

        ok = send_gateway_sms(phone, msg, sms_type="balance")
        if ok:
            cur.execute(
                "UPDATE accounts SET low_balance_alert_sent_at = NOW() "
                "WHERE account_number = %s",
                (account_number,),
            )
            conn.commit()
            stats["sent"] += 1
        else:
            logger.warning("SMS send failed for %s — not marking sent_at", account_number)

    return stats
