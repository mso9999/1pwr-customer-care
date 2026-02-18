"""
Dashboard statistics endpoints.

Computes aggregated MWh consumed and '000 LSL sold per site
from the transactions table (consolidated history).
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from models import CurrentUser
from middleware import require_employee

logger = logging.getLogger("acdb-api.stats")

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _extract_site(account_number: str) -> str:
    """Extract site code from the last 3 chars of account number (e.g. 0003MAS -> MAS)."""
    if not account_number:
        return ""
    return account_number.strip()[-3:].upper()


@router.get("/site-summary")
def site_summary(user: CurrentUser = Depends(require_employee)):
    """
    Aggregate MWh consumed and '000 LSL revenue per site.

    Queries the transactions table directly.
    Site code is extracted from the last 3 chars of account_number.
    """
    results: Dict[str, Dict[str, float]] = {}  # site -> {mwh, lsl_thousands}
    source_table = "transactions"

    with _get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT account_number,
                       SUM(kwh_value) AS total_kwh,
                       SUM(transaction_amount) AS total_lsl
                FROM transactions
                GROUP BY account_number
            """)
            rows = cursor.fetchall()

            for row in rows:
                acct = str(row[0] or "").strip()
                site = _extract_site(acct)
                if not site or len(site) < 2:
                    continue

                if site not in results:
                    results[site] = {"mwh": 0.0, "lsl_thousands": 0.0}

                kwh = float(row[1] or 0)
                lsl = float(row[2] or 0)
                results[site]["mwh"] += kwh / 1000.0  # kWh -> MWh
                results[site]["lsl_thousands"] += lsl / 1000.0  # LSL -> '000 LSL

        except Exception as e:
            logger.warning("Failed to query transactions: %s", e)

    # Totals
    total_mwh = sum(s["mwh"] for s in results.values())
    total_lsl = sum(s["lsl_thousands"] for s in results.values())

    # Build per-site list sorted by site name
    sites = []
    for site_code in sorted(results.keys()):
        data = results[site_code]
        sites.append({
            "site": site_code,
            "mwh": round(data["mwh"], 2),
            "lsl_thousands": round(data["lsl_thousands"], 2),
        })

    return {
        "sites": sites,
        "totals": {
            "mwh": round(total_mwh, 2),
            "lsl_thousands": round(total_lsl, 2),
        },
        "source_table": source_table,
        "site_count": len(sites),
    }
