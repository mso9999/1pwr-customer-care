"""Resend SMS notifications missed due to CM.com token mismatch (May 9-12, 2026).

Run from /opt/cc-portal/backend with the venv active so balance_engine,
sms_outbound, and country_config are importable.

Usage:
  python3 resend_missed.py          # dry run
  python3 resend_missed.py --apply  # send for real
"""
# Canonical on-server resend path (venv + get_connection + balance_engine + send_gateway_sms).
# Alternate for laptops: resend_missed_notifications.py (requires DATABASE_URL; raw HTTP SMS).
import os
import sys
import time

WINDOW_START = "2026-05-09 00:00:00+00"

COUNTRY_CODE = os.environ.get("COUNTRY_CODE", "LS")

from customer_api import get_connection
from balance_engine import get_balance_kwh
from sms_outbound import send_gateway_sms
from payments import _get_tariff_rate


def main():
    do_apply = "--apply" in sys.argv

    with get_connection() as conn:
        cur = conn.cursor()

        # ---- Payment receipt resend ----
        cur.execute(
            """
            SELECT DISTINCT ON (account_number)
                   account_number, phone_normalized
            FROM sms_outbound_log
            WHERE sent_at >= %s
              AND trigger_ctx = 'payment_receipt'
              AND success = true
              AND account_number IS NOT NULL
            ORDER BY account_number, sent_at DESC
            """,
            (WINDOW_START,),
        )
        payment_accounts = cur.fetchall()

        # ---- Low balance alert re-run ----
        from low_balance_alerts import low_balance_tick
        alert_stats = low_balance_tick(conn, dry_run=not do_apply)
        if not do_apply:
            conn.rollback()
        else:
            conn.commit()

        # ---- Payment receipt catch-up ----
        sym = "M" if COUNTRY_CODE == "LS" else "CFA"
        sent = 0
        failed = 0
        skipped = 0

        print(f"\n{'='*60}")
        print(f"PAYMENT RECEIPT RESEND ({len(payment_accounts)} accounts)")
        print(f"{'='*60}")

        for account_number, phone in payment_accounts:
            if not phone or len("".join(c for c in str(phone) if c.isdigit())) < 8:
                skipped += 1
                continue

            try:
                bal_kwh, _ = get_balance_kwh(conn, account_number)
                rate = _get_tariff_rate(conn, account_number)
            except Exception:
                bal_kwh, rate = 0.0, 0.0

            bal_curr = round(bal_kwh * rate, 2)

            if COUNTRY_CODE == "BN":
                msg = (
                    f"Avis: Votre paiement recent pour {account_number} a ete traite. "
                    f"Solde actuel: {bal_curr:,.0f} {sym} ({bal_kwh:.1f} kWh). "
                    f"Desole pour le retard."
                )
            else:
                msg = (
                    f"Tsebiso: Patala ya hao ya moraorao bakeng sa {account_number} "
                    f"e se e sebetsa. Saleng ya hao: {sym}{bal_curr:.2f} "
                    f"({bal_kwh:.1f} kWh). Ts'oarelo ka ho dieha."
                )

            if do_apply:
                ok = send_gateway_sms(phone, msg, sms_type="balance",
                                      account_number=account_number,
                                      trigger="missed_resend_payment")
                if ok:
                    sent += 1
                else:
                    failed += 1
                time.sleep(0.3)
            else:
                sent += 1

        print(f"  Sent: {sent}, Failed: {failed}, Skipped (no phone): {skipped}")
        print(f"\n  Low-balance alerts: would_send={alert_stats.get('would_send', 0)}, "
              f"skipped_no_phone={alert_stats.get('skipped_no_phone', 0)}, "
              f"skipped_already_warned={alert_stats.get('skipped_already_warned', 0)}")
        print(f"\nRun with --apply to execute." if not do_apply else "\nDone.")

        cur.close()


if __name__ == "__main__":
    main()
