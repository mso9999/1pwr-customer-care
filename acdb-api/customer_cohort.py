"""
Customer Cohort — list individual customers filtered by payment/connection
status, site and type.

This is the drill-down complement to the aggregate Analytics Explorer
(@analytics.py).  Status definitions use the SAME logic as the funnel
metrics in `METRIC_CATALOG`, derived from `payment_status_override` and
the sum of `transactions.transaction_amount` where `is_payment = true`,
compared to the country's connection + readyboard fee threshold.

Endpoint: POST /api/customer-cohort/query

No raw SQL from the request body — every filter is parameterised, sort
column comes from a whitelist, and pagination caps page_size at 500.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from country_config import _REGISTRY, ALL_KNOWN_SITES
from middleware import require_employee
from models import CurrentUser

logger = logging.getLogger("cc-api.customer_cohort")

router = APIRouter(prefix="/api/customer-cohort", tags=["customer-cohort"])


# ---------------------------------------------------------------------------
# Status taxonomy — must stay aligned with analytics.METRIC_CATALOG funnel
# definitions.  See @analytics.py:92-290.
# ---------------------------------------------------------------------------

COHORT_STATUSES: List[str] = [
    "not_paid",                       # total_paid <= 0 (or override='not_paid')
    "partially_paid_not_connected",   # 0 < total_paid < threshold, not connected
    "partially_paid_connected",       # 0 < total_paid < threshold, connected
    "fully_paid_not_connected",       # total_paid >= threshold, not connected (edge case)
    "fully_paid_connected",           # total_paid >= threshold, connected
    "terminated",                     # date_service_terminated IS NOT NULL
]


# Whitelisted sort columns to prevent injection.  Map UI column id → SQL.
# NOTE: these names refer to columns of the ``cohort`` CTE (see _build_query),
# not the underlying tables.  In particular ``c.community`` is aliased as
# ``site`` inside the CTE, so the sort key must be ``site`` — referencing
# ``community`` here triggers a Postgres UndefinedColumn error at runtime.
_SORT_COLS: Dict[str, str] = {
    "site":               "site",
    "account_number":     "account_number",
    "name":               "last_name, first_name",
    "phone":              "phone",
    "customer_type":      "customer_type",
    "total_paid":         "total_paid",
    "date_connected":     "date_service_connected",
    "cohort_status":      "cohort_status",
}


# Customer types accepted in filter (same list as analytics).
_CUSTOMER_TYPES = [
    "HH1", "HH2", "HH3", "SME", "CHU", "SCH", "HC",
    "GOV", "COM", "IND", "SCP", "REL", "AGR", "CLI", "PUE",
    "HCF", "OTH",
]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CohortFilters(BaseModel):
    country: Optional[str] = None
    sites: Optional[List[str]] = None
    customer_types: Optional[List[str]] = None
    statuses: Optional[List[str]] = None
    search: Optional[str] = None  # name / phone / account fragment


class CohortQuery(BaseModel):
    filters: CohortFilters = Field(default_factory=CohortFilters)
    sort_by: str = "site"
    sort_dir: str = "asc"          # asc | desc
    page: int = 1
    page_size: int = 50            # capped server-side at 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_sites(country: Optional[str], sites: Optional[List[str]]) -> List[str]:
    """Validated site list, same semantics as analytics._resolve_sites."""
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
    return sorted(ALL_KNOWN_SITES)


def _resolve_fee_threshold(country: Optional[str]) -> float:
    """Connection + readyboard fee threshold for full-payment classification."""
    cc = (country or "").upper().strip()
    cfg = _REGISTRY.get(cc)
    if cfg:
        return float(cfg.default_connection_fee + cfg.default_readyboard_fee)
    return 1.0


def _expand_customer_types(types: Optional[List[str]]) -> List[str]:
    """Same HH → HH1/2/3 expansion as analytics.py."""
    if not types:
        return []
    out: List[str] = []
    for t in types:
        u = t.upper().strip()
        if u == "HH":
            out.extend(["HH1", "HH2", "HH3"])
        else:
            out.append(u)
    return out


def _build_query(q: CohortQuery, *, count_only: bool) -> Tuple[str, list]:
    """Return ``(sql, params)`` for either the page select or its count."""
    f = q.filters
    sites = _resolve_sites(f.country, f.sites)
    fee_threshold = _resolve_fee_threshold(f.country)
    ct_expanded = _expand_customer_types(f.customer_types)
    statuses = [s for s in (f.statuses or []) if s in COHORT_STATUSES]

    site_placeholders = ",".join(["%s"] * len(sites))

    ct_clause = ""
    ct_params: list = []
    if ct_expanded:
        ph = ",".join(["%s"] * len(ct_expanded))
        ct_clause = f"AND UPPER(TRIM(c.customer_type)) IN ({ph})"
        ct_params = list(ct_expanded)

    search_clause = ""
    search_params: list = []
    if f.search and f.search.strip():
        s = f"%{f.search.strip()}%"
        search_clause = (
            "AND (c.first_name ILIKE %s OR c.last_name ILIKE %s "
            "OR c.phone ILIKE %s OR a.account_number ILIKE %s)"
        )
        search_params = [s, s, s, s]

    status_clause = ""
    if statuses:
        ph = ",".join(["%s"] * len(statuses))
        status_clause = f"AND cohort_status IN ({ph})"

    # Build params in *exact SQL placeholder order*:
    #   1-2. fee_threshold (two %s inside the CTE's CASE expression)
    #   3+.  sites      (WHERE c.community IN (...))
    #   ...  ct_clause  (AND UPPER(TRIM(c.customer_type)) IN (...))
    #   ...  search     (AND ... ILIKE ...)
    #   ...  status_clause (outer WHERE cohort_status IN (...))
    #   last LIMIT, OFFSET — appended by the page-select branch below.
    params: list = [fee_threshold, fee_threshold]
    params.extend(sites)
    params.extend(ct_params)
    params.extend(search_params)

    base_cte = f"""
        WITH paid_totals AS (
            SELECT a.customer_id,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                     THEN t.transaction_amount ELSE 0 END), 0) AS total_paid
            FROM accounts a
            LEFT JOIN transactions t ON t.account_number = a.account_number
            GROUP BY a.customer_id
        ),
        cohort AS (
            SELECT
                c.id AS customer_id,
                c.first_name,
                c.last_name,
                c.phone,
                c.community AS site,
                COALESCE(NULLIF(UPPER(TRIM(c.customer_type)), ''), 'UNKNOWN') AS customer_type,
                c.date_service_connected,
                c.date_service_terminated,
                c.payment_status_override,
                a.account_number,
                COALESCE(pt.total_paid, 0)::numeric AS total_paid,
                CASE
                    WHEN c.date_service_terminated IS NOT NULL THEN 'terminated'
                    WHEN COALESCE(pt.total_paid, 0) <= 0
                         OR c.payment_status_override = 'not_paid'
                        THEN 'not_paid'
                    WHEN (c.payment_status_override = 'fully_paid'
                          OR COALESCE(pt.total_paid, 0) >= %s)
                         AND c.date_service_connected IS NOT NULL
                        THEN 'fully_paid_connected'
                    WHEN (c.payment_status_override = 'fully_paid'
                          OR COALESCE(pt.total_paid, 0) >= %s)
                         AND c.date_service_connected IS NULL
                        THEN 'fully_paid_not_connected'
                    WHEN c.date_service_connected IS NOT NULL
                        THEN 'partially_paid_connected'
                    ELSE 'partially_paid_not_connected'
                END AS cohort_status
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            LEFT JOIN paid_totals pt ON pt.customer_id = c.id
            WHERE c.community IN ({site_placeholders})
              {ct_clause}
              {search_clause}
        )
    """

    if count_only:
        sql = base_cte + f"SELECT COUNT(*) FROM cohort WHERE 1=1 {status_clause}"
        if statuses:
            params.extend(statuses)
        return sql, params

    # Sort
    sort_col = _SORT_COLS.get(q.sort_by, _SORT_COLS["site"])
    sort_dir = "DESC" if (q.sort_dir or "").lower() == "desc" else "ASC"
    page = max(1, int(q.page or 1))
    page_size = max(1, min(500, int(q.page_size or 50)))
    offset = (page - 1) * page_size

    sql = base_cte + f"""
        SELECT customer_id, first_name, last_name, phone, site, customer_type,
               date_service_connected, date_service_terminated,
               payment_status_override, account_number, total_paid, cohort_status
        FROM cohort
        WHERE 1=1 {status_clause}
        ORDER BY {sort_col} {sort_dir} NULLS LAST, customer_id ASC
        LIMIT %s OFFSET %s
    """
    if statuses:
        params.extend(statuses)
    params.append(page_size)
    params.append(offset)
    return sql, params


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/statuses")
def list_statuses(user: CurrentUser = Depends(require_employee)):
    """Return the canonical list of cohort_status values."""
    return {"statuses": COHORT_STATUSES, "customer_types": _CUSTOMER_TYPES,
            "sort_columns": list(_SORT_COLS.keys())}


@router.post("/query")
def query_cohort(
    q: CohortQuery,
    user: CurrentUser = Depends(require_employee),
):
    """Return a paginated list of customers matching the filter set.

    Response::

        {
          "rows": [ { customer_id, first_name, last_name, phone, site, ... } ],
          "total": <int>,
          "page": <int>,
          "page_size": <int>,
          "filters_applied": { ... resolved ... }
        }
    """
    if q.sort_by not in _SORT_COLS:
        raise HTTPException(400, f"sort_by must be one of {list(_SORT_COLS.keys())}")

    sql, params = _build_query(q, count_only=False)
    count_sql, count_params = _build_query(q, count_only=True)

    # Lazy import keeps the unit test suite (which builds queries but never
    # executes them) free of the full DB / FastAPI app stack.
    from customer_api import get_connection  # noqa: WPS433

    rows: List[Dict[str, Any]] = []
    total = 0
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(count_sql, tuple(count_params))
            total = int(cur.fetchone()[0] or 0)

            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                d = _row_to_dict(cur, r)
                # Format dates / numerics for JSON
                if d.get("date_service_connected"):
                    d["date_service_connected"] = d["date_service_connected"].isoformat()
                if d.get("date_service_terminated"):
                    d["date_service_terminated"] = d["date_service_terminated"].isoformat()
                if d.get("total_paid") is not None:
                    d["total_paid"] = float(d["total_paid"])
                rows.append(d)
        except Exception:
            logger.exception("Cohort query failed")
            raise HTTPException(500, "Cohort query failed")

    sites_resolved = _resolve_sites(q.filters.country, q.filters.sites)
    return {
        "rows": rows,
        "total": total,
        "page": q.page,
        "page_size": q.page_size,
        "filters_applied": {
            "country": q.filters.country,
            "sites": sites_resolved,
            "customer_types": q.filters.customer_types,
            "statuses": q.filters.statuses,
            "search": q.filters.search,
            "sort_by": q.sort_by,
            "sort_dir": q.sort_dir,
            "fee_threshold": _resolve_fee_threshold(q.filters.country),
        },
    }
