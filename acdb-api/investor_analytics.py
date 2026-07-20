"""
Investor Analytics API
======================

Investor-grade KPI endpoints distinct from the existing analytics explorer.
Provides structured data products that financier reports consume:

  - ``GET /api/asset-register`` — Operating asset register (one row per site)
  - ``GET /api/kpis`` — KPI time series (portfolio-wide or per-site)
  - ``GET /api/sites/{concession}/customers`` — Customer list with investor fields
  - ``GET /api/sites/{concession}/transactions`` — Transaction log with USD conversion
  - ``POST /api/admin/classify-customer`` — Override customer type

All endpoints require employee authentication.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from models import CurrentUser, CCRole
from middleware import require_employee, require_role
from customer_api import get_connection

logger = logging.getLogger("cc-api.investor-analytics")

router = APIRouter(prefix="/api/investor-analytics", tags=["investor-analytics"])

# ---------------------------------------------------------------------------
# In-memory cache (same pattern as stats.py)
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 300


def _get_cached(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.monotonic() - entry[0]) < CACHE_TTL_SECONDS:
            return entry[1]
    return None


def _set_cached(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# FX helper
# ---------------------------------------------------------------------------

def _fx_to_usd(conn, currency: str, d: date) -> float:
    """Get the USD conversion rate for a currency on a given date.

    Returns the most recent rate at or before the date.
    Falls back to hardcoded approximate rates if no table data.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rate_to_usd FROM fx_rates
             WHERE currency = %s AND effective_date <= %s
             ORDER BY effective_date DESC LIMIT 1
            """,
            (currency, d),
        )
        row = cur.fetchone()
        if row:
            return float(row[0])

    _fallback = {"LSL": 0.057, "XOF": 0.0016, "ZMW": 0.036, "USD": 1.0}
    return _fallback.get(currency, 1.0)


def _fx_to_usd_period(conn, currency: str, period: str) -> float:
    """Get FX rate for a YYYY-MM or YYYY-Q# period (uses period start date)."""
    if period.startswith("Q"):
        parts = period.replace("Q", "").split("-")
        if len(parts) == 2:
            q, yr = int(parts[0]), int(parts[1])
            month = (q - 1) * 3 + 1
            d = date(yr, month, 1)
        else:
            d = date.today()
    else:
        parts = period.split("-")
        if len(parts) == 2:
            d = date(int(parts[0]), int(parts[1]), 1)
        else:
            d = date.today()
    return _fx_to_usd(conn, currency, d)


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def _quarter_start(period: str) -> date:
    parts = period.replace("Q", "").split("-")
    q, yr = int(parts[0]), int(parts[1])
    month = (q - 1) * 3 + 1
    return date(yr, month, 1)


def _quarter_end(period: str) -> date:
    parts = period.replace("Q", "").split("-")
    q, yr = int(parts[0]), int(parts[1])
    month = q * 3
    if month == 12:
        return date(yr, 12, 31)
    import calendar
    last_day = calendar.monthrange(yr, month)[1]
    return date(yr, month, last_day)


def _month_start(period: str) -> date:
    parts = period.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def _month_end(period: str) -> date:
    parts = period.split("-")
    import calendar
    last_day = calendar.monthrange(int(parts[0]), int(parts[1]))[1]
    return date(int(parts[0]), int(parts[1]), last_day)


def _period_start(period: str) -> date:
    if period.startswith("Q"):
        return _quarter_start(period)
    return _month_start(period)


def _period_end(period: str) -> date:
    if period.startswith("Q"):
        return _quarter_end(period)
    return _month_end(period)


def _generate_periods(period_type: str, start: str, end: str) -> List[str]:
    """Generate a list of period strings from start to end."""
    periods: List[str] = []
    if period_type == "quarter":
        q, yr = int(start.replace("Q", "").split("-")[0]), int(start.replace("Q", "").split("-")[1])
        eq, eyr = int(end.replace("Q", "").split("-")[0]), int(end.replace("Q", "").split("-")[1])
        while (yr, q) <= (eyr, eq):
            periods.append(f"Q{q}-{yr}")
            q += 1
            if q > 4:
                q = 1
                yr += 1
    else:
        yr, mo = int(start.split("-")[0]), int(start.split("-")[1])
        eyr, emo = int(end.split("-")[0]), int(end.split("-")[1])
        while (yr, mo) <= (eyr, emo):
            periods.append(f"{yr}-{mo:02d}")
            mo += 1
            if mo > 12:
                mo = 1
                yr += 1
    return periods


def _default_periods(period_type: str, count: int = 8) -> Tuple[str, str]:
    """Return (start, end) for the last N periods ending now."""
    today = date.today()
    if period_type == "quarter":
        current_q = (today.month - 1) // 3 + 1
        end = f"Q{current_q}-{today.year}"
        start_q = current_q - count + 1
        start_yr = today.year
        while start_q <= 0:
            start_q += 4
            start_yr -= 1
        start = f"Q{start_q}-{start_yr}"
    else:
        end = f"{today.year}-{today.month:02d}"
        total_months = today.year * 12 + today.month - 1
        start_total = total_months - count + 1
        start_yr = start_total // 12
        start_mo = start_total % 12 + 1
        start = f"{start_yr}-{start_mo:02d}"
    return start, end


# ---------------------------------------------------------------------------
# GET /api/asset-register
# ---------------------------------------------------------------------------

@router.get("/asset-register")
def get_asset_register(
    user: CurrentUser = Depends(require_employee),
) -> List[Dict[str, Any]]:
    """Operating asset register — one row per energized site."""
    cache_key = "asset-register"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT sm.site_code, sm.full_name, sm.country, sm.region,
                       sm.status, sm.commissioning_date, sm.pv_kwp,
                       sm.battery_kwh, sm.thermal_kw, sm.concession_expiry,
                       sm.metering_tech, sm.concession_permit
                FROM site_metadata sm
                ORDER BY sm.country, sm.site_code
                """
            )
            sites = cur.fetchall()

            results: List[Dict[str, Any]] = []
            for site in sites:
                sc = site["site_code"]

                # Total connections (from accounts/customers)
                cur.execute(
                    """
                    SELECT COUNT(*) as total
                    FROM accounts a
                    JOIN customers c ON c.id = a.customer_id
                    WHERE a.site_code = %s
                    """,
                    (sc,),
                )
                total_row = cur.fetchone()
                total_connections = total_row["total"] if total_row else 0

                # Active connections (transaction in last 90 days)
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT t.account_number) as active
                    FROM transactions t
                    WHERE t.site_code = %s
                      AND t.transaction_date >= NOW() - INTERVAL '90 days'
                    """,
                    (sc,),
                )
                active_row = cur.fetchone()
                active_connections = active_row["active"] if active_row else 0

                # Customer type split
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN c.customer_type = 'HH' THEN 1 ELSE 0 END), 0) as hh,
                        COALESCE(SUM(CASE WHEN c.customer_type = 'SME' THEN 1 ELSE 0 END), 0) as sme,
                        COALESCE(SUM(CASE WHEN c.customer_type = 'C_I' THEN 1 ELSE 0 END), 0) as ci
                    FROM customers c
                    JOIN accounts a ON a.customer_id = c.id
                    WHERE a.site_code = %s
                    """,
                    (sc,),
                )
                type_row = cur.fetchone()
                hh_count = type_row["hh"] if type_row else 0
                sme_count = type_row["sme"] if type_row else 0
                ci_count = type_row["ci"] if type_row else 0

                # Avg tariff from sm_tariff_plans
                cur.execute(
                    """
                    SELECT AVG(rate_amount) as avg_rate, MAX(currency) as currency
                    FROM sm_tariff_plans WHERE site_code = %s
                    """,
                    (sc,),
                )
                tariff_row = cur.fetchone()
                avg_tariff_local = float(tariff_row["avg_rate"]) if tariff_row and tariff_row["avg_rate"] else None
                tariff_currency = tariff_row["currency"] if tariff_row else None

                # Convert tariff to USD
                avg_tariff_usd = None
                if avg_tariff_local and tariff_currency:
                    fx = _fx_to_usd(conn, tariff_currency, date.today())
                    avg_tariff_usd = round(avg_tariff_local * fx, 6)

                # Availability (trailing 90-day average)
                cur.execute(
                    """
                    SELECT AVG(availability_pct) as avg_avail
                    FROM site_availability
                    WHERE site_code = %s
                      AND period >= to_char(NOW() - INTERVAL '3 months', 'YYYY-MM')
                    """,
                    (sc,),
                )
                avail_row = cur.fetchone()
                availability = float(avail_row["avg_avail"]) if avail_row and avail_row["avg_avail"] else None

                results.append({
                    "site_code": sc,
                    "full_name": site["full_name"],
                    "country": site["country"],
                    "region": site["region"],
                    "status": site["status"],
                    "commissioning_date": site["commissioning_date"].isoformat() if site["commissioning_date"] else None,
                    "pv_kwp": float(site["pv_kwp"]) if site["pv_kwp"] else None,
                    "battery_kwh": float(site["battery_kwh"]) if site["battery_kwh"] else None,
                    "thermal_kw": float(site["thermal_kw"]) if site["thermal_kw"] else None,
                    "total_connections": total_connections,
                    "active_connections": active_connections,
                    "hh_count": hh_count,
                    "sme_count": sme_count,
                    "ci_count": ci_count,
                    "avg_tariff_local": avg_tariff_local,
                    "tariff_currency": tariff_currency,
                    "avg_tariff_usd_kwh": avg_tariff_usd,
                    "concession_expiry": site["concession_expiry"].isoformat() if site["concession_expiry"] else None,
                    "metering_tech": site["metering_tech"],
                    "concession_permit": site["concession_permit"],
                    "system_availability_pct": availability,
                })

        _set_cached(cache_key, results)
        return results


# ---------------------------------------------------------------------------
# GET /api/kpis
# ---------------------------------------------------------------------------

@router.get("/kpis")
def get_kpis(
    period: str = Query("quarter", regex="^(quarter|month)$"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    concession: Optional[str] = None,
    user: CurrentUser = Depends(require_employee),
) -> List[Dict[str, Any]]:
    """KPI time series (portfolio-wide or per-site) for the given period range."""
    if not start or not end:
        start, end = _default_periods(period, 8)

    cache_key = f"kpis:{period}:{start}:{end}:{concession or 'all'}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    periods = _generate_periods(period, start, end)
    results: List[Dict[str, Any]] = []

    with get_connection() as conn:
        for p in periods:
            p_start = _period_start(p)
            p_end = _period_end(p)

            site_filter = "AND t.site_code = %s" if concession else ""
            params: list = [p_start, p_end]
            if concession:
                params.append(concession)

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Energy and revenue from transactions
                cur.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(t.kwh_value), 0) as energy_kwh,
                        COALESCE(SUM(t.transaction_amount), 0) as revenue_local,
                        MAX(t.site_code) as sample_site
                    FROM transactions t
                    WHERE t.transaction_date >= %s AND t.transaction_date <= %s
                        {site_filter}
                    """,
                    params,
                )
                txn_row = cur.fetchone()

                energy_kwh = float(txn_row["energy_kwh"]) if txn_row else 0.0
                revenue_local = float(txn_row["revenue_local"]) if txn_row else 0.0

                # Determine currency and FX
                if concession:
                    from country_config import get_currency_for_site
                    currency = get_currency_for_site(concession)
                else:
                    currency = "LSL"
                fx = _fx_to_usd_period(conn, currency, p)
                revenue_usd = round(revenue_local * fx, 2)

                # Connection counts
                conn_filter = "AND a.site_code = %s" if concession else ""
                conn_params: list = [p_end]
                if concession:
                    conn_params.append(concession)

                cur.execute(
                    f"""
                    SELECT COUNT(*) as total
                    FROM accounts a
                    WHERE a.created_at <= %s {conn_filter}
                    """,
                    conn_params,
                )
                total_row = cur.fetchone()
                total_connections = total_row["total"] if total_row else 0

                # Active connections (txn in this period)
                active_params: list = [p_start, p_end]
                if concession:
                    active_params.append(concession)
                cur.execute(
                    f"""
                    SELECT COUNT(DISTINCT t.account_number) as active
                    FROM transactions t
                    WHERE t.transaction_date >= %s AND t.transaction_date <= %s
                        {site_filter}
                    """,
                    active_params,
                )
                active_row = cur.fetchone()
                active_connections = active_row["active"] if active_row else 0

                # New connections in period
                new_params: list = [p_start, p_end]
                if concession:
                    new_params.append(concession)
                cur.execute(
                    f"""
                    SELECT COUNT(*) as new_conn
                    FROM accounts a
                    WHERE a.created_at >= %s AND a.created_at <= %s {conn_filter}
                    """,
                    new_params,
                )
                new_row = cur.fetchone()
                new_connections = new_row["new_conn"] if new_row else 0

                # Productive use share (SME + C_I kWh / total kWh)
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(t.kwh_value), 0) as productive_kwh
                    FROM transactions t
                    JOIN customers c ON c.account_number = t.account_number
                    WHERE t.transaction_date >= %s AND t.transaction_date <= %s
                        {site_filter}
                        AND c.customer_type IN ('SME', 'C_I')
                    """,
                    params,
                )
                prod_row = cur.fetchone()
                productive_kwh = float(prod_row["productive_kwh"]) if prod_row else 0.0
                productive_use_share = round(productive_kwh / energy_kwh, 4) if energy_kwh > 0 else 0.0

                # Derived metrics
                months_in_period = 3 if period == "quarter" else 1
                arpu_usd_month = round(revenue_usd / active_connections / months_in_period, 2) if active_connections > 0 else 0.0
                avg_tariff_usd_kwh = round(revenue_usd / energy_kwh, 6) if energy_kwh > 0 else 0.0

                # Invoiced revenue (Phase 2 — if table has data)
                try:
                    inv_params: list = [p_start, p_end]
                    if concession:
                        inv_params.append(concession)
                    cur.execute(
                        f"""
                        SELECT COALESCE(SUM(kwh), 0) as inv_kwh,
                               COALESCE(SUM(amount_usd), 0) as inv_usd
                        FROM invoiced_revenue
                        WHERE invoice_date >= %s AND invoice_date <= %s
                            {'AND site_code = %s' if concession else ''}
                        """,
                        inv_params,
                    )
                    inv_row = cur.fetchone()
                    inv_kwh = float(inv_row["inv_kwh"]) if inv_row else 0.0
                    inv_usd = float(inv_row["inv_usd"]) if inv_row else 0.0
                    energy_kwh += inv_kwh
                    revenue_usd = round(revenue_usd + inv_usd, 2)
                except Exception:
                    pass

                # Availability (Phase 3 — if table has data)
                availability_pct = None
                try:
                    avail_params: list = [p if period == "month" else f"{p.split('-')[1]}-{((int(p.split('-')[0].replace('Q',''))-1)*3+1):02d}"]
                    if concession:
                        avail_params.append(concession)
                    cur.execute(
                        f"""
                        SELECT AVG(availability_pct) as avg_avail
                        FROM site_availability
                        WHERE period = %s {'AND site_code = %s' if concession else ''}
                        """,
                        avail_params,
                    )
                    avail_row = cur.fetchone()
                    if avail_row and avail_row["avg_avail"]:
                        availability_pct = float(avail_row["avg_avail"])
                except Exception:
                    pass

                # Financial metrics (Phase 3 — if table has data)
                opex_usd = None
                ebitda_usd = None
                capex_deployed_usd = None
                capex_cumulative_usd = None
                try:
                    fin_params: list = [p if period == "month" else f"{p.split('-')[1]}-{((int(p.split('-')[0].replace('Q',''))-1)*3+1):02d}"]
                    if concession:
                        fin_params.append(concession)
                    cur.execute(
                        f"""
                        SELECT SUM(opex_usd) as opex, SUM(ebitda_usd) as ebitda,
                               SUM(capex_deployed_usd) as capex_dep, SUM(capex_cumulative_usd) as capex_cum
                        FROM financial_metrics
                        WHERE period = %s {'AND site_code = %s' if concession else ''}
                        """,
                        fin_params,
                    )
                    fin_row = cur.fetchone()
                    if fin_row:
                        opex_usd = float(fin_row["opex"]) if fin_row["opex"] else None
                        ebitda_usd = float(fin_row["ebitda"]) if fin_row["ebitda"] else None
                        capex_deployed_usd = float(fin_row["capex_dep"]) if fin_row["capex_dep"] else None
                        capex_cumulative_usd = float(fin_row["capex_cum"]) if fin_row["capex_cum"] else None
                except Exception:
                    pass

                # Derived financial ratios
                opex_per_connection = round(opex_usd / active_connections, 2) if opex_usd and active_connections > 0 else None
                ebitda_per_connection = round(ebitda_usd / active_connections, 2) if ebitda_usd and active_connections > 0 else None
                capex_per_connection = round(capex_cumulative_usd / total_connections, 2) if capex_cumulative_usd and total_connections > 0 else None

                results.append({
                    "period": p,
                    "concession": concession or "ALL",
                    "total_connections": total_connections,
                    "active_connections": active_connections,
                    "new_connections": new_connections,
                    "energy_kwh": round(energy_kwh, 2),
                    "revenue_usd": revenue_usd,
                    "arpu_usd_month": arpu_usd_month,
                    "avg_tariff_usd_kwh": avg_tariff_usd_kwh,
                    "productive_use_share": productive_use_share,
                    "system_availability_pct": availability_pct,
                    "opex_usd": opex_usd,
                    "opex_per_connection_usd": opex_per_connection,
                    "ebitda_usd": ebitda_usd,
                    "ebitda_per_connection_usd": ebitda_per_connection,
                    "capex_deployed_usd": capex_deployed_usd,
                    "capex_cumulative_usd": capex_cumulative_usd,
                    "capex_per_connection_usd": capex_per_connection,
                })

        _set_cached(cache_key, results)
        return results


# ---------------------------------------------------------------------------
# GET /api/sites/{concession}/customers
# ---------------------------------------------------------------------------

@router.get("/sites/{concession}/customers")
def get_site_customers(
    concession: str,
    status: Optional[str] = Query(None, regex="^(active|inactive|disconnected)$"),
    customer_type: Optional[str] = Query(None, regex="^(HH|SME|C_I|UNK)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    """Customer list for a site with investor-grade fields."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conditions = ["a.site_code = %s"]
            params: list = [concession]

            if customer_type:
                conditions.append("c.customer_type = %s")
                params.append(customer_type)

            where = " AND ".join(conditions)
            offset = (page - 1) * limit

            cur.execute(
                f"""
                SELECT c.account_number, c.name, c.customer_type,
                       c.phone, c.plot_number,
                       a.site_code, a.created_at as connection_date,
                       c.customer_status,
                       (SELECT MAX(t.transaction_date)
                        FROM transactions t
                        WHERE t.account_number = c.account_number) as last_transaction_date,
                       (SELECT tp.plan_name
                        FROM sm_tariff_plans tp
                        WHERE tp.site_code = a.site_code
                          AND tp.customer_type = c.customer_type
                        LIMIT 1) as tariff_plan
                FROM customers c
                JOIN accounts a ON a.customer_id = c.id
                WHERE {where}
                ORDER BY c.account_number
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()

            # Get total count
            cur.execute(
                f"""
                SELECT COUNT(*) as total
                FROM customers c
                JOIN accounts a ON a.customer_id = c.id
                WHERE {where}
                """,
                params,
            )
            total = cur.fetchone()["total"]

            customers: List[Dict[str, Any]] = []
            for row in rows:
                cust_status = row.get("customer_status") or "active"
                if status and cust_status != status:
                    continue
                customers.append({
                    "account_number": row["account_number"],
                    "customer_name": row["name"],
                    "customer_type": row["customer_type"] or "UNK",
                    "phone": row["phone"],
                    "site_code": row["site_code"],
                    "connection_date": row["connection_date"].isoformat() if row["connection_date"] else None,
                    "status": cust_status,
                    "last_transaction_date": row["last_transaction_date"].isoformat() if row["last_transaction_date"] else None,
                    "tariff_plan": row["tariff_plan"],
                    "plot_number": row["plot_number"],
                })

            return {
                "concession": concession,
                "page": page,
                "limit": limit,
                "total": total,
                "customers": customers,
            }


# ---------------------------------------------------------------------------
# GET /api/sites/{concession}/transactions
# ---------------------------------------------------------------------------

@router.get("/sites/{concession}/transactions")
def get_site_transactions(
    concession: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    customer_type: Optional[str] = Query(None, regex="^(HH|SME|C_I|UNK)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    """Transaction log for a site with USD conversion."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conditions = ["t.site_code = %s"]
            params: list = [concession]

            if date_from:
                conditions.append("t.transaction_date >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("t.transaction_date <= %s")
                params.append(date_to)
            if customer_type:
                conditions.append("c.customer_type = %s")
                params.append(customer_type)

            where = " AND ".join(conditions)
            offset = (page - 1) * limit

            from country_config import get_currency_for_site
            currency = get_currency_for_site(concession)

            cur.execute(
                f"""
                SELECT t.account_number, t.transaction_date, t.transaction_amount,
                       t.kwh_value, t.rate_used, c.customer_type, c.name as customer_name,
                       (SELECT tp.plan_name
                        FROM sm_tariff_plans tp
                        WHERE tp.site_code = t.site_code
                          AND tp.customer_type = c.customer_type
                        LIMIT 1) as tariff_plan
                FROM transactions t
                LEFT JOIN customers c ON c.account_number = t.account_number
                WHERE {where}
                ORDER BY t.transaction_date DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()

            # Total count
            cur.execute(
                f"""
                SELECT COUNT(*) as total
                FROM transactions t
                LEFT JOIN customers c ON c.account_number = t.account_number
                WHERE {where}
                """,
                params,
            )
            total = cur.fetchone()["total"]

            txns: List[Dict[str, Any]] = []
            for row in rows:
                amount_local = float(row["transaction_amount"]) if row["transaction_amount"] else 0.0
                txn_date = row["transaction_date"]
                fx = _fx_to_usd(conn, currency, txn_date.date() if hasattr(txn_date, 'date') else txn_date) if txn_date else _fx_to_usd(conn, currency, date.today())
                amount_usd = round(amount_local * fx, 2)

                txns.append({
                    "account_number": row["account_number"],
                    "customer_name": row["customer_name"],
                    "customer_type": row["customer_type"] or "UNK",
                    "timestamp": txn_date.isoformat() if txn_date else None,
                    "kwh": float(row["kwh_value"]) if row["kwh_value"] else 0.0,
                    "amount_local": amount_local,
                    "currency": currency,
                    "amount_usd": amount_usd,
                    "rate_used": float(row["rate_used"]) if row["rate_used"] else None,
                    "tariff_plan": row["tariff_plan"],
                })

            return {
                "concession": concession,
                "page": page,
                "limit": limit,
                "total": total,
                "transactions": txns,
            }


# ---------------------------------------------------------------------------
# POST /api/admin/classify-customer
# ---------------------------------------------------------------------------

class ClassifyCustomerRequest(BaseModel):
    account_number: str
    customer_type: str
    reason: Optional[str] = None


@router.post("/admin/classify-customer")
def classify_customer(
    req: ClassifyCustomerRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin, CCRole.onm_team)),
) -> Dict[str, Any]:
    """Override customer type for a specific account (admin only)."""
    if req.customer_type not in ("HH", "SME", "C_I", "UNK"):
        raise HTTPException(status_code=400, detail="customer_type must be one of: HH, SME, C_I, UNK")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customer_type_overrides
                    (account_number, customer_type, reason, overridden_by, overridden_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (account_number) DO UPDATE SET
                    customer_type = EXCLUDED.customer_type,
                    reason = EXCLUDED.reason,
                    overridden_by = EXCLUDED.overridden_by,
                    overridden_at = NOW()
                """,
                (req.account_number, req.customer_type, req.reason, user.user_id),
            )

            # Also update the customers table
            cur.execute(
                """
                UPDATE customers SET customer_type = %s
                WHERE account_number = %s AND EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'customers' AND column_name = 'customer_type'
                )
                """,
                (req.customer_type, req.account_number),
            )
        conn.commit()

        # Invalidate cache
        with _cache_lock:
            _cache.clear()

        logger.info(
            "Customer type override: %s → %s by %s (reason: %s)",
            req.account_number, req.customer_type, user.user_id, req.reason,
        )
        return {
            "account_number": req.account_number,
            "customer_type": req.customer_type,
            "overridden_by": user.user_id,
        }
