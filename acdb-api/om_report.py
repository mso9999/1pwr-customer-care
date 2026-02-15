"""
O&M Quarterly Report data endpoints.

Auto-generates analytics from ACCDB data to mirror the figures in
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

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from models import CurrentUser
from middleware import require_employee

logger = logging.getLogger("acdb-api.om-report")

router = APIRouter(prefix="/api/om-report", tags=["om-report"])

# Site abbreviation mapping (from Table 1 in the report)
SITE_ABBREV = {
    "MAK": "Ha Makebe",
    "MAS": "Mashai",
    "SHG": "Sehonghong",
    "LEB": "Lebakeng",
    "SEH": "Sehlabathebe",
    "MAT": "Matsoaing",
    "TLH": "Tlhanyaku",
    "TOS": "Tosing",
    "SEB": "Sebapala",
    "RIB": "Ribaneng",
    "KET": "Ketane",
    # PIH clinics
    "NKU": "Ha Nkau",
    "MET": "Methalaneng",
    "BOB": "Bobete",
    "MAN": "Manamaneng",
}

SITE_DISTRICTS = {
    "MAK": "Maseru", "MAS": "Thaba-Tseka", "SHG": "Thaba-Tseka",
    "LEB": "Qacha's Nek", "SEH": "Qacha's Nek", "MAT": "Mokhotlong",
    "TLH": "Mokhotlong", "TOS": "Quthing", "SEB": "Quthing",
    "RIB": "Mafeteng", "KET": "Mohale's Hoek",
    "NKU": "Maseru", "MET": "Thaba-Tseka", "BOB": "Thaba-Tseka",
    "MAN": "Thaba-Tseka",
}


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _extract_site(account_number: str) -> str:
    """Extract site code from the last 3 chars of account number."""
    if not account_number:
        return ""
    return account_number.strip()[-3:].upper()


def _date_to_quarter(dt) -> str:
    """Convert a date/datetime to 'YYYY QN' string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        # Try parsing common date formats
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


def _find_date_column(cursor, table_name: str) -> Optional[str]:
    """Discover the date column in an account history table."""
    try:
        cols = cursor.columns(table=table_name)
        date_candidates = []
        for col in cols:
            name = col.column_name
            tname = (col.type_name or "").upper()
            if "DATE" in tname or "DATETIME" in tname or "TIMESTAMP" in tname:
                date_candidates.append(name)
            elif "date" in name.lower():
                date_candidates.append(name)
        # Prefer columns with 'date' in the name
        for c in date_candidates:
            if "date" in c.lower():
                return c
        return date_candidates[0] if date_candidates else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Portfolio Overview
# ---------------------------------------------------------------------------

@router.get("/overview")
def report_overview(user: CurrentUser = Depends(require_employee)):
    """Summary statistics for the report header."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        # Total customers
        cursor.execute("SELECT COUNT(*) FROM tblcustomer")
        total_customers = cursor.fetchone()[0]

        # Customers with terminated date
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM tblcustomer WHERE [DATE SERVICE TERMINATED] IS NOT NULL "
                "AND [DATE SERVICE TERMINATED] <> ''"
            )
            terminated = cursor.fetchone()[0]
        except Exception:
            terminated = 0

        active_customers = total_customers - terminated

        # Sites (concessions)
        cursor.execute(
            "SELECT DISTINCT [Concession name] FROM tblcustomer "
            "WHERE [Concession name] IS NOT NULL AND [Concession name] <> ''"
        )
        sites = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]

        # Total consumption and sales
        tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]
        total_kwh = 0.0
        total_lsl = 0.0
        for table in tables_to_try:
            try:
                cursor.execute(f"SELECT SUM([kwh value]), SUM([transaction amount]) FROM [{table}]")
                row = cursor.fetchone()
                if row and row[0] is not None:
                    total_kwh = float(row[0] or 0)
                    total_lsl = float(row[1] or 0)
                    break
            except Exception:
                continue

        return {
            "total_customers": total_customers,
            "active_customers": active_customers,
            "terminated_customers": terminated,
            "total_sites": len(sites),
            "sites": sites,
            "total_mwh": round(total_kwh / 1000, 2),
            "total_lsl_thousands": round(total_lsl / 1000, 2),
        }


# ---------------------------------------------------------------------------
# 2. Customer Statistics per Site (Figure 14)
# ---------------------------------------------------------------------------

@router.get("/customer-stats")
def customer_stats_by_site(
    quarter: Optional[str] = Query(None, description="Quarter in YYYY QN format, e.g. '2025 Q4'"),
    user: CurrentUser = Depends(require_employee),
):
    """Customer counts per concession: total, active, and new in the specified quarter."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        # All customers grouped by concession
        cursor.execute(
            "SELECT [Concession name], [CUSTOMER ID], "
            "[DATE SERVICE CONNECTED], [DATE SERVICE TERMINATED] "
            "FROM tblcustomer WHERE [Concession name] IS NOT NULL"
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

            # Active = not terminated
            is_terminated = terminated_date is not None and str(terminated_date).strip() != ""
            if not is_terminated:
                sites[concession]["active"] += 1

            # New in quarter
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
            "SELECT [DATE SERVICE CONNECTED] FROM tblcustomer "
            "WHERE [DATE SERVICE CONNECTED] IS NOT NULL"
        )
        rows = cursor.fetchall()

        quarterly: Dict[str, int] = defaultdict(int)
        for row in rows:
            q = _date_to_quarter(row[0])
            if q:
                quarterly[q] += 1

        # Sort by quarter and compute cumulative
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
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)

                if date_col:
                    cursor.execute(
                        f"SELECT [accountnumber], [{date_col}], [kwh value] FROM [{table}]"
                    )
                else:
                    cursor.execute(
                        f"SELECT [accountnumber], NULL, [kwh value] FROM [{table}]"
                    )

                rows = cursor.fetchall()
                if not rows:
                    continue

                # Per-site, per-quarter aggregation
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

                # Build response
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
                    "source_table": table,
                    "quarter_filter": quarter,
                }

            except Exception as e:
                logger.warning("Failed to query %s for consumption: %s", table, e)
                continue

        return {"sites": [], "total_kwh": 0, "error": "No account history data found"}


# ---------------------------------------------------------------------------
# 5. Sales by Site per Quarter (Figure 6)
# ---------------------------------------------------------------------------

@router.get("/sales-by-site")
def sales_by_site(
    quarter: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """LSL revenue per site, optionally filtered by quarter."""
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)

                if date_col:
                    cursor.execute(
                        f"SELECT [accountnumber], [{date_col}], [transaction amount] FROM [{table}]"
                    )
                else:
                    cursor.execute(
                        f"SELECT [accountnumber], NULL, [transaction amount] FROM [{table}]"
                    )

                rows = cursor.fetchall()
                if not rows:
                    continue

                site_quarter: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
                site_totals: Dict[str, float] = defaultdict(float)

                for row in rows:
                    acct = str(row[0] or "").strip()
                    site = _extract_site(acct)
                    if not site or len(site) < 2:
                        continue

                    lsl = float(row[2] or 0)
                    q = _date_to_quarter(row[1]) if row[1] else "Unknown"

                    if quarter and q != quarter:
                        continue

                    site_quarter[site][q] += lsl
                    site_totals[site] += lsl

                per_site = []
                for site_code in sorted(site_totals.keys()):
                    quarters_data = {q: round(v, 2) for q, v in sorted(site_quarter[site_code].items())}
                    per_site.append({
                        "site": site_code,
                        "name": SITE_ABBREV.get(site_code, site_code),
                        "total_lsl": round(site_totals[site_code], 2),
                        "quarters": quarters_data,
                    })

                return {
                    "sites": per_site,
                    "total_lsl": round(sum(site_totals.values()), 2),
                    "source_table": table,
                    "quarter_filter": quarter,
                }

            except Exception as e:
                logger.warning("Failed to query %s for sales: %s", table, e)
                continue

        return {"sites": [], "total_lsl": 0, "error": "No account history data found"}


# ---------------------------------------------------------------------------
# 6. Cumulative Consumption & Sales Trends (Figures 3, 4)
# ---------------------------------------------------------------------------

@router.get("/cumulative-trends")
def cumulative_trends(user: CurrentUser = Depends(require_employee)):
    """Quarterly cumulative consumption (kWh) and sales (LSL) over time."""
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)
                if not date_col:
                    continue

                cursor.execute(
                    f"SELECT [{date_col}], [kwh value], [transaction amount] FROM [{table}]"
                )
                rows = cursor.fetchall()
                if not rows:
                    continue

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

                return {"trends": result, "source_table": table}

            except Exception as e:
                logger.warning("Failed to query %s for cumulative: %s", table, e)
                continue

        return {"trends": [], "error": "No date column found in account history"}


# ---------------------------------------------------------------------------
# 7. Average Consumption per Customer Trend (Figures 8, 9)
# ---------------------------------------------------------------------------

@router.get("/avg-consumption-trend")
def avg_consumption_trend(user: CurrentUser = Depends(require_employee)):
    """Average daily consumption and sales per customer per quarter."""
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        # First get customer counts per quarter from connection dates
        cursor.execute(
            "SELECT [DATE SERVICE CONNECTED] FROM tblcustomer "
            "WHERE [DATE SERVICE CONNECTED] IS NOT NULL"
        )
        cust_rows = cursor.fetchall()

        # Build cumulative customer count per quarter
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

        # Then get consumption/sales per quarter
        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)
                if not date_col:
                    continue

                cursor.execute(
                    f"SELECT [{date_col}], [kwh value], [transaction amount] FROM [{table}]"
                )
                rows = cursor.fetchall()
                if not rows:
                    continue

                quarterly_kwh: Dict[str, float] = defaultdict(float)
                quarterly_lsl: Dict[str, float] = defaultdict(float)
                quarterly_days: Dict[str, set] = defaultdict(set)

                for row in rows:
                    q = _date_to_quarter(row[0])
                    if not q:
                        continue
                    quarterly_kwh[q] += float(row[1] or 0)
                    quarterly_lsl[q] += float(row[2] or 0)
                    # Track unique days for daily average
                    try:
                        if hasattr(row[0], 'date'):
                            quarterly_days[q].add(row[0].date())
                        elif isinstance(row[0], str):
                            quarterly_days[q].add(row[0][:10])
                    except Exception:
                        pass

                sorted_q = sorted(set(quarterly_kwh.keys()) & set(cum_customers.keys()))
                result = []
                for q in sorted_q:
                    customers = cum_customers.get(q, 1)
                    days = len(quarterly_days.get(q, set())) or 90  # ~90 days per quarter
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

                return {"trends": result, "source_table": table}

            except Exception as e:
                logger.warning("Failed to query %s for avg trend: %s", table, e)
                continue

        return {"trends": [], "error": "No data found"}


# ---------------------------------------------------------------------------
# 8. Site Overview with Districts (Tables 1, 2, 3)
# ---------------------------------------------------------------------------

@router.get("/site-overview")
def site_overview(user: CurrentUser = Depends(require_employee)):
    """List of all concessions with customer counts and district info."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT [Concession name], COUNT(*) as cnt "
            "FROM tblcustomer "
            "WHERE [Concession name] IS NOT NULL AND [Concession name] <> '' "
            "GROUP BY [Concession name] "
            "ORDER BY [Concession name]"
        )
        rows = cursor.fetchall()

        sites = []
        for row in rows:
            name = str(row[0]).strip()
            count = row[1]
            # Try to match abbreviation
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
#
# Uses "Copy Of tblmeter" (the ACCDB meter registry) which contains:
#   meterid, accountnumber, customer id, customer type, latitude, longitude, community
# Joined with tblaccounthistory1 on accountnumber for consumption data.
# No uGridPLAN sync required -- ACCDB is the source of truth for meter/type/GPS.

# Meter tables to try (prefer "Copy Of tblmeter" with 5k+ rows over "tblmeter" with 33)
_METER_TABLES = ["Copy Of tblmeter", "tblmeter"]


@router.get("/load-curves-by-type")
def load_curves_by_type(
    quarter: Optional[str] = Query(None, description="Filter to quarter, e.g. '2025 Q4'"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Average daily consumption per customer type.
    Joins ACCDB meter table (customer type + account number) with account history.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # 1. Build account -> customer_type mapping from the ACCDB meter table
        acct_type: Dict[str, str] = {}
        meter_source = ""

        for meter_table in _METER_TABLES:
            try:
                cursor.execute(
                    f"SELECT [accountnumber], [customer type] FROM [{meter_table}] "
                    f"WHERE [customer type] IS NOT NULL AND [customer type] <> ''"
                )
                for row in cursor.fetchall():
                    acct = str(row[0] or "").strip()
                    ctype = str(row[1] or "").strip()
                    if acct and ctype:
                        acct_type[acct] = ctype
                if acct_type:
                    meter_source = meter_table
                    break
            except Exception as e:
                logger.warning("Could not read %s: %s", meter_table, e)
                continue

        if not acct_type:
            return {
                "curves": [],
                "quarterly": [],
                "note": "No customer type data found in meter tables.",
            }

        # 2. Query account history
        tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)
                if date_col:
                    cursor.execute(
                        f"SELECT [accountnumber], [{date_col}], [kwh value], [transaction amount] FROM [{table}]"
                    )
                else:
                    cursor.execute(
                        f"SELECT [accountnumber], NULL, [kwh value], [transaction amount] FROM [{table}]"
                    )

                rows = cursor.fetchall()
                if not rows:
                    continue

                # Aggregate by customer type
                type_totals: Dict[str, Dict[str, Any]] = defaultdict(
                    lambda: {"kwh": 0.0, "lsl": 0.0, "customers": set(), "days": set()}
                )
                # Quarterly breakdown by type
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
                    if row[1]:
                        try:
                            if hasattr(row[1], 'date'):
                                type_totals[ctype]["days"].add(row[1].date())
                            elif isinstance(row[1], str):
                                type_totals[ctype]["days"].add(row[1][:10])
                        except Exception:
                            pass

                    type_quarter[ctype][q]["kwh"] += kwh
                    type_quarter[ctype][q]["lsl"] += lsl

                # Build response
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

                # Quarterly stacked data
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
                    "source_table": table,
                    "meter_source": meter_source,
                    "quarter_filter": quarter,
                }

            except Exception as e:
                logger.warning("Failed to query %s for load curves: %s", table, e)
                continue

        return {"curves": [], "quarterly": [], "error": "No account history data found"}


# ---------------------------------------------------------------------------
# 10. 24-Hour Daily Load Profiles by Customer Type
# ---------------------------------------------------------------------------
#
# Uses tblmeterdata1 (10-minute interval readings: whdatetime, powerkW, meterid)
# joined with meter registry (meterid -> customer type) to build average
# hourly power curves for each customer type.

@router.get("/daily-load-profiles")
def daily_load_profiles(
    site: Optional[str] = Query(None, description="Filter to site code (e.g. MAK)"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Average 24-hour load profiles by customer type.
    Returns average kW for each hour (0-23) per type, derived from
    10-minute meter readings in tblmeterdata1.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # 1. Build meterid -> customer_type mapping from meter registry
        meter_type: Dict[str, str] = {}
        meter_source = ""
        for meter_table in _METER_TABLES:
            try:
                if site:
                    cursor.execute(
                        f"SELECT [meterid], [customer type] FROM [{meter_table}] "
                        f"WHERE [customer type] IS NOT NULL AND [customer type] <> '' "
                        f"AND [community] = ?",
                        (site.upper(),),
                    )
                else:
                    cursor.execute(
                        f"SELECT [meterid], [customer type] FROM [{meter_table}] "
                        f"WHERE [customer type] IS NOT NULL AND [customer type] <> ''"
                    )
                for row in cursor.fetchall():
                    mid = str(row[0] or "").strip()
                    ctype = str(row[1] or "").strip()
                    if mid and ctype:
                        meter_type[mid] = ctype
                if meter_type:
                    meter_source = meter_table
                    break
            except Exception as e:
                logger.warning("Could not read meter types from %s: %s", meter_table, e)
                continue

        if not meter_type:
            return {
                "profiles": [],
                "note": "No customer type data found in meter tables.",
            }

        # 2. Query tblmeterdata1 for timestamped readings
        #    Extract hour from whdatetime, group by type + hour, average kW
        try:
            # Build SQL with meterid filter for efficiency
            meter_ids = list(meter_type.keys())

            # For large sets, query all and filter in Python
            if site:
                cursor.execute(
                    "SELECT [meterid], [whdatetime], [powerkW] FROM [tblmeterdata1] "
                    "WHERE [community] = ? AND [powerkW] IS NOT NULL",
                    (site.upper(),),
                )
            else:
                cursor.execute(
                    "SELECT [meterid], [whdatetime], [powerkW] FROM [tblmeterdata1] "
                    "WHERE [powerkW] IS NOT NULL"
                )

            # Aggregate: type -> hour -> list of kW readings
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

                # Extract hour
                try:
                    if hasattr(dt, 'hour'):
                        hour = dt.hour
                    elif isinstance(dt, str):
                        # Parse "YYYY-MM-DD HH:MM:SS"
                        hour = int(dt.split(" ")[1].split(":")[0])
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
                    n_meters = len(type_meter_count[ctype])
                    avg_kw = sum(readings) / len(readings) if readings else 0
                    # Average per meter (divide total by number of meters)
                    avg_kw_per_meter = avg_kw  # already per-reading average
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

            # Also build a combined chart data array for frontend
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
                "meter_source": meter_source,
                "site_filter": site,
            }

        except Exception as e:
            logger.warning("Failed to query tblmeterdata1 for load profiles: %s", e)
            return {"profiles": [], "error": str(e)}


# ---------------------------------------------------------------------------
# 11. ARPU (Average Revenue Per User) Time Series
# ---------------------------------------------------------------------------

@router.get("/arpu")
def arpu_time_series(user: CurrentUser = Depends(require_employee)):
    """
    Quarterly ARPU: total revenue / active customers per quarter, with per-site breakdown.

    Combines account history revenue data with customer connection/termination dates
    to compute a proper active-customer count for each quarter.
    """
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        # ── 1. Build per-quarter active customer counts (overall + per-site) ──
        cursor.execute(
            "SELECT [CUSTOMER ID], [Concession name], "
            "[DATE SERVICE CONNECTED], [DATE SERVICE TERMINATED] "
            "FROM tblcustomer "
            "WHERE [Concession name] IS NOT NULL"
        )
        cust_rows = cursor.fetchall()

        # Collect all connection quarters to define the timeline
        all_connect_quarters: set = set()
        customers = []
        for row in cust_rows:
            cid = row[0]
            concession = str(row[1] or "").strip()
            connected = row[2]
            terminated = row[3]
            cq = _date_to_quarter(connected) if connected else ""
            tq = _date_to_quarter(terminated) if terminated and str(terminated).strip() else ""
            if cq:
                all_connect_quarters.add(cq)
            customers.append((cid, concession, cq, tq))

        # ── 2. Get revenue per quarter per site from account history ──
        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)
                if not date_col:
                    continue

                cursor.execute(
                    f"SELECT [accountnumber], [{date_col}], [transaction amount] "
                    f"FROM [{table}]"
                )
                txn_rows = cursor.fetchall()
                if not txn_rows:
                    continue

                # Quarterly revenue: overall and per-site
                q_revenue: Dict[str, float] = defaultdict(float)
                q_site_revenue: Dict[str, Dict[str, float]] = defaultdict(
                    lambda: defaultdict(float)
                )

                for row in txn_rows:
                    acct = str(row[0] or "").strip()
                    q = _date_to_quarter(row[1])
                    lsl = float(row[2] or 0)
                    if not q:
                        continue
                    site = _extract_site(acct)
                    q_revenue[q] += lsl
                    if site and len(site) >= 2:
                        q_site_revenue[q][site] += lsl

                # ── 3. For each quarter with revenue, count active customers ──
                all_quarters = sorted(q_revenue.keys())

                result = []
                for q in all_quarters:
                    # A customer is "active" in quarter Q if:
                    #   connected_quarter <= Q  AND  (no termination OR terminated_quarter > Q)
                    active_total = 0
                    site_customers: Dict[str, int] = defaultdict(int)

                    for _cid, concession, cq, tq in customers:
                        if not cq or cq > q:
                            continue
                        if tq and tq <= q:
                            continue
                        active_total += 1
                        # Map concession name to site code
                        site_code = ""
                        for code, name in SITE_ABBREV.items():
                            if name.lower() == concession.lower() or code.lower() in concession.lower():
                                site_code = code
                                break
                        if site_code:
                            site_customers[site_code] += 1

                    revenue = q_revenue[q]
                    arpu = round(revenue / active_total, 2) if active_total > 0 else 0

                    # Per-site ARPU
                    per_site = {}
                    for site_code, site_rev in sorted(q_site_revenue[q].items()):
                        site_custs = site_customers.get(site_code, 0)
                        per_site[site_code] = {
                            "name": SITE_ABBREV.get(site_code, site_code),
                            "revenue": round(site_rev, 2),
                            "customers": site_custs,
                            "arpu": round(site_rev / site_custs, 2) if site_custs > 0 else 0,
                        }

                    result.append({
                        "quarter": q,
                        "total_revenue": round(revenue, 2),
                        "active_customers": active_total,
                        "arpu": arpu,
                        "per_site": per_site,
                    })

                # Collect all site codes seen across all quarters
                all_site_codes = sorted(
                    set(
                        code
                        for entry in result
                        for code in entry["per_site"]
                    )
                )

                return {
                    "arpu": result,
                    "site_codes": all_site_codes,
                    "site_names": {c: SITE_ABBREV.get(c, c) for c in all_site_codes},
                    "source_table": table,
                }

            except Exception as e:
                logger.warning("Failed to compute ARPU from %s: %s", table, e)
                continue

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
    Monthly ARPU: total revenue / distinct transacting customers per month.

    Counts distinct account numbers that made at least one transaction in a
    given month as the "active customer" denominator.  This is more robust
    than connection/termination date matching (used by the quarterly
    endpoint) because every account with revenue is guaranteed to be counted.
    """
    tables_to_try = ["tblaccounthistory1", "tblaccounthistoryOriginal"]

    with _get_connection() as conn:
        cursor = conn.cursor()

        for table in tables_to_try:
            try:
                date_col = _find_date_column(cursor, table)
                if not date_col:
                    continue

                cursor.execute(
                    f"SELECT [accountnumber], [{date_col}], [transaction amount] "
                    f"FROM [{table}]"
                )
                txn_rows = cursor.fetchall()
                if not txn_rows:
                    continue

                # Per-month aggregation
                m_revenue: Dict[str, float] = defaultdict(float)
                m_customers: Dict[str, set] = defaultdict(set)
                m_site_revenue: Dict[str, Dict[str, float]] = defaultdict(
                    lambda: defaultdict(float)
                )
                m_site_customers: Dict[str, Dict[str, set]] = defaultdict(
                    lambda: defaultdict(set)
                )

                for row in txn_rows:
                    acct = str(row[0] or "").strip()
                    m = _date_to_month(row[1])
                    lsl = float(row[2] or 0)
                    if not m or not acct:
                        continue
                    site = _extract_site(acct)

                    m_revenue[m] += lsl
                    m_customers[m].add(acct)
                    if site and len(site) >= 2:
                        m_site_revenue[m][site] += lsl
                        m_site_customers[m][site].add(acct)

                all_months = sorted(m_revenue.keys())

                result = []
                for m in all_months:
                    revenue = m_revenue[m]
                    active = len(m_customers[m])
                    arpu = round(revenue / active, 2) if active > 0 else 0

                    per_site = {}
                    for site_code in sorted(m_site_revenue[m]):
                        site_rev = m_site_revenue[m][site_code]
                        site_custs = len(m_site_customers[m][site_code])
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
                    "source_table": table,
                }

            except Exception as e:
                logger.warning("Failed to compute monthly ARPU from %s: %s", table, e)
                continue

        return {"monthly_arpu": [], "site_codes": [], "error": "No account history data found"}


def _date_to_quarter_from_month(month_str: str) -> str:
    """Convert 'YYYY-MM' to 'YYYY QN'."""
    try:
        y, m = month_str.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"{y} Q{q}"
    except (ValueError, AttributeError):
        return ""
