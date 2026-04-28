"""
Dashboard statistics endpoints.

Computes aggregated MWh consumed and '000 LSL sold per site
from the transactions table (consolidated history).

Expensive queries are cached in-memory with a short TTL so that
the dashboard loads instantly for concurrent/repeated requests.
"""

import calendar
import logging
import os
import time
import threading
from datetime import date, timezone, datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from fastapi import APIRouter, Depends, Query

from models import CurrentUser
from middleware import require_employee

BJ_DATABASE_URL = os.environ.get(
    "BJ_DATABASE_URL",
    "postgresql://cc_api:gKkYLkzYwSRPNoSwuC87YVqbzCmnhI4e@localhost:5432/onepower_bj",
)

logger = logging.getLogger("acdb-api.stats")

router = APIRouter(prefix="/api/stats", tags=["stats"])

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 600


def _get_cached(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.monotonic() - entry[0]) < CACHE_TTL_SECONDS:
            return entry[1]
    return None


def _set_cached(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


_warm_scheduled = False


def warm_stats_cache():
    """Schedule a deferred, low-priority cache warm in a background thread.

    Waits 120 seconds after boot to avoid contending with other startup
    work and heavy import jobs.  Uses statement_timeout to bail out if
    the query takes too long, preventing runaway memory consumption.
    """
    global _warm_scheduled
    if _warm_scheduled:
        return
    _warm_scheduled = True

    def _warm():
        time.sleep(120)
        try:
            from models import CurrentUser
            fake_user = CurrentUser(
                user_type="employee", user_id="system",
                name="cache-warm", role="superadmin",
            )
            logger.info("Pre-warming dashboard stats cache (deferred)...")
            t0 = time.monotonic()
            site_summary(fake_user)
            customer_record_completeness(fake_user)
            logger.info("Dashboard cache warmed in %.1fs", time.monotonic() - t0)
        except Exception:
            logger.warning("Cache warm-up failed (non-fatal); first user request will compute live")

    t = threading.Thread(target=_warm, daemon=True)
    t.start()


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s)",
        (table_name,),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s)",
        (table_name, column_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    columns = [desc[0] for desc in cursor.description]
    return {col: val for col, val in zip(columns, row)}


def _extract_site(account_number: str) -> str:
    """Extract site code from the last 3 chars of account number (e.g. 0003MAS -> MAS).

    Only returns a value if it matches a known site code in *any* registered
    country (1PDB is consolidated, country-aware), so cross-country accounts
    such as ``0001GBO`` are kept regardless of the active ``COUNTRY_CODE``.
    """
    from country_config import ALL_KNOWN_SITES
    if not account_number:
        return ""
    candidate = account_number.strip()[-3:].upper()
    return candidate if candidate in ALL_KNOWN_SITES else ""


@router.get("/site-summary")
def site_summary(user: CurrentUser = Depends(require_employee)):
    """
    Aggregate MWh consumed and revenue per site.

    Primary source: ``transactions`` (detailed per-txn rows, typical for LS).
    Fallback: ``monthly_consumption`` + ``monthly_transactions`` (aggregate
    tables populated by Koios import, typical for BN and new countries).
    """
    cached = _get_cached("site-summary")
    if cached is not None:
        return cached

    results: Dict[str, Dict[str, float]] = {}
    source_table = "transactions"

    with _get_connection() as conn:
        cursor = conn.cursor()

        # --- Strategy 1: detailed transactions table ---
        try:
            cursor.execute("""
                SELECT account_number,
                       SUM(kwh_value) AS total_kwh,
                       SUM(transaction_amount) AS total_amt
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
                results[site]["mwh"] += float(row[1] or 0) / 1000.0
                results[site]["lsl_thousands"] += float(row[2] or 0) / 1000.0

        except Exception as e:
            logger.warning("Failed to query transactions: %s", e)

        # --- Strategy 2: monthly aggregates (fallback) ---
        if not results:
            source_table = "monthly_consumption+monthly_transactions"
            try:
                cursor.execute("""
                    SELECT community, SUM(kwh)
                    FROM monthly_consumption
                    WHERE community IS NOT NULL
                    GROUP BY community
                """)
                for row in cursor.fetchall():
                    site = str(row[0]).strip().upper()
                    if site:
                        results.setdefault(site, {"mwh": 0.0, "lsl_thousands": 0.0})
                        results[site]["mwh"] += float(row[1] or 0) / 1000.0
            except Exception as e:
                logger.warning("Failed to query monthly_consumption: %s", e)

            try:
                cursor.execute("""
                    SELECT community, SUM(amount_lsl)
                    FROM monthly_transactions
                    WHERE community IS NOT NULL
                    GROUP BY community
                """)
                for row in cursor.fetchall():
                    site = str(row[0]).strip().upper()
                    if site:
                        results.setdefault(site, {"mwh": 0.0, "lsl_thousands": 0.0})
                        results[site]["lsl_thousands"] += float(row[1] or 0) / 1000.0
            except Exception as e:
                logger.warning("Failed to query monthly_transactions: %s", e)

    from country_config import ALL_KNOWN_SITES
    results = {k: v for k, v in results.items() if k in ALL_KNOWN_SITES}

    total_mwh = sum(s["mwh"] for s in results.values())
    total_lsl = sum(s["lsl_thousands"] for s in results.values())

    sites = []
    for site_code in sorted(results.keys()):
        data = results[site_code]
        sites.append({
            "site": site_code,
            "mwh": round(data["mwh"], 2),
            "lsl_thousands": round(data["lsl_thousands"], 2),
        })

    response = {
        "sites": sites,
        "totals": {
            "mwh": round(total_mwh, 2),
            "lsl_thousands": round(total_lsl, 2),
        },
        "source_table": source_table,
        "site_count": len(sites),
    }
    _set_cached("site-summary", response)
    return response


@router.get("/customer-record-completeness")
def customer_record_completeness(user: CurrentUser = Depends(require_employee)):
    """
    Summarize hourly 1PDB record coverage by customer type.

    Completeness is measured as:
      distinct account-hours present in ``hourly_consumption``
      -------------------------------------------------------
      expected account-hours from commissioning date to data horizon

    The expected window starts at ``date_service_connected`` (day-truncated) and
    ends at the earlier of the latest loaded hourly record or
    ``date_service_terminated`` (if present).
    """
    cached = _get_cached("customer-record-completeness")
    if cached is not None:
        return cached

    empty_totals = {
        "customer_count": 0,
        "customers_with_account": 0,
        "commissioned_customers": 0,
        "account_count": 0,
        "commissioned_accounts": 0,
        "accounts_with_records": 0,
        "actual_records": 0,
        "expected_records": 0,
        "completeness_pct": None,
    }

    with _get_connection() as conn:
        cursor = conn.cursor()

        if not _table_exists(cursor, "customers") or not _table_exists(cursor, "accounts"):
            return {
                "rows": [],
                "totals": empty_totals,
                "data_as_of": None,
                "record_source": "hourly_consumption",
                "note": "Customers/accounts tables are unavailable in this environment.",
            }

        if not _table_exists(cursor, "hourly_consumption"):
            return {
                "rows": [],
                "totals": empty_totals,
                "data_as_of": None,
                "record_source": "hourly_consumption",
                "note": "The hourly_consumption table is unavailable in this environment.",
            }

        has_customer_type = _column_exists(cursor, "customers", "customer_type")
        has_customer_position = _column_exists(cursor, "customers", "customer_position")
        has_connected = _column_exists(cursor, "customers", "date_service_connected")
        has_terminated = _column_exists(cursor, "customers", "date_service_terminated")

        type_sources: List[str] = []
        if has_customer_type:
            type_sources.append("NULLIF(UPPER(BTRIM(COALESCE(c.customer_type::text, ''))), '')")
        if has_customer_position:
            type_sources.append("NULLIF(UPPER(BTRIM(COALESCE(c.customer_position::text, ''))), '')")

        customer_type_sql = (
            f"COALESCE({', '.join(type_sources)}, 'UNKNOWN')" if type_sources else "'UNKNOWN'"
        )
        commissioned_sql = (
            "c.date_service_connected::timestamp" if has_connected else "NULL::timestamp"
        )
        terminated_sql = (
            "c.date_service_terminated::timestamp" if has_terminated else "NULL::timestamp"
        )

        cursor.execute("SELECT MAX(last_record_at) FROM mv_hourly_account_summary")
        data_as_of = cursor.fetchone()[0]

        completeness_sql = f"""
            WITH customer_accounts AS (
                SELECT DISTINCT
                    c.id AS customer_pk,
                    c.customer_id_legacy,
                    {customer_type_sql} AS customer_type,
                    a.account_number,
                    {commissioned_sql} AS commissioned_at,
                    {terminated_sql} AS terminated_at
                FROM customers c
                LEFT JOIN accounts a ON a.customer_id = c.id
            ),
            first_transaction AS (
                SELECT account_number,
                       MIN(CASE WHEN year_month ~ '^\d{{4}}-\d{{2}}$'
                                THEN (year_month || '-01')::date
                                ELSE NULL END) AS first_txn
                FROM monthly_transactions
                GROUP BY account_number
            ),
            service_windows AS (
                SELECT
                    ca.customer_pk,
                    ca.customer_id_legacy,
                    ca.customer_type,
                    ca.account_number,
                    CASE
                        WHEN %s::timestamp IS NULL THEN NULL
                        WHEN ft.first_txn IS NOT NULL
                            THEN ft.first_txn::timestamp
                        ELSE NULL
                    END AS window_start,
                    CASE
                        WHEN %s::timestamp IS NULL THEN NULL
                        WHEN ft.first_txn IS NOT NULL
                            THEN LEAST(
                                %s::timestamp + INTERVAL '1 hour',
                                COALESCE(
                                    date_trunc('day', ca.terminated_at) + INTERVAL '1 day',
                                    %s::timestamp + INTERVAL '1 hour'
                                )
                            )
                        ELSE NULL
                    END AS window_end
                FROM customer_accounts ca
                LEFT JOIN first_transaction ft ON ft.account_number = ca.account_number
            ),
            records_by_account AS (
                SELECT
                    sw.customer_pk,
                    sw.customer_id_legacy,
                    sw.customer_type,
                    sw.account_number,
                    sw.window_start,
                    sw.window_end,
                    CASE
                        WHEN sw.window_start IS NULL OR sw.window_end IS NULL OR sw.window_end <= sw.window_start
                            THEN 0::bigint
                        ELSE FLOOR(EXTRACT(EPOCH FROM (sw.window_end - sw.window_start)) / 3600)::bigint
                    END AS expected_records,
                    COALESCE(hs.distinct_hours, 0)::bigint AS actual_records,
                    hs.first_record_at,
                    hs.last_record_at
                FROM service_windows sw
                LEFT JOIN mv_hourly_account_summary hs
                    ON sw.account_number IS NOT NULL
                   AND hs.account_number = sw.account_number
            )
            SELECT
                customer_type,
                COUNT(DISTINCT customer_pk)::bigint AS customer_count,
                COUNT(DISTINCT customer_pk) FILTER (WHERE account_number IS NOT NULL)::bigint AS customers_with_account,
                COUNT(DISTINCT customer_pk) FILTER (WHERE window_start IS NOT NULL)::bigint AS commissioned_customers,
                COUNT(DISTINCT account_number) FILTER (WHERE account_number IS NOT NULL)::bigint AS account_count,
                COUNT(DISTINCT account_number) FILTER (WHERE window_start IS NOT NULL)::bigint AS commissioned_accounts,
                COUNT(DISTINCT account_number) FILTER (WHERE actual_records > 0)::bigint AS accounts_with_records,
                COALESCE(SUM(actual_records), 0)::bigint AS actual_records,
                COALESCE(SUM(expected_records), 0)::bigint AS expected_records,
                MIN(first_record_at) AS first_record_at,
                MAX(last_record_at) AS last_record_at
            FROM records_by_account
            GROUP BY customer_type
            ORDER BY COUNT(DISTINCT customer_pk) DESC, customer_type
        """
        cursor.execute(completeness_sql, (data_as_of, data_as_of, data_as_of, data_as_of))
        query_rows = [_row_to_dict(cursor, row) for row in cursor.fetchall()]

    rows = []
    for row in query_rows:
        actual_records = int(row.get("actual_records") or 0)
        expected_records = int(row.get("expected_records") or 0)
        completeness_pct: Optional[float] = None
        if expected_records > 0:
            completeness_pct = round(actual_records / expected_records * 100.0, 1)
        rows.append({
            "customer_type": str(row.get("customer_type") or "UNKNOWN"),
            "customer_count": int(row.get("customer_count") or 0),
            "customers_with_account": int(row.get("customers_with_account") or 0),
            "commissioned_customers": int(row.get("commissioned_customers") or 0),
            "account_count": int(row.get("account_count") or 0),
            "commissioned_accounts": int(row.get("commissioned_accounts") or 0),
            "accounts_with_records": int(row.get("accounts_with_records") or 0),
            "actual_records": actual_records,
            "expected_records": expected_records,
            "completeness_pct": completeness_pct,
            "first_record_at": row.get("first_record_at").isoformat() if row.get("first_record_at") else None,
            "last_record_at": row.get("last_record_at").isoformat() if row.get("last_record_at") else None,
        })

    totals = {
        "customer_count": sum(r["customer_count"] for r in rows),
        "customers_with_account": sum(r["customers_with_account"] for r in rows),
        "commissioned_customers": sum(r["commissioned_customers"] for r in rows),
        "account_count": sum(r["account_count"] for r in rows),
        "commissioned_accounts": sum(r["commissioned_accounts"] for r in rows),
        "accounts_with_records": sum(r["accounts_with_records"] for r in rows),
        "actual_records": sum(r["actual_records"] for r in rows),
        "expected_records": sum(r["expected_records"] for r in rows),
        "completeness_pct": None,
    }
    if totals["expected_records"] > 0:
        totals["completeness_pct"] = round(
            totals["actual_records"] / totals["expected_records"] * 100.0,
            1,
        )

    note = (
        "Completeness uses distinct hourly_consumption hours between each customer's "
        "commissioning date and the earlier of the latest loaded hour or termination date."
    )
    if data_as_of is None:
        note = (
            "No hourly_consumption records are loaded yet, so expected-vs-actual "
            "completeness percentages are unavailable."
        )

    response = {
        "rows": rows,
        "totals": totals,
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "record_source": "hourly_consumption",
        "note": note,
    }
    _set_cached("customer-record-completeness", response)
    return response


# ---------------------------------------------------------------------------
# Cross-country revenue / ARPU summary (rolling 12 months)
# ---------------------------------------------------------------------------

_FX_TO_USD = {
    "LSL": 0.054,
    "XOF": 0.0016,
}


def _country_monthly_revenue(conn, country: str, currency: str, months: int) -> List[Dict[str, Any]]:
    """Query monthly_transactions for a single country DB.

    Handles two data shapes:
      - Per-customer rows (LS): COUNT(DISTINCT account_number) for paying customers
      - Site-level aggregates (BJ): account_number like 'SITE_%'; falls back to
        counting accounts with consumption in that month.

    Returns list of {month, revenue_local, paying_customers, currency, country}.
    """
    cursor = conn.cursor()

    cutoff = f"to_char(NOW() - interval '{int(months)} months', 'YYYY-MM')"

    cursor.execute(
        "SELECT year_month, "
        f"      SUM(amount_lsl)::numeric(14,2) AS revenue, "
        "       COUNT(DISTINCT account_number) AS acct_count, "
        "       COUNT(DISTINCT CASE WHEN amount_lsl > 0 "
        "                             AND account_number NOT LIKE 'SITE_%%' "
        "                             THEN account_number END) AS real_acct_count "
        "FROM monthly_transactions "
        f"WHERE year_month >= {cutoff} "
        "GROUP BY year_month "
        "ORDER BY year_month"
    )
    raw = cursor.fetchall()

    has_consumption_table = _table_exists(cursor, "monthly_consumption") or _table_exists(cursor, "hourly_consumption")

    results = []
    for r in raw:
        ym = str(r[0])
        revenue = float(r[1])
        real_accts = int(r[3])

        if real_accts > 0:
            paying = real_accts
        elif has_consumption_table:
            try:
                cursor.execute(
                    "SELECT COUNT(DISTINCT account_number) "
                    "FROM monthly_consumption "
                    "WHERE year_month = %s AND kwh > 0",
                    (ym,),
                )
                row = cursor.fetchone()
                paying = int(row[0]) if row and row[0] else 0
            except Exception:
                conn.rollback()
                paying = int(r[2])
        else:
            paying = int(r[2])

        results.append({
            "month": ym,
            "revenue_local": revenue,
            "paying_customers": paying,
            "currency": currency,
            "country": country,
        })
    return results


def _country_active_connections(conn) -> int:
    """Count active connections (accounts with active meters, non-deleted customers)."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(DISTINCT a.account_number) "
            "FROM accounts a "
            "JOIN meters m ON m.account_number = a.account_number "
            "  AND m.status = 'active' "
            "LEFT JOIN soft_deletes sd "
            "  ON sd.table_name = 'customers' "
            "  AND sd.record_id = CAST(a.customer_id AS TEXT) "
            "WHERE sd.record_id IS NULL"
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        conn.rollback()
        return 0


@router.get("/revenue-summary")
def revenue_summary(
    user: CurrentUser = Depends(require_employee),
    months: int = Query(default=12, ge=3, le=36, description="Rolling window in months"),
):
    """
    Cross-country 12-month rolling revenue, customer count, and ARPU.

    Returns per-country monthly breakdowns in local currency, plus
    a consolidated USD-equivalent series.
    """
    cache_key = f"revenue-summary-{months}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    countries: List[Dict[str, Any]] = []

    # -- Lesotho (onepower_cc) --
    try:
        with _get_connection() as conn:
            ls_months = _country_monthly_revenue(conn, "LS", "LSL", months)
            ls_connections = _country_active_connections(conn)
            countries.append({
                "country": "LS",
                "country_name": "Lesotho",
                "currency": "LSL",
                "fx_to_usd": _FX_TO_USD["LSL"],
                "active_connections": ls_connections,
                "months": ls_months,
            })
    except Exception as e:
        logger.warning("LS revenue query failed: %s", e)

    # -- Benin (onepower_bj) --
    try:
        bj_conn = psycopg2.connect(BJ_DATABASE_URL)
        bj_conn.autocommit = True
        try:
            bj_months = _country_monthly_revenue(bj_conn, "BJ", "XOF", months)
            bj_connections = _country_active_connections(bj_conn)
            countries.append({
                "country": "BJ",
                "country_name": "Benin",
                "currency": "XOF",
                "fx_to_usd": _FX_TO_USD["XOF"],
                "active_connections": bj_connections,
                "months": bj_months,
            })
        finally:
            bj_conn.close()
    except Exception as e:
        logger.warning("BJ revenue query failed: %s", e)

    # -- Build consolidated USD series --
    monthly_index: Dict[str, Dict[str, Any]] = {}
    for c in countries:
        fx = c["fx_to_usd"]
        for m in c["months"]:
            key = m["month"]
            if key not in monthly_index:
                monthly_index[key] = {
                    "month": key,
                    "revenue_usd": 0.0,
                    "total_paying_customers": 0,
                    "per_country": {},
                }
            monthly_index[key]["revenue_usd"] += round(m["revenue_local"] * fx, 2)
            monthly_index[key]["total_paying_customers"] += m["paying_customers"]
            monthly_index[key]["per_country"][c["country"]] = {
                "revenue_local": m["revenue_local"],
                "paying_customers": m["paying_customers"],
                "currency": m["currency"],
                "revenue_usd": round(m["revenue_local"] * fx, 2),
            }

    consolidated = sorted(monthly_index.values(), key=lambda x: x["month"])

    today = datetime.now(timezone.utc).date()
    current_ym = today.strftime("%Y-%m")
    days_in_current = calendar.monthrange(today.year, today.month)[1]
    current_fraction = today.day / days_in_current

    for entry in consolidated:
        cust = entry["total_paying_customers"]
        raw_arpu = round(entry["revenue_usd"] / cust, 2) if cust > 0 else 0.0
        entry["arpu_usd"] = raw_arpu

        if entry["month"] == current_ym and current_fraction > 0:
            entry["month_fraction"] = round(current_fraction, 4)
            entry["arpu_usd_prorated"] = round(raw_arpu / current_fraction, 2)
        else:
            entry["month_fraction"] = 1.0
            entry["arpu_usd_prorated"] = raw_arpu

    # Per-country ARPU
    for c in countries:
        for m in c["months"]:
            raw = round(m["revenue_local"] / m["paying_customers"], 2) if m["paying_customers"] > 0 else 0.0
            m["arpu_local"] = raw
            if m["month"] == current_ym and current_fraction > 0:
                m["month_fraction"] = round(current_fraction, 4)
                m["arpu_local_prorated"] = round(raw / current_fraction, 2)
            else:
                m["month_fraction"] = 1.0
                m["arpu_local_prorated"] = raw

    result = {
        "countries": countries,
        "consolidated": consolidated,
        "fx_rates": _FX_TO_USD,
        "fx_note": "Approximate indicative rates; not live market rates.",
        "window_months": months,
    }
    _set_cached(cache_key, result)
    return result
