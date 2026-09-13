"""Read-only forecast series for uGridPREDICT (machine key, no employee JWT).

Additive routes under ``/api/integration/forecast/*``. Same
``X-CC-Integration-Key`` / ``CC_INTEGRATION_KEY`` gate as
``integration.py``. Does not write CC and does not change existing
``/api/integration/om/*`` or ``/doe/*`` payloads.

Canonical connection measure is ``customer_commissioned`` month-end stock.
``service_connected`` is a named sibling only — never a substitute.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Header, HTTPException, Query

from country_config import COUNTRY, live_site_abbrev
from customer_api import get_connection
from integration import (
    _CHURCH_TYPES,
    _CLINIC_TYPES,
    _EXCLUDED_COMMUNITIES,
    _HH_TYPES,
    _SCHOOL_TYPES,
    _BIZ_TYPES,
    _require_key,
)

logger = logging.getLogger("acdb-api.integration-forecast")

router = APIRouter(prefix="/api/integration/forecast", tags=["integration-forecast"])

_SITE_RE = re.compile(r"^[A-Za-z]{3}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DEFAULT_MONTHS = 36

_CLASS_SQL = """
CASE
    WHEN UPPER(TRIM(COALESCE(c.customer_type, ''))) IN %s THEN 'HH'
    WHEN UPPER(TRIM(COALESCE(c.customer_type, ''))) IN %s THEN 'SME'
    WHEN UPPER(TRIM(COALESCE(c.customer_type, ''))) IN %s THEN 'SCH'
    WHEN UPPER(TRIM(COALESCE(c.customer_type, ''))) IN %s THEN 'HC'
    WHEN UPPER(TRIM(COALESCE(c.customer_type, ''))) IN %s THEN 'CHU'
    ELSE 'OTHER'
END
"""

_DEFINITIONS = {
    "customer_commissioned": (
        "Month-end stock of customers with customer_commissioned=TRUE, "
        "customer_commissioned_date on or before the month, and not terminated "
        "before the next month. Lender/board connection measure. Not UGP "
        "St_code_3, not service-connected, not DoE letter counts."
    ),
    "service_connected": (
        "Named sibling only. Month-end stock of date_service_connected. "
        "Do not use as commissioned."
    ),
    "commissioned_missing_date": (
        "customer_commissioned=TRUE but customer_commissioned_date IS NULL. "
        "Counted in the current snapshot gap, not placed in history."
    ),
    "kwh": "Sum of monthly_consumption.kwh for the calendar month.",
    "kwh_per_connection": (
        "kwh / customer_commissioned stock for that site+class+month. "
        "Null when stock is 0. Not blended ARPU."
    ),
    "collected_local": (
        "Cash received: SUM(monthly_transactions.amount_lsl) where amount > 0. "
        "Column name is historical; units are the lane currency."
    ),
    "billed_local": (
        "Prepaid: same as collected_local (energy is sold at payment). "
        "Plus invoiced_revenue.amount_local when that table has rows. "
        "Not ARPU."
    ),
    "join_key": (
        "site_code is the 3-letter PR / CC community code "
        "(customers.community = site_metadata.site_code = PR site code)."
    ),
}


def parse_year_month(value: Optional[str], field: str) -> Optional[date]:
    if value is None or value == "":
        return None
    if not _MONTH_RE.match(value):
        raise HTTPException(400, f"{field} must be YYYY-MM")
    year, month = int(value[:4]), int(value[5:7])
    if month < 1 or month > 12:
        raise HTTPException(400, f"{field} must be YYYY-MM")
    return date(year, month, 1)


def month_window(
    date_from: Optional[str],
    date_to: Optional[str],
    today: Optional[date] = None,
) -> Tuple[date, date]:
    today = today or date.today()
    end = parse_year_month(date_to, "date_to") or date(today.year, today.month, 1)
    start = parse_year_month(date_from, "date_from")
    if start is None:
        year = end.year
        month = end.month - (_DEFAULT_MONTHS - 1)
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
    if start > end:
        raise HTTPException(400, "date_from must be on or before date_to")
    return start, end


def normalize_site(site: Optional[str]) -> Optional[str]:
    if site is None or site == "" or site.upper() == "ALL":
        return None
    if not _SITE_RE.match(site):
        raise HTTPException(400, "site must be a 3-letter PR/CC site code")
    return site.upper()


def _class_params() -> Tuple[tuple, ...]:
    return (_HH_TYPES, _BIZ_TYPES, _SCHOOL_TYPES, _CLINIC_TYPES, _CHURCH_TYPES)


def _site_clause(site: Optional[str]) -> Tuple[str, list]:
    if not site:
        return "", []
    return "AND UPPER(TRIM(c.community)) = %s", [site]


def _row_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (row["year_month"], row["site_code"], row["customer_class"])


def attach_kwh_per_connection(
    stock_rows: Sequence[Dict[str, Any]],
    kwh_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    stock_map = {_row_key(r): r for r in stock_rows}
    kwh_map = {_row_key(r): float(r.get("kwh") or 0) for r in kwh_rows}
    keys = sorted(set(stock_map) | set(kwh_map))
    out: List[Dict[str, Any]] = []
    for key in keys:
        stock = stock_map.get(key, {})
        commissioned = int(stock.get("customer_commissioned") or 0)
        kwh = round(float(kwh_map.get(key, 0.0)), 4)
        out.append(
            {
                "year_month": key[0],
                "site_code": key[1],
                "customer_class": key[2],
                "customer_commissioned": commissioned,
                "kwh": kwh,
                "kwh_per_connection": (
                    round(kwh / commissioned, 4) if commissioned > 0 else None
                ),
            }
        )
    return out


def merge_collections(
    prepaid_rows: Sequence[Dict[str, Any]],
    invoiced_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    prepaid = {_row_key(r): r for r in prepaid_rows}
    invoiced = {_row_key(r): r for r in invoiced_rows}
    keys = sorted(set(prepaid) | set(invoiced))
    out: List[Dict[str, Any]] = []
    for key in keys:
        p = prepaid.get(key, {})
        i = invoiced.get(key, {})
        prepaid_collected = float(p.get("collected_local") or 0)
        invoiced_billed = float(i.get("billed_local") or 0)
        invoiced_collected = float(i.get("collected_local") or 0)
        billed = round(prepaid_collected + invoiced_billed, 2)
        collected = round(prepaid_collected + invoiced_collected, 2)
        out.append(
            {
                "year_month": key[0],
                "site_code": key[1],
                "customer_class": key[2],
                "billed_local": billed,
                "collected_local": collected,
                "prepaid_collected_local": round(prepaid_collected, 2),
                "invoiced_billed_local": round(invoiced_billed, 2),
                "invoiced_collected_local": round(invoiced_collected, 2),
            }
        )
    return out


def month_totals(rows: Iterable[Dict[str, Any]], numeric_keys: Sequence[str]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        month = row["year_month"]
        bucket = buckets.setdefault(month, {"year_month": month, "site_code": "ALL"})
        for key in numeric_keys:
            if row.get(key) is None:
                continue
            bucket[key] = round(float(bucket.get(key) or 0) + float(row[key]), 4)
    for bucket in buckets.values():
        stock = bucket.get("customer_commissioned")
        kwh = bucket.get("kwh")
        if stock is not None and kwh is not None:
            stock_i = int(stock)
            bucket["customer_commissioned"] = stock_i
            bucket["kwh_per_connection"] = (
                round(float(kwh) / stock_i, 4) if stock_i > 0 else None
            )
    return [buckets[k] for k in sorted(buckets)]


def _fetch_stock(cursor, start: date, end: date, site: Optional[str]) -> List[Dict[str, Any]]:
    site_sql, site_params = _site_clause(site)
    cursor.execute(
        f"""
        WITH months AS (
            SELECT generate_series(
                %s::date,
                %s::date,
                interval '1 month'
            )::date AS month_start
        )
        SELECT
            to_char(m.month_start, 'YYYY-MM') AS year_month,
            UPPER(TRIM(c.community)) AS site_code,
            {_CLASS_SQL} AS customer_class,
            COUNT(*) FILTER (
                WHERE c.customer_commissioned
                  AND c.customer_commissioned_date IS NOT NULL
                  AND c.customer_commissioned_date < (m.month_start + interval '1 month')
                  AND (
                      c.date_service_terminated IS NULL
                      OR c.date_service_terminated >= (m.month_start + interval '1 month')
                  )
            ) AS customer_commissioned,
            COUNT(*) FILTER (
                WHERE c.customer_commissioned
                  AND c.customer_commissioned_date IS NULL
                  AND (
                      c.date_service_terminated IS NULL
                      OR c.date_service_terminated >= (m.month_start + interval '1 month')
                  )
            ) AS commissioned_missing_date,
            COUNT(*) FILTER (
                WHERE c.date_service_connected IS NOT NULL
                  AND c.date_service_connected < (m.month_start + interval '1 month')
                  AND (
                      c.date_service_terminated IS NULL
                      OR c.date_service_terminated >= (m.month_start + interval '1 month')
                  )
            ) AS service_connected
        FROM months m
        JOIN customers c
          ON c.community NOT IN %s
         AND c.community IS NOT NULL
         AND TRIM(c.community) <> ''
        WHERE TRUE
          {site_sql}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        (start, end, *_class_params(), _EXCLUDED_COMMUNITIES, *site_params),
    )
    cols = [d[0] for d in cursor.description]
    rows = []
    for raw in cursor.fetchall():
        row = dict(zip(cols, raw))
        rows.append(
            {
                "year_month": row["year_month"],
                "site_code": row["site_code"],
                "customer_class": row["customer_class"],
                "customer_commissioned": int(row["customer_commissioned"] or 0),
                "commissioned_missing_date": int(row["commissioned_missing_date"] or 0),
                "service_connected": int(row["service_connected"] or 0),
            }
        )
    return rows


def _fetch_kwh(cursor, start: date, end: date, site: Optional[str]) -> List[Dict[str, Any]]:
    site_sql, site_params = _site_clause(site)
    cursor.execute(
        f"""
        SELECT
            mc.year_month,
            UPPER(TRIM(c.community)) AS site_code,
            {_CLASS_SQL} AS customer_class,
            COALESCE(SUM(mc.kwh), 0) AS kwh
        FROM monthly_consumption mc
        JOIN accounts a ON a.account_number = mc.account_number
        JOIN customers c ON c.id = a.customer_id
        WHERE mc.year_month >= %s
          AND mc.year_month <= %s
          AND c.community NOT IN %s
          {site_sql}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        (
            start.strftime("%Y-%m"),
            end.strftime("%Y-%m"),
            *_class_params(),
            _EXCLUDED_COMMUNITIES,
            *site_params,
        ),
    )
    return [
        {
            "year_month": row[0],
            "site_code": row[1],
            "customer_class": row[2],
            "kwh": float(row[3] or 0),
        }
        for row in cursor.fetchall()
    ]


def _fetch_prepaid(cursor, start: date, end: date, site: Optional[str]) -> List[Dict[str, Any]]:
    site_sql, site_params = _site_clause(site)
    cursor.execute(
        f"""
        SELECT
            mt.year_month,
            UPPER(TRIM(c.community)) AS site_code,
            {_CLASS_SQL} AS customer_class,
            COALESCE(SUM(mt.amount_lsl), 0) AS collected_local
        FROM monthly_transactions mt
        JOIN accounts a ON a.account_number = mt.account_number
        JOIN customers c ON c.id = a.customer_id
        WHERE mt.year_month >= %s
          AND mt.year_month <= %s
          AND mt.amount_lsl > 0
          AND c.community NOT IN %s
          {site_sql}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        (
            start.strftime("%Y-%m"),
            end.strftime("%Y-%m"),
            *_class_params(),
            _EXCLUDED_COMMUNITIES,
            *site_params,
        ),
    )
    return [
        {
            "year_month": row[0],
            "site_code": row[1],
            "customer_class": row[2],
            "collected_local": float(row[3] or 0),
        }
        for row in cursor.fetchall()
    ]


def _fetch_invoiced(cursor, start: date, end: date, site: Optional[str]) -> List[Dict[str, Any]]:
    cursor.execute("SELECT to_regclass('public.invoiced_revenue')")
    if cursor.fetchone()[0] is None:
        return []
    site_sql = ""
    params: list = [start.strftime("%Y-%m"), end.strftime("%Y-%m")]
    if site:
        site_sql = "AND UPPER(TRIM(ir.site_code)) = %s"
        params.append(site)
    cursor.execute(
        f"""
        SELECT
            ir.period AS year_month,
            UPPER(TRIM(ir.site_code)) AS site_code,
            CASE
                WHEN UPPER(TRIM(COALESCE(ir.customer_type, ''))) IN %s THEN 'HH'
                WHEN UPPER(TRIM(COALESCE(ir.customer_type, ''))) IN %s THEN 'SME'
                WHEN UPPER(TRIM(COALESCE(ir.customer_type, ''))) IN %s THEN 'SCH'
                WHEN UPPER(TRIM(COALESCE(ir.customer_type, ''))) IN %s THEN 'HC'
                WHEN UPPER(TRIM(COALESCE(ir.customer_type, ''))) IN %s THEN 'CHU'
                ELSE 'OTHER'
            END AS customer_class,
            COALESCE(SUM(ir.amount_local), 0) AS billed_local,
            COALESCE(SUM(ir.amount_local) FILTER (
                WHERE LOWER(COALESCE(ir.collection_status, '')) IN ('paid', 'collected')
            ), 0) AS collected_local
        FROM invoiced_revenue ir
        WHERE ir.period >= %s
          AND ir.period <= %s
          {site_sql}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        (*_class_params(), *params),
    )
    return [
        {
            "year_month": row[0],
            "site_code": row[1],
            "customer_class": row[2],
            "billed_local": float(row[3] or 0),
            "collected_local": float(row[4] or 0),
        }
        for row in cursor.fetchall()
    ]


def _envelope(
    start: date,
    end: date,
    site: Optional[str],
    extra: Dict[str, Any],
) -> Dict[str, Any]:
    last_day = calendar.monthrange(end.year, end.month)[1]
    return {
        "as_of": date.today().isoformat(),
        "lane_country": COUNTRY.code,
        "currency": COUNTRY.currency,
        "date_from": start.strftime("%Y-%m"),
        "date_to": end.strftime("%Y-%m"),
        "as_of_month_end": date(end.year, end.month, last_day).isoformat(),
        "site": site,
        "join_key": "site_code",
        "join_key_source": "customers.community = PR 3-letter site code",
        "measure": "customer_commissioned",
        "definitions": _DEFINITIONS,
        **extra,
    }


@router.get("/sites")
def forecast_sites(
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Join-key catalog: 3-letter CC community / PR site codes on this lane."""
    _require_key(x_cc_integration_key)
    names = live_site_abbrev()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT site_code, full_name, status, country
            FROM site_metadata
            ORDER BY site_code
            """
        )
        meta = {
            str(r[0]).upper(): {"name": r[1], "status": r[2], "country": r[3]}
            for r in cursor.fetchall()
            if r[0]
        }
    sites = []
    for code in sorted(set(names) | set(meta)):
        m = meta.get(code, {})
        sites.append(
            {
                "site_code": code,
                "pr_site_code": code,
                "name": m.get("name") or names.get(code) or code,
                "country": m.get("country") or COUNTRY.code,
                "site_status": m.get("status"),
            }
        )
    return {
        "lane_country": COUNTRY.code,
        "join_key": "site_code",
        "join_key_source": "customers.community = site_metadata.site_code = PR 3-letter site code",
        "sites": sites,
        "definitions": {"join_key": _DEFINITIONS["join_key"]},
    }


@router.get("/connections")
def forecast_connections(
    site: Optional[str] = Query(None, description="3-letter PR/CC site code"),
    date_from: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Monthly customer_commissioned stock by site and class, plus siblings."""
    _require_key(x_cc_integration_key)
    site_code = normalize_site(site)
    start, end = month_window(date_from, date_to)
    with get_connection() as conn:
        rows = _fetch_stock(conn.cursor(), start, end, site_code)
    return _envelope(
        start,
        end,
        site_code,
        {
            "rows": rows,
            "totals": month_totals(
                rows,
                ("customer_commissioned", "commissioned_missing_date", "service_connected"),
            ),
        },
    )


@router.get("/consumption")
def forecast_consumption(
    site: Optional[str] = Query(None, description="3-letter PR/CC site code"),
    date_from: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Monthly kWh and kWh per commissioned connection by site and class."""
    _require_key(x_cc_integration_key)
    site_code = normalize_site(site)
    start, end = month_window(date_from, date_to)
    with get_connection() as conn:
        cursor = conn.cursor()
        stock = _fetch_stock(cursor, start, end, site_code)
        kwh = _fetch_kwh(cursor, start, end, site_code)
    rows = attach_kwh_per_connection(stock, kwh)
    return _envelope(
        start,
        end,
        site_code,
        {
            "rows": rows,
            "totals": month_totals(rows, ("customer_commissioned", "kwh")),
        },
    )


@router.get("/collections")
def forecast_collections(
    site: Optional[str] = Query(None, description="3-letter PR/CC site code"),
    date_from: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM inclusive"),
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Monthly billed vs collected in lane currency. No ARPU field."""
    _require_key(x_cc_integration_key)
    site_code = normalize_site(site)
    start, end = month_window(date_from, date_to)
    with get_connection() as conn:
        cursor = conn.cursor()
        prepaid = _fetch_prepaid(cursor, start, end, site_code)
        invoiced = _fetch_invoiced(cursor, start, end, site_code)
    rows = merge_collections(prepaid, invoiced)
    return _envelope(
        start,
        end,
        site_code,
        {
            "rows": rows,
            "totals": month_totals(rows, ("billed_local", "collected_local")),
            "arpu": None,
        },
    )
