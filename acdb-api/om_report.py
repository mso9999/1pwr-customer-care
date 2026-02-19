"""
O&M Quarterly Report data endpoints.

Auto-generates analytics from PostgreSQL data to mirror the figures in
the SMP Operations & Maintenance Quarterly Report:
  - Customer statistics per site (total, active, new per quarter)
  - Customer connection growth over time (quarterly)
  - Consumption per site per quarter (kWh)
  - Sales/revenue per site per quarter (LSL)
  - Cumulative consumption and sales trends
  - Generation vs consumption per site
  - Average consumption per customer trends
  - Site overview (concession list with districts)
"""

import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query

from models import CurrentUser
from middleware import require_employee

logger = logging.getLogger("acdb-api.om-report")

router = APIRouter(prefix="/api/om-report", tags=["om-report"])

from country_config import (
    SITE_ABBREV, KNOWN_SITES, SITE_DISTRICTS,
    COUNTRY, CURRENCY, CURRENCY_SYMBOL,
)


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _extract_site(account_number: str) -> str:
    """Extract site code from the last 3 chars of account number.

    Only returns a value if it matches a known site code, to prevent
    meter serial suffixes (e.g., '7E7', '3F0') from polluting charts.
    """
    if not account_number:
        return ""
    candidate = account_number.strip()[-3:].upper()
    if candidate in KNOWN_SITES:
        return candidate
    return ""


def _date_to_quarter(dt) -> str:
    """Convert a date/datetime to 'YYYY QN' string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(dt.strip(), fmt)
                break
            except (ValueError, AttributeError):
                continue
        else:
            return ""
    try:
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year} Q{q}"
    except (AttributeError, TypeError):
        return ""


def _day_key(val) -> Optional[str]:
    """Normalise a date/datetime/string to 'YYYY-MM-DD' for unique-day tracking."""
    if val is None:
        return None
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, str):
        return val[:10]
    return None


# ---------------------------------------------------------------------------
# 1. Portfolio Overview
# ---------------------------------------------------------------------------

@router.get("/overview")
def report_overview(user: CurrentUser = Depends(require_employee)):
    """Summary statistics for the report header."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM customers")
        total_customers = cursor.fetchone()[0]

        try:
            cursor.execute(
                "SELECT COUNT(*) FROM customers "
                "WHERE date_service_terminated IS NOT NULL"
            )
            terminated = cursor.fetchone()[0]
        except Exception:
            terminated = 0

        active_customers = total_customers - terminated

        cursor.execute(
            "SELECT DISTINCT community FROM customers "
            "WHERE community IS NOT NULL AND community <> ''"
        )
        sites = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]

        total_kwh = 0.0
        total_lsl = 0.0
        txn_source = "transactions"

        try:
            cursor.execute("SELECT SUM(amount_lsl) FROM monthly_transactions")
            row = cursor.fetchone()
            if row and row[0] is not None:
                total_lsl = float(row[0])
                txn_source = "monthly_transactions"
        except Exception:
            pass

        try:
            cursor.execute("SELECT SUM(kwh) FROM monthly_consumption")
            row = cursor.fetchone()
            if row and row[0] is not None:
                total_kwh = float(row[0])
        except Exception:
            pass

        if total_kwh == 0 or (total_lsl == 0 and txn_source == "transactions"):
            try:
                cursor.execute(
                    "SELECT SUM(kwh_value), SUM(transaction_amount) "
                    "FROM transactions"
                )
                row = cursor.fetchone()
                if row:
                    if total_kwh == 0 and row[0] is not None:
                        total_kwh = float(row[0])
                    if total_lsl == 0 and row[1] is not None:
                        total_lsl = float(row[1])
            except Exception:
                pass

        return {
            "total_customers": total_customers,
            "active_customers": active_customers,
            "terminated_customers": terminated,
            "total_sites": len(sites),
            "sites": sites,
            "total_mwh": round(total_kwh / 1000, 2),
            "total_lsl_thousands": round(total_lsl / 1000, 2),
            "data_sources": {
                "revenue": txn_source,
            },
        }


# ---------------------------------------------------------------------------
# 2. Customer Statistics per Site (Figure 14)
# ---------------------------------------------------------------------------

@router.get("/customer-stats")
def customer_stats_by_site(
    quarter: Optional[str] = Query(None, description="Quarter in YYYY QN format, e.g. '2025 Q4'"),
    user: CurrentUser = Depends(require_employee),
):
    """Customer counts per community: total, active, and new in the specified quarter."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT community, customer_id_legacy, "
            "date_service_connected, date_service_terminated "
            "FROM customers WHERE community IS NOT NULL"
        )
        rows = cursor.fetchall()

        sites: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "active": 0, "new": 0})

        for row in rows:
            concession = str(row[0] or "").strip()
            if not concession:
                continue

            connected_date = row[2]
            terminated_date = row[3]

            sites[concession]["total"] += 1

            if terminated_date is None:
                sites[concession]["active"] += 1

            if quarter and connected_date:
                cq = _date_to_quarter(connected_date)
                if cq == quarter:
                    sites[concession]["new"] += 1

        result = []
        for name in sorted(sites.keys()):
            data = sites[name]
            result.append({
                "concession": name,
                "total": data["total"],
                "active": data["active"],
                "new": data["new"],
                "activation_rate": round(data["active"] / data["total"] * 100, 1) if data["total"] > 0 else 0,
            })

        totals = {
            "total": sum(s["total"] for s in result),
            "active": sum(s["active"] for s in result),
            "new": sum(s["new"] for s in result),
        }

        return {"sites": result, "totals": totals, "quarter": quarter}


# ---------------------------------------------------------------------------
# 3. Customer Growth Over Time (Figure 15)
# ---------------------------------------------------------------------------

@router.get("/customer-growth")
def customer_growth(user: CurrentUser = Depends(require_employee)):
    """Quarterly customer connection growth since first site commissioned."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT date_service_connected FROM customers "
            "WHERE date_service_connected IS NOT NULL"
        )
        rows = cursor.fetchall()

        quarterly: Dict[str, int] = defaultdict(int)
        for row in rows:
            q = _date_to_quarter(row[0])
            if q:
                quarterly[q] += 1

        sorted_quarters = sorted(quarterly.keys())
        cumulative = 0
        result = []
        for q in sorted_quarters:
            new_count = quarterly[q]
            cumulative += new_count
            result.append({
                "quarter": q,
                "new_customers": new_count,
                "cumulative": cumulative,
            })

        return {"growth": result, "total": cumulative}


# ---------------------------------------------------------------------------
# 4. Consumption by Site per Quarter (Figures 5, 12)
# ---------------------------------------------------------------------------

@router.get("/consumption-by-site")
def consumption_by_site(
    quarter: Optional[str] = Query(None, description="Filter to specific quarter"),
    user: CurrentUser = Depends(require_employee),
):
    """kWh consumption per site, optionally filtered by quarter."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT account_number, transaction_date, kwh_value "
                "FROM transactions"
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Failed to query transactions for consumption: %s", e)
            return {"sites": [], "total_kwh": 0, "error": "No account history data found"}

        if not rows:
            return {"sites": [], "total_kwh": 0, "error": "No account history data found"}

        site_quarter: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        site_totals: Dict[str, float] = defaultdict(float)

        for row in rows:
            acct = str(row[0] or "").strip()
            site = _extract_site(acct)
            if not site or len(site) < 2:
                continue

            kwh = float(row[2] or 0)
            q = _date_to_quarter(row[1]) if row[1] else "Unknown"

            if quarter and q != quarter:
                continue

            site_quarter[site][q] += kwh
            site_totals[site] += kwh

        per_site = []
        for site_code in sorted(site_totals.keys()):
            quarters_data = {q: round(v, 2) for q, v in sorted(site_quarter[site_code].items())}
            per_site.append({
                "site": site_code,
                "name": SITE_ABBREV.get(site_code, site_code),
                "total_kwh": round(site_totals[site_code], 2),
                "quarters": quarters_data,
            })

        return {
            "sites": per_site,
            "total_kwh": round(sum(site_totals.values()), 2),
            "source_table": "transactions",
            "quarter_filter": quarter,
        }


# ---------------------------------------------------------------------------
# 5. Sales by Site per Quarter (Figure 6)
# ---------------------------------------------------------------------------

@router.get("/sales-by-site")
def sales_by_site(
    quarter: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """LSL revenue per site, optionally filtered by quarter.

    Data source priority:
      1. monthly_transactions (SparkMeter, includes corrections)
      2. transactions (raw history, fallback)
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        rows: List[tuple] = []
        source_table = ""

        try:
            cursor.execute(
                "SELECT account_number, year_month, amount_lsl, community "
                "FROM monthly_transactions"
            )
            raw = cursor.fetchall()
            if raw:
                for r in raw:
                    acct = str(r[0] or "").strip()
                    ym = str(r[1] or "").strip()
                    lsl = float(r[2] or 0)
                    community = str(r[3] or "").strip()
                    if acct and ym and lsl > 0:
                        try:
                            y, m = int(ym[:4]), int(ym[5:7])
                            dt = datetime(y, m, 15)
                        except (ValueError, IndexError):
                            continue
                        rows.append((acct, dt, lsl, community))
                if rows:
                    source_table = "monthly_transactions"
        except Exception as e:
            logger.warning("monthly_transactions query failed: %s", e)

        if not rows:
            try:
                cursor.execute(
                    "SELECT account_number, transaction_date, transaction_amount "
                    "FROM transactions"
                )
                raw = cursor.fetchall()
                if raw:
                    for r in raw:
                        acct = str(r[0] or "").strip()
                        lsl = float(r[2] or 0)
                        rows.append((acct, r[1], lsl, ""))
                    if rows:
                        source_table = "transactions"
            except Exception as e:
                logger.warning("Failed to query transactions for sales: %s", e)

        if not rows:
            return {"sites": [], "total_lsl": 0, "error": "No transaction data found"}

        site_quarter: Dict[str, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        site_totals: Dict[str, float] = defaultdict(float)

        for acct, dt_or_ym, lsl, community in rows:
            site = community if community else _extract_site(acct)
            if not site or len(site) < 2:
                continue
            q = _date_to_quarter(dt_or_ym) if dt_or_ym else "Unknown"
            if quarter and q != quarter:
                continue
            site_quarter[site][q] += lsl
            site_totals[site] += lsl

        per_site = []
        for site_code in sorted(site_totals.keys()):
            quarters_data = {
                q: round(v, 2)
                for q, v in sorted(site_quarter[site_code].items())
            }
            per_site.append({
                "site": site_code,
                "name": SITE_ABBREV.get(site_code, site_code),
                "total_lsl": round(site_totals[site_code], 2),
                "quarters": quarters_data,
            })

        return {
            "sites": per_site,
            "total_lsl": round(sum(site_totals.values()), 2),
            "source_table": source_table,
            "quarter_filter": quarter,
        }


# ---------------------------------------------------------------------------
# 6. Cumulative Consumption & Sales Trends (Figures 3, 4)
# ---------------------------------------------------------------------------

@router.get("/cumulative-trends")
def cumulative_trends(user: CurrentUser = Depends(require_employee)):
    """Quarterly cumulative consumption (kWh) and sales (LSL) over time."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT transaction_date, kwh_value, transaction_amount "
                "FROM transactions"
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Failed to query transactions for cumulative: %s", e)
            return {"trends": [], "error": "No date column found in account history"}

        if not rows:
            return {"trends": [], "error": "No date column found in account history"}

        quarterly_kwh: Dict[str, float] = defaultdict(float)
        quarterly_lsl: Dict[str, float] = defaultdict(float)

        for row in rows:
            q = _date_to_quarter(row[0])
            if not q:
                continue
            quarterly_kwh[q] += float(row[1] or 0)
            quarterly_lsl[q] += float(row[2] or 0)

        sorted_quarters = sorted(set(quarterly_kwh.keys()) | set(quarterly_lsl.keys()))
        cum_kwh = 0.0
        cum_lsl = 0.0
        result = []
        for q in sorted_quarters:
            kwh = quarterly_kwh.get(q, 0)
            lsl = quarterly_lsl.get(q, 0)
            cum_kwh += kwh
            cum_lsl += lsl
            result.append({
                "quarter": q,
                "kwh": round(kwh, 2),
                "lsl": round(lsl, 2),
                "cumulative_kwh": round(cum_kwh, 2),
                "cumulative_lsl": round(cum_lsl, 2),
            })

        return {"trends": result, "source_table": "transactions"}


# ---------------------------------------------------------------------------
# 7. Average Consumption per Customer Trend (Figures 8, 9)
# ---------------------------------------------------------------------------

@router.get("/avg-consumption-trend")
def avg_consumption_trend(user: CurrentUser = Depends(require_employee)):
    """Average daily consumption and sales per customer per quarter."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT date_service_connected FROM customers "
            "WHERE date_service_connected IS NOT NULL"
        )
        cust_rows = cursor.fetchall()

        quarterly_new: Dict[str, int] = defaultdict(int)
        for row in cust_rows:
            q = _date_to_quarter(row[0])
            if q:
                quarterly_new[q] += 1

        all_quarters = sorted(quarterly_new.keys())
        cum_customers: Dict[str, int] = {}
        cum = 0
        for q in all_quarters:
            cum += quarterly_new[q]
            cum_customers[q] = cum

        try:
            cursor.execute(
                "SELECT transaction_date, kwh_value, transaction_amount "
                "FROM transactions"
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Failed to query transactions for avg trend: %s", e)
            return {"trends": [], "error": "No data found"}

        if not rows:
            return {"trends": [], "error": "No data found"}

        quarterly_kwh: Dict[str, float] = defaultdict(float)
        quarterly_lsl: Dict[str, float] = defaultdict(float)
        quarterly_days: Dict[str, set] = defaultdict(set)

        for row in rows:
            q = _date_to_quarter(row[0])
            if not q:
                continue
            quarterly_kwh[q] += float(row[1] or 0)
            quarterly_lsl[q] += float(row[2] or 0)
            dk = _day_key(row[0])
            if dk:
                quarterly_days[q].add(dk)

        sorted_q = sorted(set(quarterly_kwh.keys()) & set(cum_customers.keys()))
        result = []
        for q in sorted_q:
            customers = cum_customers.get(q, 1)
            days = len(quarterly_days.get(q, set())) or 90
            kwh = quarterly_kwh.get(q, 0)
            lsl = quarterly_lsl.get(q, 0)

            result.append({
                "quarter": q,
                "customers": customers,
                "total_kwh": round(kwh, 2),
                "total_lsl": round(lsl, 2),
                "avg_daily_kwh_per_customer": round(kwh / (customers * days), 4) if customers > 0 else 0,
                "avg_daily_lsl_per_customer": round(lsl / (customers * days), 4) if customers > 0 else 0,
            })

        return {"trends": result, "source_table": "transactions"}


# ---------------------------------------------------------------------------
# 8. Site Overview with Districts (Tables 1, 2, 3)
# ---------------------------------------------------------------------------

@router.get("/site-overview")
def site_overview(user: CurrentUser = Depends(require_employee)):
    """List of all communities with customer counts and district info."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT community, COUNT(*) AS cnt "
            "FROM customers "
            "WHERE community IS NOT NULL AND community <> '' "
            "GROUP BY community "
            "ORDER BY community"
        )
        rows = cursor.fetchall()

        sites = []
        for row in rows:
            name = str(row[0]).strip()
            count = row[1]
            abbrev = ""
            for code, full_name in SITE_ABBREV.items():
                if full_name.lower() == name.lower() or code.lower() in name.lower():
                    abbrev = code
                    break
            sites.append({
                "concession": name,
                "abbreviation": abbrev,
                "district": SITE_DISTRICTS.get(abbrev, ""),
                "customer_count": count,
            })

        return {"sites": sites}


# ---------------------------------------------------------------------------
# 9. Load Curves by Customer Type
# ---------------------------------------------------------------------------

@router.get("/load-curves-by-type")
def load_curves_by_type(
    quarter: Optional[str] = Query(None, description="Filter to quarter, e.g. '2025 Q4'"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Average daily consumption per customer type.
    Joins meters table (customer type + account number) with transactions.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # 1. Build account -> customer_type mapping from meters
        acct_type: Dict[str, str] = {}
        cursor.execute(
            "SELECT account_number, customer_type FROM meters "
            "WHERE customer_type IS NOT NULL AND customer_type <> ''"
        )
        for row in cursor.fetchall():
            acct = str(row[0] or "").strip()
            ctype = str(row[1] or "").strip()
            if acct and ctype:
                acct_type[acct] = ctype

        if not acct_type:
            return {
                "curves": [],
                "quarterly": [],
                "note": "No customer type data found in meters table.",
            }

        # 2. Query transactions
        try:
            cursor.execute(
                "SELECT account_number, transaction_date, kwh_value, "
                "transaction_amount FROM transactions"
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Failed to query transactions for load curves: %s", e)
            return {"curves": [], "quarterly": [], "error": "No account history data found"}

        if not rows:
            return {"curves": [], "quarterly": [], "error": "No account history data found"}

        type_totals: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"kwh": 0.0, "lsl": 0.0, "customers": set(), "days": set()}
        )
        type_quarter: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {"kwh": 0.0, "lsl": 0.0})
        )

        for row in rows:
            acct = str(row[0] or "").strip()
            ctype = acct_type.get(acct)
            if not ctype:
                continue

            kwh = float(row[2] or 0)
            lsl = float(row[3] or 0)
            q = _date_to_quarter(row[1]) if row[1] else "Unknown"

            if quarter and q != quarter:
                continue

            type_totals[ctype]["kwh"] += kwh
            type_totals[ctype]["lsl"] += lsl
            type_totals[ctype]["customers"].add(acct)
            dk = _day_key(row[1])
            if dk:
                type_totals[ctype]["days"].add(dk)

            type_quarter[ctype][q]["kwh"] += kwh
            type_quarter[ctype][q]["lsl"] += lsl

        curves = []
        for ctype in sorted(type_totals.keys()):
            data = type_totals[ctype]
            n_customers = len(data["customers"])
            n_days = len(data["days"]) or 90
            curves.append({
                "type": ctype,
                "total_kwh": round(data["kwh"], 2),
                "total_lsl": round(data["lsl"], 2),
                "customer_count": n_customers,
                "avg_daily_kwh": round(data["kwh"] / n_days, 4) if n_days > 0 else 0,
                "avg_daily_kwh_per_customer": round(
                    data["kwh"] / (n_customers * n_days), 4
                ) if n_customers > 0 and n_days > 0 else 0,
            })

        all_quarters = sorted(
            set(q for tq in type_quarter.values() for q in tq.keys())
        )
        quarterly = []
        for q in all_quarters:
            entry: Dict[str, Any] = {"quarter": q}
            for ctype in sorted(type_totals.keys()):
                entry[ctype] = round(type_quarter[ctype].get(q, {}).get("kwh", 0), 2)
            quarterly.append(entry)

        return {
            "curves": curves,
            "quarterly": quarterly,
            "customer_types": sorted(type_totals.keys()),
            "total_typed_customers": len(acct_type),
            "source_table": "transactions",
            "meter_source": "meters",
            "quarter_filter": quarter,
        }


# ---------------------------------------------------------------------------
# 10. 24-Hour Daily Load Profiles by Customer Type
# ---------------------------------------------------------------------------
#
# Uses meter_readings (10-minute interval readings: reading_time, power_kw,
# meter_id) joined with meters (meter_id -> customer type) to build average
# hourly power curves for each customer type.

@router.get("/daily-load-profiles")
def daily_load_profiles(
    site: Optional[str] = Query(None, description="Filter to site code (e.g. MAK)"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Average 24-hour load profiles by customer type.
    Returns average kW for each hour (0-23) per type, derived from
    10-minute meter readings in meter_readings.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # 1. Build meter_id -> customer_type mapping from meters
        meter_type: Dict[str, str] = {}
        if site:
            cursor.execute(
                "SELECT meter_id, customer_type FROM meters "
                "WHERE customer_type IS NOT NULL AND customer_type <> '' "
                "AND community = %s",
                (site.upper(),),
            )
        else:
            cursor.execute(
                "SELECT meter_id, customer_type FROM meters "
                "WHERE customer_type IS NOT NULL AND customer_type <> ''"
            )
        for row in cursor.fetchall():
            mid = str(row[0] or "").strip()
            ctype = str(row[1] or "").strip()
            if mid and ctype:
                meter_type[mid] = ctype

        if not meter_type:
            return {
                "profiles": [],
                "note": "No customer type data found in meters table.",
            }

        # 2. Query meter_readings for timestamped readings
        try:
            if site:
                cursor.execute(
                    "SELECT meter_id, reading_time, power_kw FROM meter_readings "
                    "WHERE community = %s AND power_kw IS NOT NULL",
                    (site.upper(),),
                )
            else:
                cursor.execute(
                    "SELECT meter_id, reading_time, power_kw FROM meter_readings "
                    "WHERE power_kw IS NOT NULL"
                )

            type_hour_kw: Dict[str, Dict[int, List[float]]] = defaultdict(
                lambda: defaultdict(list)
            )
            type_meter_count: Dict[str, set] = defaultdict(set)
            total_readings = 0

            for row in cursor.fetchall():
                mid = str(row[0] or "").strip()
                ctype = meter_type.get(mid)
                if not ctype:
                    continue

                dt = row[1]
                kw = row[2]
                if dt is None or kw is None:
                    continue

                try:
                    kw_val = float(kw)
                except (ValueError, TypeError):
                    continue

                try:
                    from country_config import UTC_OFFSET_HOURS
                    if hasattr(dt, 'hour'):
                        local_dt = dt.replace(tzinfo=None) + timedelta(hours=UTC_OFFSET_HOURS) if hasattr(dt, 'tzinfo') and dt.tzinfo else dt + timedelta(hours=UTC_OFFSET_HOURS)
                        hour = local_dt.hour
                    elif isinstance(dt, str):
                        hour = (int(dt.split(" ")[1].split(":")[0]) + UTC_OFFSET_HOURS) % 24
                    else:
                        continue
                except (IndexError, ValueError, AttributeError):
                    continue

                type_hour_kw[ctype][hour].append(kw_val)
                type_meter_count[ctype].add(mid)
                total_readings += 1

            if not type_hour_kw:
                return {
                    "profiles": [],
                    "note": "No timestamped meter readings found.",
                }

            # 3. Build 24-hour profiles
            profiles = []
            for ctype in sorted(type_hour_kw.keys()):
                hourly = []
                for h in range(24):
                    readings = type_hour_kw[ctype].get(h, [])
                    avg_kw = sum(readings) / len(readings) if readings else 0
                    hourly.append({
                        "hour": h,
                        "avg_kw": round(avg_kw, 4),
                        "readings": len(readings),
                    })

                profiles.append({
                    "type": ctype,
                    "meter_count": len(type_meter_count[ctype]),
                    "hourly": hourly,
                    "peak_hour": max(range(24), key=lambda h: sum(type_hour_kw[ctype].get(h, [0])) / max(len(type_hour_kw[ctype].get(h, [1])), 1)),
                    "peak_kw": round(max(
                        sum(type_hour_kw[ctype].get(h, [0])) / max(len(type_hour_kw[ctype].get(h, [1])), 1)
                        for h in range(24)
                    ), 4),
                })

            chart_data = []
            for h in range(24):
                point: Dict[str, Any] = {"hour": f"{h:02d}:00"}
                for ctype in sorted(type_hour_kw.keys()):
                    readings = type_hour_kw[ctype].get(h, [])
                    point[ctype] = round(sum(readings) / len(readings), 4) if readings else 0
                chart_data.append(point)

            return {
                "profiles": profiles,
                "chart_data": chart_data,
                "customer_types": sorted(type_hour_kw.keys()),
                "total_readings": total_readings,
                "meter_source": "meters",
                "site_filter": site,
            }

        except Exception as e:
            logger.warning("Failed to query meter_readings for load profiles: %s", e)
            return {"profiles": [], "error": str(e)}


# ---------------------------------------------------------------------------
# 11. ARPU (Average Revenue Per User) Time Series
# ---------------------------------------------------------------------------

@router.get("/arpu")
def arpu_time_series(user: CurrentUser = Depends(require_employee)):
    """
    Quarterly ARPU: total revenue / cumulative customer base per quarter.

    "Active customers" = all distinct account numbers that have ever
    transacted up to and including the quarter.  This produces a
    monotonically-increasing customer count that reflects the growing
    customer base, and divides quarterly revenue by that base.

    Data source priority:
      1. monthly_transactions (SparkMeter portfolio data, includes manual corrections)
      2. transactions (raw history, fallback)
    """

    with _get_connection() as conn:
        cursor = conn.cursor()

        txn_rows: List[tuple] = []
        source_table = ""

        try:
            cursor.execute(
                "SELECT account_number, year_month, amount_lsl, community "
                "FROM monthly_transactions"
            )
            raw = cursor.fetchall()
            if raw:
                for row in raw:
                    acct = str(row[0] or "").strip()
                    ym = str(row[1] or "").strip()
                    lsl = float(row[2] or 0)
                    community = str(row[3] or "").strip()
                    if not acct or not ym or lsl <= 0:
                        continue
                    try:
                        y, m = int(ym[:4]), int(ym[5:7])
                        dt = datetime(y, m, 15)
                    except (ValueError, IndexError):
                        continue
                    txn_rows.append((acct, dt, lsl, community))
                if txn_rows:
                    source_table = "monthly_transactions"
        except Exception as e:
            logger.warning("Failed to query monthly_transactions for ARPU: %s", e)

        if not txn_rows:
            try:
                cursor.execute(
                    "SELECT account_number, transaction_date, transaction_amount "
                    "FROM transactions"
                )
                raw = cursor.fetchall()
                if raw:
                    txn_rows = [
                        (str(r[0] or "").strip(), r[1], float(r[2] or 0), "")
                        for r in raw
                    ]
                    source_table = "transactions"
            except Exception:
                pass

        if txn_rows:
            q_revenue: Dict[str, float] = defaultdict(float)
            q_site_revenue: Dict[str, Dict[str, float]] = defaultdict(
                lambda: defaultdict(float)
            )
            acct_first_quarter: Dict[str, str] = {}
            acct_site: Dict[str, str] = {}

            for row in txn_rows:
                acct = str(row[0] or "").strip()
                q = _date_to_quarter(row[1])
                lsl = float(row[2] or 0)
                community = row[3] if len(row) > 3 else ""
                if not q or not acct:
                    continue
                site = community if community else _extract_site(acct)

                q_revenue[q] += lsl
                if site and len(site) >= 2:
                    q_site_revenue[q][site] += lsl

                if acct not in acct_first_quarter or q < acct_first_quarter[acct]:
                    acct_first_quarter[acct] = q
                    if site and len(site) >= 2:
                        acct_site[acct] = site

            all_quarters = sorted(q_revenue.keys())
            cumulative_all: set = set()
            cumulative_by_site: Dict[str, set] = defaultdict(set)

            result = []
            for q in all_quarters:
                for acct, first_q in acct_first_quarter.items():
                    if first_q <= q:
                        cumulative_all.add(acct)
                        site = acct_site.get(acct, "")
                        if site:
                            cumulative_by_site[site].add(acct)

                revenue = q_revenue[q]
                active = len(cumulative_all)
                arpu = round(revenue / active, 2) if active > 0 else 0

                per_site = {}
                for site_code in sorted(q_site_revenue[q]):
                    site_rev = q_site_revenue[q][site_code]
                    site_custs = len(cumulative_by_site.get(site_code, set()))
                    per_site[site_code] = {
                        "name": SITE_ABBREV.get(site_code, site_code),
                        "revenue": round(site_rev, 2),
                        "customers": site_custs,
                        "arpu": round(site_rev / site_custs, 2) if site_custs > 0 else 0,
                    }

                result.append({
                    "quarter": q,
                    "total_revenue": round(revenue, 2),
                    "active_customers": active,
                    "arpu": arpu,
                    "per_site": per_site,
                })

            all_site_codes = sorted(
                set(code for entry in result for code in entry["per_site"])
            )

            return {
                "arpu": result,
                "site_codes": all_site_codes,
                "site_names": {c: SITE_ABBREV.get(c, c) for c in all_site_codes},
                "source_table": source_table,
            }

        return {"arpu": [], "site_codes": [], "error": "No account history data found"}


# ---------------------------------------------------------------------------
# 12. Monthly ARPU Time Series
# ---------------------------------------------------------------------------

def _date_to_month(dt) -> str:
    """Convert a date/datetime to 'YYYY-MM' string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(dt.strip(), fmt)
                break
            except (ValueError, AttributeError):
                continue
        else:
            return ""
    try:
        return f"{dt.year}-{dt.month:02d}"
    except (AttributeError, TypeError):
        return ""


@router.get("/monthly-arpu")
def monthly_arpu_time_series(user: CurrentUser = Depends(require_employee)):
    """
    Monthly ARPU: total revenue / cumulative customer base per month.

    Data source priority:
      1. monthly_transactions (SparkMeter portfolio, includes corrections)
      2. transactions (raw history, fallback)
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        txn_rows: List[tuple] = []
        source_table = ""

        try:
            cursor.execute(
                "SELECT account_number, year_month, amount_lsl, community "
                "FROM monthly_transactions"
            )
            raw = cursor.fetchall()
            if raw:
                for row in raw:
                    acct = str(row[0] or "").strip()
                    ym = str(row[1] or "").strip()
                    lsl = float(row[2] or 0)
                    community = str(row[3] or "").strip()
                    if acct and ym and lsl > 0:
                        txn_rows.append((acct, ym, lsl, community))
                if txn_rows:
                    source_table = "monthly_transactions"
        except Exception as e:
            logger.warning("monthly_transactions query failed: %s", e)

        if not txn_rows:
            try:
                cursor.execute(
                    "SELECT account_number, transaction_date, transaction_amount "
                    "FROM transactions"
                )
                raw = cursor.fetchall()
                if raw:
                    for r in raw:
                        acct = str(r[0] or "").strip()
                        m = _date_to_month(r[1])
                        lsl = float(r[2] or 0)
                        if acct and m:
                            txn_rows.append((acct, m, lsl, ""))
                    if txn_rows:
                        source_table = "transactions"
            except Exception:
                pass

        if not txn_rows:
            return {"monthly_arpu": [], "site_codes": [], "error": "No transaction data found"}

        m_revenue: Dict[str, float] = defaultdict(float)
        m_site_revenue: Dict[str, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        acct_first_month: Dict[str, str] = {}
        acct_site: Dict[str, str] = {}

        for acct, m, lsl, community in txn_rows:
            site = community if community else _extract_site(acct)
            m_revenue[m] += lsl
            if site and len(site) >= 2:
                m_site_revenue[m][site] += lsl
            if acct not in acct_first_month or m < acct_first_month[acct]:
                acct_first_month[acct] = m
                if site and len(site) >= 2:
                    acct_site[acct] = site

        all_months = sorted(m_revenue.keys())
        cumulative_all: set = set()
        cumulative_by_site: Dict[str, set] = defaultdict(set)

        result = []
        for m in all_months:
            for acct, first_m in acct_first_month.items():
                if first_m <= m:
                    cumulative_all.add(acct)
                    site = acct_site.get(acct, "")
                    if site:
                        cumulative_by_site[site].add(acct)

            revenue = m_revenue[m]
            active = len(cumulative_all)
            arpu = round(revenue / active, 2) if active > 0 else 0

            per_site = {}
            for site_code in sorted(m_site_revenue[m]):
                site_rev = m_site_revenue[m][site_code]
                site_custs = len(cumulative_by_site.get(site_code, set()))
                per_site[site_code] = {
                    "name": SITE_ABBREV.get(site_code, site_code),
                    "revenue": round(site_rev, 2),
                    "customers": site_custs,
                    "arpu": round(site_rev / site_custs, 2) if site_custs > 0 else 0,
                }

            result.append({
                "month": m,
                "quarter": _date_to_quarter_from_month(m),
                "total_revenue": round(revenue, 2),
                "active_customers": active,
                "arpu": arpu,
                "per_site": per_site,
            })

        all_site_codes = sorted(
            set(code for entry in result for code in entry["per_site"])
        )

        return {
            "monthly_arpu": result,
            "site_codes": all_site_codes,
            "site_names": {c: SITE_ABBREV.get(c, c) for c in all_site_codes},
            "source_table": source_table,
        }


def _date_to_quarter_from_month(month_str: str) -> str:
    """Convert 'YYYY-MM' to 'YYYY QN'."""
    try:
        y, m = month_str.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"{y} Q{q}"
    except (ValueError, AttributeError):
        return ""


# ---------------------------------------------------------------------------
# 13. Average Consumption by Tenure (months since first transaction)
# ---------------------------------------------------------------------------

@router.get("/consumption-by-tenure")
def consumption_by_tenure(
    user: CurrentUser = Depends(require_employee),
):
    """
    Average monthly kWh consumption as a function of tenure (months since
    first reading/transaction), segmented by customer type (HH, SME, etc.)
    with +/- 1 standard deviation bands.

    Data source priority:
      1. monthly_consumption -- actual meter readings imported from Koios /
         ThunderCloud via import_meter_readings.py.
      2. transactions -- kWh vended per transaction (fallback if meter
         readings not yet imported).

    Customer type is resolved from the meters table first, then from the
    static JSON mapping (meter_customer_types.json) as a fallback.
    """

    # -- Load meter -> customer-type mapping (JSON fallback) --
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    _type_map_path = os.path.join(_data_dir, "meter_customer_types.json")
    try:
        with open(_type_map_path, "r") as f:
            json_type_map: Dict[str, str] = json.load(f)
    except Exception as e:
        logger.error("Cannot load meter_customer_types.json: %s", e)
        json_type_map = {}

    norm_map: Dict[str, str] = {}
    for mid, ctype in json_type_map.items():
        norm_map[mid.upper().replace("_", "-")] = ctype

    def _parse_dt(dt) -> Optional[datetime]:
        if dt is None:
            return None
        if isinstance(dt, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(dt.strip(), fmt)
                except (ValueError, AttributeError):
                    continue
            return None
        try:
            _ = dt.year
            return dt
        except (AttributeError, TypeError):
            return None

    def _lookup_type(meter_id: str) -> Optional[str]:
        if not meter_id:
            return None
        key = meter_id.strip().upper().replace("_", "-")
        return norm_map.get(key)

    with _get_connection() as conn:
        cursor = conn.cursor()

        # -- Fetch all meter data in one query --
        cursor.execute(
            "SELECT meter_id, account_number, customer_type FROM meters"
        )
        all_meter_rows = cursor.fetchall()

        # -- Build mappings from meters table --
        acct_type: Dict[str, str] = {}
        meter_type_map: Dict[str, str] = {}
        meter_to_acct: Dict[str, str] = {}

        for row in all_meter_rows:
            mid = str(row[0] or "").strip()
            acct = str(row[1] or "").strip()
            ctype = str(row[2] or "").strip()

            if mid and acct:
                meter_to_acct[mid] = acct
                meter_to_acct[mid.upper()] = acct

            if mid and ctype:
                meter_type_map[mid] = ctype
                meter_type_map[mid.upper()] = ctype

            if acct and ctype and acct not in acct_type:
                acct_type[acct] = ctype

        # Enrich acct_type for accounts without a direct type, via JSON
        for row in all_meter_rows:
            mid = str(row[0] or "").strip()
            acct = str(row[1] or "").strip()
            if not acct or acct in acct_type:
                continue
            ctype_json = _lookup_type(mid)
            if ctype_json:
                acct_type[acct] = ctype_json

        acct_type_lower: Dict[str, str] = {k.lower(): v for k, v in acct_type.items()}

        def _resolve_type(acct: str, meterid: str = "") -> Optional[str]:
            """Multi-strategy customer type resolution."""
            ct = acct_type.get(acct) or acct_type_lower.get(acct.lower())
            if ct:
                return ct
            if meterid:
                ct = meter_type_map.get(meterid) or meter_type_map.get(meterid.upper())
                if ct:
                    return ct
                ct = _lookup_type(meterid)
                if ct:
                    return ct
            ct = meter_type_map.get(acct) or meter_type_map.get(acct.upper())
            if ct:
                return ct
            return _lookup_type(acct)

        # -- Merge ALL data sources for comprehensive tenure analysis --
        parsed_rows: List[tuple] = []
        acct_first_txn: Dict[str, datetime] = {}
        debug_info: Dict[str, Any] = {"acct_type_map_size": len(acct_type)}

        # -- Source 1: monthly_consumption (actual meter readings) --
        consumption_acct_months: Set[Tuple[str, str]] = set()
        consumption_rows: list = []

        try:
            cursor.execute(
                "SELECT account_number, year_month, kwh, meter_id "
                "FROM monthly_consumption"
            )
            consumption_rows = cursor.fetchall()
        except Exception as e:
            logger.warning("monthly_consumption query failed: %s", e)

        cons_matched = 0
        cons_unmatched = 0
        cons_added = 0

        for row in consumption_rows:
            raw_acct = str(row[0] or "").strip()
            ym = str(row[1] or "").strip()
            try:
                kwh = float(row[2] or 0)
            except (ValueError, TypeError):
                continue
            meterid = str(row[3] or "").strip() if len(row) > 3 else ""
            if not raw_acct or not ym or kwh <= 0:
                continue

            acct = (meter_to_acct.get(raw_acct)
                    or meter_to_acct.get(raw_acct.upper())
                    or raw_acct)
            ctype = _resolve_type(acct, meterid or raw_acct)
            if not ctype:
                cons_unmatched += 1
                continue
            cons_matched += 1

            try:
                y, m = int(ym[:4]), int(ym[5:7])
                dt = datetime(y, m, 1)
            except (ValueError, IndexError):
                continue

            ym_key = f"{dt.year:04d}-{dt.month:02d}"
            consumption_acct_months.add((acct, ym_key))
            parsed_rows.append((acct, ctype, dt, kwh))
            cons_added += 1
            if acct not in acct_first_txn or dt < acct_first_txn[acct]:
                acct_first_txn[acct] = dt

        debug_info["consumption"] = {
            "total_rows": len(consumption_rows),
            "matched": cons_matched,
            "unmatched": cons_unmatched,
            "added": cons_added,
        }

        # -- Source 2: transactions (comprehensive vended kWh) --
        history_rows: list = []
        try:
            cursor.execute(
                "SELECT meter_id, account_number, transaction_date, kwh_value "
                "FROM transactions"
            )
            history_rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Failed to read transactions for tenure: %s", e)

        # Extend type mapping from history rows
        for row in history_rows:
            mid = str(row[0] or "").strip()
            acct = str(row[1] or "").strip()
            if not acct or acct in acct_type:
                continue
            ctype = _lookup_type(mid)
            if ctype:
                acct_type[acct] = ctype
        acct_type_lower.update({k.lower(): v for k, v in acct_type.items()})

        hist_added = 0
        hist_skipped_overlap = 0
        hist_matched = 0
        hist_unmatched = 0

        for row in history_rows:
            mid = str(row[0] or "").strip()
            acct = str(row[1] or "").strip()
            if not acct:
                continue

            ctype = _resolve_type(acct, mid)
            if not ctype:
                hist_unmatched += 1
                continue
            hist_matched += 1

            txn_dt = _parse_dt(row[2])
            if txn_dt is None:
                continue
            try:
                kwh = float(row[3] or 0)
            except (ValueError, TypeError):
                continue

            ym_key = f"{txn_dt.year:04d}-{txn_dt.month:02d}"
            if (acct, ym_key) in consumption_acct_months:
                hist_skipped_overlap += 1
                continue

            parsed_rows.append((acct, ctype, txn_dt, kwh))
            hist_added += 1
            if acct not in acct_first_txn or txn_dt < acct_first_txn[acct]:
                acct_first_txn[acct] = txn_dt

        debug_info["transactions"] = {
            "rows": len(history_rows),
            "matched": hist_matched,
            "unmatched": hist_unmatched,
            "added": hist_added,
            "skipped_overlap": hist_skipped_overlap,
        }
        debug_info["total_unique_accounts"] = len(acct_first_txn)

        data_source = (
            "merged (consumption + vended)"
            if cons_added > 0
            else "vended"
        )

        if not acct_first_txn:
            return {
                "chart_data": [], "customer_types": [],
                "data_source": data_source,
                "error": "No account data found",
                "debug": debug_info,
            }

        # -- Aggregate by type -> tenure_month -> acct --
        type_tenure_acct: Dict[str, Dict[int, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )

        for acct, ctype, txn_dt, kwh in parsed_rows:
            if kwh <= 0:
                continue
            first_dt = acct_first_txn[acct]
            tenure_months = (
                (txn_dt.year - first_dt.year) * 12
                + (txn_dt.month - first_dt.month)
            )
            if tenure_months < 0:
                continue
            type_tenure_acct[ctype][tenure_months][acct] += kwh

        all_types = sorted(type_tenure_acct.keys())
        if not all_types:
            return {
                "chart_data": [], "customer_types": [],
                "data_source": data_source,
                "error": "No typed tenure data after aggregation",
                "debug": debug_info,
            }

        now = datetime.now()
        type_acct_tenure: Dict[str, Dict[str, int]] = defaultdict(dict)
        max_tenure = 0
        for acct, ctype, _dt, _kwh in parsed_rows:
            if acct in type_acct_tenure.get(ctype, {}):
                continue
            first_dt = acct_first_txn[acct]
            tenure = (now.year - first_dt.year) * 12 + (now.month - first_dt.month)
            if tenure < 0:
                continue
            type_acct_tenure[ctype][acct] = tenure
            if tenure > max_tenure:
                max_tenure = tenure

        chart_data = []
        last_valid_t = 0
        for t in range(max_tenure + 1):
            point: Dict[str, Any] = {"tenure_month": t}
            has_any_data = False
            for ctype in all_types:
                n_eligible = sum(
                    1 for ten in type_acct_tenure.get(ctype, {}).values()
                    if ten >= t
                )
                acct_kwh = type_tenure_acct[ctype].get(t, {})
                raw_values = sorted(acct_kwh.values())
                if len(raw_values) >= 4:
                    q1_idx = len(raw_values) // 4
                    q3_idx = 3 * len(raw_values) // 4
                    q1 = raw_values[q1_idx]
                    q3 = raw_values[q3_idx]
                    iqr = q3 - q1
                    lo = q1 - 1.5 * iqr
                    hi = q3 + 1.5 * iqr
                    values = [v for v in raw_values if lo <= v <= hi]
                else:
                    values = raw_values
                if len(values) < 3:
                    point[ctype] = None
                    point[f"{ctype}_upper"] = None
                    point[f"{ctype}_lower"] = None
                    point[f"{ctype}_n"] = n_eligible
                    point[f"{ctype}_nd"] = len(raw_values)
                else:
                    has_any_data = True
                    n_data = len(values)
                    mean = sum(values) / n_data
                    if n_data > 1:
                        variance = sum((v - mean) ** 2 for v in values) / n_data
                        sd = math.sqrt(variance)
                    else:
                        sd = 0.0
                    point[ctype] = round(mean, 2)
                    point[f"{ctype}_upper"] = round(mean + sd, 2)
                    point[f"{ctype}_lower"] = round(max(mean - sd, 0), 2)
                    point[f"{ctype}_n"] = n_eligible
                    point[f"{ctype}_nd"] = len(raw_values)
                    point[f"{ctype}_nf"] = len(raw_values) - n_data
                    point[f"{ctype}_min"] = round(min(values), 2)
                    point[f"{ctype}_max"] = round(max(values), 2)
            chart_data.append(point)
            if has_any_data:
                last_valid_t = t

        chart_data = chart_data[: last_valid_t + 1]
        max_tenure = last_valid_t

        type_stats = []
        for ctype in all_types:
            all_accts: set = set()
            total_kwh = 0.0
            for t_data in type_tenure_acct[ctype].values():
                all_accts.update(t_data.keys())
                total_kwh += sum(t_data.values())
            max_t = (
                max(type_tenure_acct[ctype].keys())
                if type_tenure_acct[ctype]
                else 0
            )
            type_stats.append({
                "type": ctype,
                "customer_count": len(all_accts),
                "total_kwh": round(total_kwh, 2),
                "max_tenure_months": max_t,
            })

        debug_info["type_acct_tenure_counts"] = {
            ct: len(accts) for ct, accts in type_acct_tenure.items()
        }

        return {
            "chart_data": chart_data,
            "customer_types": all_types,
            "type_stats": type_stats,
            "max_tenure_months": max_tenure,
            "total_accounts_matched": len(acct_first_txn),
            "data_source": data_source,
            "segmentation": "customer_type",
            "mapping_size": len(json_type_map),
            "debug": debug_info,
        }


# ---------------------------------------------------------------------------
# 14. Meter Data Export (for CDF building)
# ---------------------------------------------------------------------------

@router.get("/meter-export")
def meter_data_export(
    customer_type: Optional[str] = Query(None, description="Filter by customer type (e.g. HH, SME, SCH)"),
    site: Optional[str] = Query(None, description="Filter by site code (e.g. MAK)"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Export raw meter readings from meter_readings for CDF generation.

    Returns timestamped kW readings joined with the meters table to
    include customer type and site.  Designed for batch consumption by
    the uGridPlan 8760 CDF builder script.

    Response: {readings: [{timestamp, kw, customer_type, site, meterid}, ...], meta: {...}}
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # -- 1. Build meter_id -> (customer_type, community) mapping --
        meter_info: Dict[str, Dict[str, str]] = {}
        cursor.execute(
            "SELECT meter_id, customer_type, community FROM meters "
            "WHERE customer_type IS NOT NULL AND customer_type <> ''"
        )
        for row in cursor.fetchall():
            mid = str(row[0] or "").strip()
            ctype = str(row[1] or "").strip().upper()
            community = str(row[2] or "").strip().upper()
            if mid and ctype:
                meter_info[mid] = {"type": ctype, "site": community}

        if not meter_info:
            return {"readings": [], "meta": {"error": "No meter registry data found"}}

        # -- 2. Query meter_readings --
        sql = (
            "SELECT meter_id, reading_time, power_kw FROM meter_readings "
            "WHERE power_kw IS NOT NULL"
        )
        params: List[Any] = []

        if site:
            sql += " AND community = %s"
            params.append(site.upper())

        try:
            cursor.execute(sql, params) if params else cursor.execute(sql)
        except Exception as e:
            logger.warning("meter-export: query failed: %s", e)
            return {"readings": [], "meta": {"error": str(e)}}

        # -- 3. Stream results with Python-side filtering --
        readings: List[Dict[str, Any]] = []
        skipped = 0

        for row in cursor.fetchall():
            mid = str(row[0] or "").strip()
            info = meter_info.get(mid)
            if not info:
                skipped += 1
                continue

            ctype = info["type"]
            community = info["site"]

            if customer_type and ctype != customer_type.upper():
                continue

            dt_val = row[1]
            kw_val = row[2]
            if dt_val is None or kw_val is None:
                continue

            try:
                kw_float = float(kw_val)
            except (ValueError, TypeError):
                continue

            try:
                if hasattr(dt_val, 'year'):
                    ts = dt_val
                elif isinstance(dt_val, str):
                    ts = datetime.strptime(dt_val.strip()[:19], "%Y-%m-%d %H:%M:%S")
                else:
                    continue
            except (ValueError, AttributeError):
                continue

            if start_date:
                try:
                    sd = datetime.strptime(start_date, "%Y-%m-%d")
                    if ts < sd:
                        continue
                except ValueError:
                    pass
            if end_date:
                try:
                    ed = datetime.strptime(end_date, "%Y-%m-%d")
                    if ts > ed:
                        continue
                except ValueError:
                    pass

            readings.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "kw": round(kw_float, 4),
                "customer_type": ctype,
                "site": community,
                "meterid": mid,
            })

        # -- 4. Summary --
        type_counts: Dict[str, int] = defaultdict(int)
        site_counts: Dict[str, int] = defaultdict(int)
        for r in readings:
            type_counts[r["customer_type"]] += 1
            site_counts[r["site"]] += 1

        return {
            "readings": readings,
            "meta": {
                "total_readings": len(readings),
                "skipped_no_type": skipped,
                "meter_source": "meters",
                "customer_types": dict(type_counts),
                "sites": dict(site_counts),
                "filters": {
                    "customer_type": customer_type,
                    "site": site,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            },
        }
