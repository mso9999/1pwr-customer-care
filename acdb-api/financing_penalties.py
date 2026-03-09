"""
Automatic financing penalty application.

Run periodically (daily via cron) to check active financing agreements
and apply penalties when payments are overdue per the agreement terms.

Usage:
    python financing_penalties.py          # one-shot run
    (or integrate into sync_consumption.sh cron)
"""

import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("financing-penalties")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)


def check_and_apply_penalties():
    """Scan active agreements and apply penalties where overdue."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        cur.execute("""
            SELECT id, outstanding_balance, penalty_rate,
                   penalty_grace_days, penalty_interval_days
            FROM financing_agreements
            WHERE status = 'active' AND outstanding_balance > 0
                  AND penalty_rate > 0
        """)
        agreements = cur.fetchall()

        applied = 0
        for agr_id, balance, rate, grace_days, interval_days in agreements:
            balance = float(balance)
            rate = float(rate)

            cur.execute("""
                SELECT MAX(created_at) FROM financing_ledger
                WHERE agreement_id = %s AND entry_type = 'payment'
            """, (agr_id,))
            row = cur.fetchone()
            last_payment = row[0] if row and row[0] else None

            if not last_payment:
                cur.execute(
                    "SELECT created_at FROM financing_agreements WHERE id = %s",
                    (agr_id,),
                )
                last_payment = cur.fetchone()[0]

            if last_payment.tzinfo is None:
                from datetime import timezone as tz
                last_payment = last_payment.replace(tzinfo=tz.utc)

            days_since = (now - last_payment).days
            if days_since < grace_days:
                continue

            cur.execute("""
                SELECT MAX(created_at) FROM financing_ledger
                WHERE agreement_id = %s AND entry_type = 'penalty'
            """, (agr_id,))
            row = cur.fetchone()
            last_penalty = row[0] if row and row[0] else None

            if last_penalty:
                if last_penalty.tzinfo is None:
                    last_penalty = last_penalty.replace(tzinfo=timezone.utc)
                days_since_penalty = (now - last_penalty).days
                if days_since_penalty < interval_days:
                    continue

            penalty_amount = round(balance * rate, 2)
            new_balance = round(balance + penalty_amount, 2)

            cur.execute("""
                INSERT INTO financing_ledger
                    (agreement_id, entry_type, amount, balance_after, note, created_by)
                VALUES (%s, 'penalty', %s, %s, %s, 'system')
            """, (
                agr_id, -penalty_amount, new_balance,
                f"Automatic penalty: {rate*100:.1f}% of {balance:.2f} = {penalty_amount:.2f} "
                f"({days_since} days since last payment)",
            ))

            cur.execute(
                "UPDATE financing_agreements SET outstanding_balance = %s WHERE id = %s",
                (new_balance, agr_id),
            )
            applied += 1
            logger.info(
                "Penalty applied: agreement=%d amount=%.2f new_balance=%.2f",
                agr_id, penalty_amount, new_balance,
            )

        conn.commit()
        logger.info("Penalty check complete: %d/%d agreements penalized", applied, len(agreements))
        return applied

    except Exception:
        conn.rollback()
        logger.exception("Penalty check failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    count = check_and_apply_penalties()
    sys.exit(0)
