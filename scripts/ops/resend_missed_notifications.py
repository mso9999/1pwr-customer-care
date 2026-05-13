"""Resend SMS notifications missed due to CM.com token mismatch (May 9-12, 2026).

Sends an updated balance statement to each account that should have received
a payment receipt or low-balance alert during the bad-token window.
"""
# Prefer resend_missed.py on the CC host venv (get_connection + balance_engine + send_gateway_sms).
# This file is a standalone DB+HTTP fallback; DATABASE_URL is required (no credential defaults).
# See resend_missed.py docstring for usage from /opt/cc-portal/backend.
import os
import sys
import time

import psycopg2
import requests

SMS_SERVER_URL = os.environ.get(
    "SMS_SERVER_URL",
    "https://sms.1pwrafrica.com/",
)
COUNTRY_CODE = os.environ.get("COUNTRY_CODE", "LS")
SYM = "M" if COUNTRY_CODE == "LS" else "CFA"

WINDOW_START = "2026-05-09 00:00:00+00"  # token was bad since at least May 9


def get_affected_accounts(cur):
    """Get unique account+phone pairs that had messages during bad-token window."""
    cur.execute(
        """
        SELECT DISTINCT ON (account_number)
               account_number, phone_normalized
        FROM sms_outbound_log
        WHERE sent_at >= %s
          AND trigger_ctx IN ('payment_receipt', 'low_balance_alert')
          AND success = true
          AND account_number IS NOT NULL
        ORDER BY account_number, sent_at DESC
        """,
        (WINDOW_START,),
    )
    return cur.fetchall()


def get_current_balance(cur, account_number):
    """Get balance in kWh from 1PDB via balance_engine."""
    try:
        cur.execute(
            "SELECT balance_kwh FROM balance_corrections WHERE account_number = %s "
            "ORDER BY applies_at DESC LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return 0.0


def get_tariff_rate(cur, account_number):
    """Get tariff rate for account."""
    try:
        cur.execute(
            """
            SELECT rate FROM system_config
            WHERE key = 'tariff_rate' LIMIT 1
            """,
        )
        row = cur.fetchone()
        if row and row[0]:
            return float(row[0])
    except Exception:
        pass
    return 0.0


def send_sms(phone, message):
    """Send SMS via gateway."""
    import urllib.parse
    url = (
        f"{SMS_SERVER_URL}generate_and_send.php"
        f"?message={urllib.parse.quote(message)}"
        f"&type=balance&number={phone}"
    )
    try:
        r = requests.get(url, timeout=20)
        return r.status_code == 200
    except Exception:
        return False


def main():
    do_apply = "--apply" in sys.argv

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        print(
            "error: DATABASE_URL must be set to a PostgreSQL connection string "
            "(same DB as Customer Care / 1PDB).",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    accounts = get_affected_accounts(cur)
    print(f"Affected accounts with bad-token SMS: {len(accounts)}")

    rate = 0.0
    try:
        cur.execute("SELECT rate FROM system_config WHERE key = 'tariff_rate' LIMIT 1")
        row = cur.fetchone()
        if row and row[0]:
            rate = float(row[0])
    except Exception:
        rate = 0.0

    sent = 0
    failed = 0
    skipped_no_phone = 0

    for account_number, phone in accounts:
        if not phone or len("".join(c for c in str(phone) if c.isdigit())) < 8:
            skipped_no_phone += 1
            continue

        bal_kwh = get_current_balance(cur, account_number)
        bal_curr = round(bal_kwh * rate, 2)

        if COUNTRY_CODE == "BN":
            msg = (
                f"Avis: Votre paiement recent pour {account_number} a ete traite. "
                f"Solde actuel: {bal_curr:,.0f} {SYM} ({bal_kwh:.1f} kWh). "
                f"Desole pour le retard de confirmation."
            )
        else:
            msg = (
                f"Tsebiso: Patala ya hao ya moraorao bakeng sa {account_number} "
                f"e se e sebetsa. Saleng ya hao: {SYM}{bal_curr:.2f} "
                f"({bal_kwh:.1f} kWh). Ts'oarelo ka ho dieha ha ho netefatsa."
            )

        if do_apply:
            ok = send_sms(phone, msg)
            if ok:
                sent += 1
            else:
                failed += 1
            time.sleep(0.3)  # rate limit
        else:
            sent += 1

    print(f"\n{'='*50}")
    print(f"RESEND PLAN")
    print(f"{'='*50}")
    print(f"  Accounts to notify:   {len(accounts)}")
    print(f"  Skipped (no phone):   {skipped_no_phone}")
    print(f"  Would send:           {sent if not do_apply else sent}")
    if failed:
        print(f"  Failed:               {failed}")
    print(f"{'='*50}")

    if not do_apply:
        # Show sample
        print("\nSample messages:")
        for account_number, phone in accounts[:3]:
            if phone and len("".join(c for c in str(phone) if c.isdigit())) >= 8:
                bal_kwh = get_current_balance(cur, account_number)
                bal_curr = round(bal_kwh * rate, 2)
                print(f"  {account_number} -> {phone}: bal={bal_kwh:.1f}kWh")
        print(f"\nRun with --apply to send {sent} messages.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
