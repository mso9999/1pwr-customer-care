"""Onboarding portfolio dashboards (workbook Totals / Monthly parity)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from customer_api import get_connection
from middleware import require_employee, CurrentUser

router = APIRouter(prefix="/api/onboarding/dashboard", tags=["onboarding-dashboard"])

SITE_CODES = ("ALL", "MAT", "TLH", "MAK", "SHG", "MAS", "SEH", "KET", "LSB")


@router.get("/summary")
def onboarding_summary(
    site: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    site_filter = ""
    params: list = []
    if site and site.upper() != "ALL":
        site_filter = "AND c.community = %s"
        params.append(site.upper())

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
              COUNT(DISTINCT c.id) AS registered,
              COUNT(DISTINCT c.id) FILTER (WHERE c.date_service_connected IS NOT NULL) AS connected,
              COUNT(DISTINCT c.id) FILTER (WHERE c.date_service_connected IS NULL) AS pending,
              COUNT(DISTINCT c.id) FILTER (WHERE c.meter_installed = TRUE) AS meter_installed,
              COUNT(DISTINCT c.id) FILTER (WHERE c.customer_commissioned = TRUE) AS commissioned
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            LEFT JOIN meters m ON m.account_number = a.account_number
            WHERE TRUE {site_filter}
            """,
            params,
        )
        row = cur.fetchone()
        return {
            "site": site or "ALL",
            "registered": int(row[0] or 0),
            "connected": int(row[1] or 0),
            "pending": int(row[2] or 0),
            "meter_installed": int(row[3] or 0),
            "commissioned": int(row[4] or 0),
        }


@router.get("/monthly")
def onboarding_monthly(
    year: int = Query(default_factory=lambda: date.today().year),
    user: CurrentUser = Depends(require_employee),
):
    results = []
    with get_connection() as conn:
        cur = conn.cursor()
        for site in SITE_CODES:
            site_filter = ""
            params: list = [year]
            if site != "ALL":
                site_filter = "AND c.community = %s"
                params.append(site)
            cur.execute(
                f"""
                SELECT date_trunc('month', c.customer_commissioned_date)::date AS month,
                       COUNT(DISTINCT c.id) AS commissioned
                FROM customers c
                LEFT JOIN accounts a ON a.customer_id = c.id
                LEFT JOIN meters m ON m.account_number = a.account_number
                WHERE c.customer_commissioned = TRUE
                  AND c.customer_commissioned_date IS NOT NULL
                  AND EXTRACT(YEAR FROM c.customer_commissioned_date) = %s
                  {site_filter}
                GROUP BY 1
                ORDER BY 1
                """,
                params,
            )
            months = [{"month": r[0].isoformat(), "commissioned": int(r[1])} for r in cur.fetchall()]
            results.append({"site": site, "months": months})
    return {"year": year, "sites": results}
