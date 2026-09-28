"""Keep ``monthly_transactions`` aligned with the live ``transactions`` ledger.

The dashboard portfolio widget reads ``transactions`` directly. Financial and
Analytics still use this aggregate. Rebuild it with the same grouping that
survives NULL ``meter_id`` and does not fan out across every meter an account
ever had.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("acdb-api.monthly-aggregates")

REBUILD_SQL = """
INSERT INTO monthly_transactions
    (account_number, meter_id, year_month, kwh_vended,
     amount_lsl, txn_count, community, source)
SELECT t.account_number, COALESCE(t.meter_id, ''),
       TO_CHAR(t.transaction_date, 'YYYY-MM'),
       SUM(COALESCE(t.kwh_value, 0)),
       SUM(COALESCE(t.transaction_amount, 0)),
       COUNT(*),
       MAX(COALESCE(m.community, '')),
       'import'::transaction_source
FROM transactions t
LEFT JOIN meters m ON t.meter_id = m.meter_id
GROUP BY t.account_number, COALESCE(t.meter_id, ''),
         TO_CHAR(t.transaction_date, 'YYYY-MM')
"""


def rebuild_monthly_transactions(conn) -> dict[str, Any]:
    """Replace ``monthly_transactions`` from ``transactions``. Commits."""
    cur = conn.cursor()
    cur.execute("SELECT count(*), max(year_month) FROM monthly_transactions")
    before_count, before_max = cur.fetchone()
    cur.execute("TRUNCATE monthly_transactions")
    cur.execute(REBUILD_SQL)
    conn.commit()
    cur.execute(
        "SELECT count(*), min(year_month), max(year_month), "
        "round(sum(amount_lsl)::numeric, 2) FROM monthly_transactions"
    )
    after_count, after_min, after_max, total = cur.fetchone()
    result = {
        "before_count": int(before_count or 0),
        "before_max": before_max,
        "after_count": int(after_count or 0),
        "after_min": after_min,
        "after_max": after_max,
        "total_amount": float(total or 0),
    }
    logger.info("monthly_transactions rebuilt: %s", result)
    return result
