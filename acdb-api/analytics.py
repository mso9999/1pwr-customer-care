"""
Analytics Explorer — dynamic metric catalog and query engine.

Provides a catalog of pre-defined metrics (customer funnel, financial,
consumption) that share a common filter / group-by engine.  No raw user
SQL — every value is parameterised, and group-by expressions come from a
hardcoded whitelist.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from models import CurrentUser
from middleware import require_employee
from customer_api import get_connection
from country_config import _REGISTRY, ALL_KNOWN_SITES, CountryConfig

logger = logging.getLogger("cc-api.analytics")

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# ---------------------------------------------------------------------------
# Caching (same pattern as stats.py, shorter TTL for interactive use)
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 300


def _get_cached(key: str) -> Optional[Any]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.monotonic() - entry[0]) < CACHE_TTL_SECONDS:
            return entry[1]
    return None


def _set_cached(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Group-by whitelist — only these values are allowed; each maps to a SQL
# expression.  The column *alias* must be ``group_key`` everywhere.
# ---------------------------------------------------------------------------

_GROUP_COL_SQL: Dict[str, str] = {
    "month":   "to_char(date_trunc('month', c.created_at), 'YYYY-MM')",
    "quarter": "to_char(date_trunc('quarter', c.created_at), 'YYYY-\"Q\"Q')",
    "year":    "to_char(date_trunc('year', c.created_at), 'YYYY')",
    "site":    "c.community",
    "customer_type": "COALESCE(NULLIF(UPPER(TRIM(c.customer_type)), ''), 'UNKNOWN')",
    "none":    "'All'",
}

# For financial / consumption metrics the anchor date is a transaction or
# reading month, not the customer registration date.
_GROUP_COL_MONTHLY: Dict[str, str] = {
    "month":   "mt.year_month",
    "quarter": "to_char(date_trunc('quarter', (mt.year_month || '-01')::date), 'YYYY-\"Q\"Q')",
    "year":    "SUBSTRING(mt.year_month, 1, 4)",
    "site":    "c.community",
    "customer_type": "COALESCE(NULLIF(UPPER(TRIM(c.customer_type)), ''), 'UNKNOWN')",
    "none":    "'All'",
}

_GROUP_COL_CONSUMPTION: Dict[str, str] = {
    "month":   "mc.year_month",
    "quarter": "to_char(date_trunc('quarter', (mc.year_month || '-01')::date), 'YYYY-\"Q\"Q')",
    "year":    "SUBSTRING(mc.year_month, 1, 4)",
    "site":    "c.community",
    "customer_type": "COALESCE(NULLIF(UPPER(TRIM(c.customer_type)), ''), 'UNKNOWN')",
    "none":    "'All'",
}

# ---------------------------------------------------------------------------
# Metric catalog
# ---------------------------------------------------------------------------

METRIC_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── Funnel metrics ───────────────────────────────────────────────
    "registered_not_paid": {
        "id": "registered_not_paid",
        "name": "Registered – Not Paid",
        "description": "Customers with no payment recorded (is_payment=true, amount > 0).",
        "category": "funnel",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "Count",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN %(sites)s
              {customer_type_filter}
              AND (
                  c.payment_status_override = 'not_paid'
                  OR (
                      c.payment_status_override IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.account_number = a.account_number
                            AND t.is_payment = true
                            AND t.transaction_amount > 0
                      )
                  )
              )
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
        "snapshot": True,
    },
    "registered_paid_not_connected": {
        "id": "registered_paid_not_connected",
        "name": "Paid – Not Connected",
        "description": "Has at least one payment (is_payment=true) but not yet connected.",
        "category": "funnel",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "Count",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN %(sites)s
              {customer_type_filter}
              AND (
                  c.payment_status_override IN ('paid', 'fully_paid')
                  OR (
                      c.payment_status_override IS NULL
                      AND EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.account_number = a.account_number
                            AND t.is_payment = true
                            AND t.transaction_amount > 0
                      )
                  )
              )
              AND c.date_service_connected IS NULL
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "registered_partially_paid_not_connected": {
        "id": "registered_partially_paid_not_connected",
        "name": "Partially Paid – Not Connected",
        "description": "Has payments totalling less than connection+readyboard fees; not connected.",
        "category": "funnel",
        "default_viz": "bar",
        "group_by_options": ["site", "customer_type", "none"],
        "column_label": "Count",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN %(sites)s
              {customer_type_filter}
              AND (
                  c.payment_status_override IN ('paid', 'fully_paid')
                  OR (
                      c.payment_status_override IS NULL
                      AND EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.account_number = a.account_number
                            AND t.is_payment = true
                            AND t.transaction_amount > 0
                      )
                  )
              )
              AND (
                  c.payment_status_override = 'paid'
                  OR (
                      c.payment_status_override IS NULL
                      AND (SELECT COALESCE(SUM(t.transaction_amount), 0)
                           FROM transactions t
                           WHERE t.account_number = a.account_number
                             AND t.is_payment = true) < %(fee_threshold)s
                  )
              )
              AND c.date_service_connected IS NULL
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "registered_partially_paid_connected": {
        "id": "registered_partially_paid_connected",
        "name": "Partially Paid – Connected",
        "description": "Has payments totalling less than connection+readyboard fees; connected.",
        "category": "funnel",
        "default_viz": "bar",
        "group_by_options": ["site", "customer_type", "none"],
        "column_label": "Count",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN %(sites)s
              {customer_type_filter}
              AND (
                  c.payment_status_override IN ('paid', 'fully_paid')
                  OR (
                      c.payment_status_override IS NULL
                      AND EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.account_number = a.account_number
                            AND t.is_payment = true
                            AND t.transaction_amount > 0
                      )
                  )
              )
              AND (
                  c.payment_status_override = 'paid'
                  OR (
                      c.payment_status_override IS NULL
                      AND (SELECT COALESCE(SUM(t.transaction_amount), 0)
                           FROM transactions t
                           WHERE t.account_number = a.account_number
                             AND t.is_payment = true) < %(fee_threshold)s
                  )
              )
              AND c.date_service_connected IS NOT NULL
              AND c.date_service_terminated IS NULL
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "registered_fully_paid_connected": {
        "id": "registered_fully_paid_connected",
        "name": "Fully Paid – Connected",
        "description": "Has payments totalling at least connection+readyboard fees; connected and active.",
        "category": "funnel",
        "default_viz": "bar",
        "group_by_options": ["site", "customer_type", "none"],
        "column_label": "Count",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN %(sites)s
              {customer_type_filter}
              AND (
                  c.payment_status_override IN ('paid', 'fully_paid')
                  OR (
                      c.payment_status_override IS NULL
                      AND EXISTS (
                          SELECT 1 FROM transactions t
                          WHERE t.account_number = a.account_number
                            AND t.is_payment = true
                            AND t.transaction_amount > 0
                      )
                  )
              )
              AND (
                  c.payment_status_override = 'fully_paid'
                  OR (
                      c.payment_status_override IS NULL
                      AND (SELECT COALESCE(SUM(t.transaction_amount), 0)
                           FROM transactions t
                           WHERE t.account_number = a.account_number
                             AND t.is_payment = true) >= %(fee_threshold)s
                  )
              )
              AND c.date_service_connected IS NOT NULL
              AND c.date_service_terminated IS NULL
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },

    # ── Customer metrics ────────────────────────────────────────────
    "total_customers": {
        "id": "total_customers",
        "name": "Total Registered Customers",
        "description": "All customers ever registered (not soft-deleted).",
        "category": "customer",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "Customers",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            WHERE c.community IN %(sites)s
              {customer_type_filter}
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
        "snapshot": True,
    },
    "active_customers": {
        "id": "active_customers",
        "name": "Active Customers",
        "description": "Customers with date_service_connected set and not terminated.",
        "category": "customer",
        "default_viz": "bar",
        "group_by_options": ["site", "customer_type", "none"],
        "column_label": "Customers",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            WHERE c.community IN %(sites)s
              AND c.date_service_connected IS NOT NULL
              AND c.date_service_terminated IS NULL
              {customer_type_filter}
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "commissioned_customers": {
        "id": "commissioned_customers",
        "name": "Commissioned Customers",
        "description": "Customers who completed the commissioning wizard.",
        "category": "customer",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "Customers",
        "value_format": "integer",
        "group_source": "customer",
        "sql_template": """
            SELECT {group_col} AS group_key, COUNT(*) AS value
            FROM customers c
            WHERE c.community IN %(sites)s
              AND c.customer_commissioned = TRUE
              {customer_type_filter}
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },

    # ── Financial metrics ───────────────────────────────────────────
    "arpu": {
        "id": "arpu",
        "name": "ARPU (Average Revenue Per User)",
        "description": "Total revenue divided by active customer count per period.",
        "category": "financial",
        "default_viz": "line",
        "group_by_options": ["month", "quarter", "year", "site", "none"],
        "column_label": "ARPU",
        "value_format": "currency",
        "group_source": "monthly",
        "sql_template": """
            SELECT {group_col} AS group_key,
                   ROUND(COALESCE(SUM(mt.amount_lsl), 0), 2) AS total_revenue,
                   COUNT(DISTINCT a.account_number) AS active_accounts,
                   CASE WHEN COUNT(DISTINCT a.account_number) > 0
                     THEN ROUND(SUM(mt.amount_lsl) / COUNT(DISTINCT a.account_number), 2)
                     ELSE 0 END AS value
            FROM monthly_transactions mt
            JOIN accounts a ON a.account_number = mt.account_number
            JOIN customers c ON c.id = a.customer_id
            WHERE mt.year_month >= %(date_from_month)s
              AND mt.year_month <= %(date_to_month)s
              AND c.community IN %(sites)s
              {customer_type_filter}
              AND mt.amount_lsl > 0
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "total_revenue": {
        "id": "total_revenue",
        "name": "Total Revenue",
        "description": "Sum of all payment amounts in local currency.",
        "category": "financial",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "Revenue",
        "value_format": "currency",
        "group_source": "monthly",
        "sql_template": """
            SELECT {group_col} AS group_key,
                   ROUND(COALESCE(SUM(mt.amount_lsl), 0), 2) AS value
            FROM monthly_transactions mt
            JOIN accounts a ON a.account_number = mt.account_number
            JOIN customers c ON c.id = a.customer_id
            WHERE mt.year_month >= %(date_from_month)s
              AND mt.year_month <= %(date_to_month)s
              AND c.community IN %(sites)s
              {customer_type_filter}
              AND mt.amount_lsl > 0
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },

    # ── Consumption metrics ─────────────────────────────────────────
    "total_consumption_kwh": {
        "id": "total_consumption_kwh",
        "name": "Total Consumption (kWh)",
        "description": "Total kilowatt-hours consumed from meter readings.",
        "category": "consumption",
        "default_viz": "bar",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "kWh",
        "value_format": "decimal2",
        "group_source": "consumption",
        "sql_template": """
            SELECT {group_col} AS group_key,
                   ROUND(COALESCE(SUM(mc.kwh), 0), 2) AS value
            FROM monthly_consumption mc
            JOIN accounts a ON a.account_number = mc.account_number
            JOIN customers c ON c.id = a.customer_id
            WHERE mc.year_month >= %(date_from_month)s
              AND mc.year_month <= %(date_to_month)s
              AND c.community IN %(sites)s
              {customer_type_filter}
              AND mc.kwh > 0
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
    "avg_consumption_kwh_day": {
        "id": "avg_consumption_kwh_day",
        "name": "Avg Daily Consumption (kWh/day)",
        "description": "Average kWh consumed per active customer per day in each period.",
        "category": "consumption",
        "default_viz": "line",
        "group_by_options": ["month", "quarter", "year", "site", "customer_type", "none"],
        "column_label": "kWh/day/cust",
        "value_format": "decimal2",
        "group_source": "consumption",
        "sql_template": """
            SELECT {group_col} AS group_key,
                   ROUND(COALESCE(SUM(mc.kwh), 0), 2) AS total_kwh,
                   COUNT(DISTINCT a.account_number) AS cust_count,
                   CASE WHEN COUNT(DISTINCT a.account_number) > 0
                     THEN ROUND(SUM(mc.kwh) / (COUNT(DISTINCT a.account_number) * 30.4375), 4)
                     ELSE 0 END AS value
            FROM monthly_consumption mc
            JOIN accounts a ON a.account_number = mc.account_number
            JOIN customers c ON c.id = a.customer_id
            WHERE mc.year_month >= %(date_from_month)s
              AND mc.year_month <= %(date_to_month)s
              AND c.community IN %(sites)s
              {customer_type_filter}
              AND mc.kwh > 0
            GROUP BY {group_col}
            ORDER BY {group_col}
        """,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CUSTOMER_TYPES = [
    "HH1", "HH2", "HH3", "SME", "CHU", "SCH", "HC",
    "GOV", "COM", "IND", "SCP", "REL", "AGR", "CLI", "PUE",
    "HCF", "OTH",
]


def _resolve_sites(country: Optional[str], sites: Optional[List[str]]) -> List[str]:
    """Return a validated list of site codes from country + optional site filter."""
    if sites:
        valid = [s.upper().strip() for s in sites if s.upper().strip() in ALL_KNOWN_SITES]
        if not valid:
            raise HTTPException(400, f"No valid sites in: {sites}")
        return valid

    if country:
        cc = country.upper().strip()
        cfg = _REGISTRY.get(cc)
        if not cfg:
            raise HTTPException(400, f"Unknown country: {country}")
        return sorted(cfg.site_abbrev.keys())

    # All known sites across all countries
    return sorted(ALL_KNOWN_SITES)


def _build_query(metric_id: str, filters: Dict[str, Any], group_by: str) -> Tuple[str, tuple]:
    """Build a parameterised SQL query for a metric.

    Returns ``(sql, params_tuple)`` suitable for ``cursor.execute()``.
    """
    metric = METRIC_CATALOG[metric_id]

    # Validate group_by
    if group_by not in metric["group_by_options"]:
        raise HTTPException(
            400, f"group_by '{group_by}' not allowed for metric '{metric_id}'"
        )

    # Pick the right group-col dictionary
    gs = metric.get("group_source", "customer")
    if gs == "monthly":
        gcol_map = _GROUP_COL_MONTHLY
    elif gs == "consumption":
        gcol_map = _GROUP_COL_CONSUMPTION
    else:
        gcol_map = _GROUP_COL_SQL

    group_col = gcol_map[group_by]

    # --- customer type filter ---
    ctypes = filters.get("customer_types", [])
    ct_clause = ""
    ct_params: list = []
    if ctypes:
        # Expand "HH" → HH1, HH2, HH3 (same as om_report._matches_customer_type)
        expanded: List[str] = []
        for ct in ctypes:
            ct_upper = ct.upper().strip()
            if ct_upper == "HH":
                expanded.extend(["HH1", "HH2", "HH3"])
            else:
                expanded.append(ct_upper)
        if expanded:
            ph = ",".join(["%s"] * len(expanded))
            ct_clause = f"AND UPPER(TRIM(c.customer_type)) IN ({ph})"
            ct_params = expanded

    # --- date range ---
    # Snapshot metrics (funnel, customer counts) don't use date filters in their
    # templates.  Financial / consumption metrics default to trailing 12 months
    # when no explicit date range is given.
    today = datetime.now(timezone.utc)
    date_from = (filters.get("date_from") or "").strip()
    date_to = (filters.get("date_to") or "").strip()
    has_date_placeholders = (
        "%(date_from)s" in metric["sql_template"]
        or "%(date_from_month)s" in metric["sql_template"]
    )

    if has_date_placeholders and not date_from:
        # Default to trailing 12 months for financial/consumption metrics
        date_to = today.strftime("%Y-%m-%d")
        # 12 months ago, first of month
        if today.month == 1:
            date_from = f"{today.year - 1}-12-01"
        else:
            date_from = f"{today.year}-{today.month - 1:02d}-01"

    date_from_month = date_from[:7] if date_from else "2020-01"
    date_to_month = date_to[:7] if date_to else today.strftime("%Y-%m")

    # --- sites (pre-resolved by caller) ---
    site_list = filters["_resolved_sites"]
    site_placeholders = ",".join(["%s"] * len(site_list))

    # --- assemble SQL ---
    sql = metric["sql_template"]
    sql = sql.replace("%(sites)s", f"({site_placeholders})")

    # Date placeholders — templates use either %(date_from)s/%(date_to)s (timestamps)
    # or %(date_from_month)s/%(date_to_month)s (YYYY-MM strings for monthly tables).
    # We replace whichever the template uses.
    sql = sql.replace("%(date_from_month)s", "%s")
    sql = sql.replace("%(date_to_month)s", "%s")
    sql = sql.replace("%(date_from)s", "%s")
    sql = sql.replace("%(date_to)s", "%s")

    # --- fee threshold (for funnel partially/fully paid distinction) ---
    has_fee_threshold = "%(fee_threshold)s" in metric["sql_template"]
    fee_threshold = 0.0
    if has_fee_threshold:
        country_code = (filters.get("country") or "").upper().strip()
        cfg = _REGISTRY.get(country_code)
        if cfg:
            fee_threshold = cfg.default_connection_fee + cfg.default_readyboard_fee
        # Fallback: if country unknown, use a generous threshold (any payment > 0 counts as fully paid)
        if fee_threshold <= 0:
            fee_threshold = 1.0

    sql = sql.replace("{group_col}", group_col)
    sql = sql.replace("{customer_type_filter}", ct_clause)
    sql = sql.replace("%(fee_threshold)s", "%s")

    # --- build params tuple ---
    # Order: sites first, then fee_threshold (if used), then date params,
    # then customer types.
    params: list = list(site_list)

    if has_fee_threshold:
        params.append(fee_threshold)

    if has_date_placeholders:
        date_from_val = date_from or "2020-01-01"
        date_to_val = date_to or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if "%(date_from_month)s" in metric["sql_template"]:
            params.append(date_from_month)
            params.append(date_to_month)
        else:
            params.append(date_from_val)
            params.append(date_to_val)

    params.extend(ct_params)

    logger.debug("metric=%s group_by=%s sql=%s params=%s", metric_id, group_by, sql[:120], params)
    return sql, tuple(params)


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AnalyticsQueryRequest(BaseModel):
    metrics: List[str]
    filters: Dict[str, Any] = {}
    group_by: str = "none"
    time_series: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/metrics")
def list_metrics(user: CurrentUser = Depends(require_employee)):
    """Return the catalog of available metrics."""
    result: List[Dict[str, Any]] = []
    for mid, meta in METRIC_CATALOG.items():
        result.append({
            "id": mid,
            "name": meta["name"],
            "description": meta.get("description", ""),
            "category": meta.get("category", "uncategorized"),
            "default_viz": meta.get("default_viz", "bar"),
            "group_by_options": meta.get("group_by_options", []),
            "column_label": meta.get("column_label", "Value"),
            "value_format": meta.get("value_format", "decimal2"),
        })
    result.sort(key=lambda m: (
        {"funnel": 0, "customer": 1, "financial": 2, "consumption": 3}.get(m["category"], 9),
        m["name"],
    ))
    categories = sorted(set(m["category"] for m in result),
                        key=lambda c: {"funnel": 0, "customer": 1, "financial": 2, "consumption": 3}.get(c, 9))
    return {
        "metrics": result,
        "categories": categories,
        "customer_types": _CUSTOMER_TYPES,
        "group_by_options": [
            {"value": "month", "label": "Month"},
            {"value": "quarter", "label": "Quarter"},
            {"value": "year", "label": "Year"},
            {"value": "site", "label": "Site"},
            {"value": "customer_type", "label": "Customer Type"},
            {"value": "none", "label": "Total (no grouping)"},
        ],
    }


@router.post("/query")
def run_analytics_query(
    req: AnalyticsQueryRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Execute one or more metrics with shared filters.

    Request body example::

        {
          "metrics": ["active_customers", "arpu"],
          "filters": {
            "country": "LS",
            "sites": ["MAK"],
            "customer_types": ["HH1", "SME"],
            "date_from": "2025-01-01",
            "date_to": "2026-05-01"
          },
          "group_by": "site",
          "time_series": true
        }
    """
    if not req.metrics:
        raise HTTPException(400, "At least one metric ID is required.")

    for mid in req.metrics:
        if mid not in METRIC_CATALOG:
            raise HTTPException(400, f"Unknown metric: {mid}")

    filters = req.filters or {}
    group_by = req.group_by or "none"

    # Resolve sites
    try:
        filters["_resolved_sites"] = _resolve_sites(
            filters.get("country"), filters.get("sites")
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid country or site filter.")

    # Cache key
    cache_key = hashlib.sha256(
        json.dumps({
            "m": sorted(req.metrics),
            "f": {k: v for k, v in filters.items() if not k.startswith("_")},
            "g": group_by,
            "t": req.time_series,
        }, sort_keys=True).encode()
    ).hexdigest()

    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # Execute each metric
    results: Dict[str, Dict[str, Any]] = {}
    series_data: List[Dict[str, Any]] = []

    with get_connection() as conn:
        for mid in req.metrics:
            sql, params = _build_query(mid, filters, group_by)
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                rows = cur.fetchall()
                metric_data = [_row_to_dict(cur, r) for r in rows]
            except Exception as exc:
                logger.exception("Metric %s query failed", mid)
                metric_data = []
                # Continue with other metrics — don't fail the whole request

            metric = METRIC_CATALOG[mid]
            results[mid] = {
                "data": metric_data,
                "column_label": metric["column_label"],
                "value_format": metric.get("value_format", "decimal2"),
                "name": metric["name"],
            }

            if req.time_series and metric_data:
                series_data.append({
                    "metric_id": mid,
                    "metric_name": metric["name"],
                    "points": [
                        {"group": r.get("group_key", str(i)), "value": r.get("value", 0)}
                        for i, r in enumerate(metric_data)
                    ],
                })

    response = {
        "metrics": results,
        "series": series_data if req.time_series else None,
        "filters_applied": {
            "country": filters.get("country"),
            "sites": filters.get("_resolved_sites"),
            "customer_types": filters.get("customer_types"),
            "date_from": filters.get("date_from", "2020-01-01"),
            "date_to": filters.get("date_to", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "group_by": group_by,
        },
    }

    _set_cached(cache_key, response)
    return response
