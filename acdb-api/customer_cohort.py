"""
Customer Cohort — list individual customers filtered by payment/connection
status, site and type.

This is the drill-down complement to the aggregate Analytics Explorer
(@analytics.py).  Status definitions use the SAME logic as the funnel
metrics in `METRIC_CATALOG`, derived from `payment_status_override` and
the sum of `transactions.transaction_amount` where `is_payment = true`,
compared to the country's connection + readyboard fee threshold.

Row payment breakdown (same customer / all time as ``total_paid``):
``payments_connection_fee`` / ``payments_readyboard_fee`` sum explicit
``payment_category`` fee rows. ``payments_fee_repayment_via_electricity``
sums ``fee_repayment_portion`` on ``payment_category = 'electricity'`` rows
(allocation is connection debt first then readyboard in the allocator; the
per-row split is not stored). ``payments_electricity`` is the kWh-purchase
slice (``electricity_portion`` with legacy fallback). Advance and financing
repayments remain in ``total_paid`` but are not shown in these four buckets.

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
    "partially_paid_connected",       # 0 < total_paid < threshold, connected, metered
    "partially_paid_not_metered",     # 0 < total_paid < threshold, connected, no meter
    "fully_paid_not_connected",       # total_paid >= threshold, not connected (edge case)
    "fully_paid_connected",           # total_paid >= threshold, connected, metered
    "fully_paid_not_metered",         # total_paid >= threshold, connected, no meter
    "terminated",                     # date_service_terminated IS NOT NULL
]

# Independent customer filters (applied before cohort_status is computed).
CONNECTION_STATUSES: List[str] = [
    "not_connected",   # no date_service_connected, not terminated
    "connected",       # has date_service_connected, not terminated
    "terminated",      # date_service_terminated set
]

CONTRACT_STATUSES: List[str] = [
    "signed",
    "not_signed",
]

QUERY_STATEMENT_TIMEOUT_MS = 90_000


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
    statuses: Optional[List[str]] = None  # payment funnel (cohort_status)
    connection_statuses: Optional[List[str]] = None
    contract_statuses: Optional[List[str]] = None
    search: Optional[str] = None  # name / phone / account fragment


class CohortQuery(BaseModel):
    filters: CohortFilters = Field(default_factory=CohortFilters)
    sort_by: str = "site"
    sort_dir: str = "asc"          # asc | desc
    page: int = 1
    page_size: int = 50            # capped server-side at 500


class CohortExportRequest(BaseModel):
    """Export all rows matching filters (not only the current UI page)."""

    filters: CohortFilters = Field(default_factory=CohortFilters)
    sort_by: str = "site"
    sort_dir: str = "asc"
    columns: List[str] = Field(
        default_factory=list,
        description="Export column ids; account_number and name are always included.",
    )


EXPORT_MAX_ROWS = 50_000

# Whitelisted export columns → SQL select expressions on the ``cohort`` CTE.
# ``mandatory`` columns are always appended even if omitted from the request.
_EXPORT_SELECT: Dict[str, Dict[str, Any]] = {
    "account_number": {"mandatory": True, "label": "Account number", "sql": "account_number"},
    "name": {
        "mandatory": True,
        "label": "Name",
        "sql": (
            "TRIM(CONCAT_WS(' ', NULLIF(TRIM(first_name), ''), "
            "NULLIF(TRIM(middle_name), ''), NULLIF(TRIM(last_name), ''))) AS name"
        ),
    },
    "site": {"label": "Site", "sql": "site"},
    "phone": {"label": "Phone", "sql": "phone"},
    "customer_type": {"label": "Customer type", "sql": "customer_type"},
    "cohort_status": {"label": "Payment status", "sql": "cohort_status"},
    "payments_connection_fee": {"label": "Connection fee paid", "sql": "payments_connection_fee"},
    "payments_readyboard_fee": {"label": "Readyboard fee paid", "sql": "payments_readyboard_fee"},
    "payments_fee_repayment_via_electricity": {
        "label": "Fee repayment via electricity",
        "sql": "payments_fee_repayment_via_electricity",
    },
    "payments_electricity": {"label": "Electricity paid", "sql": "payments_electricity"},
    "total_paid": {"label": "Total paid", "sql": "total_paid"},
    "date_service_connected": {"label": "Date connected", "sql": "date_service_connected"},
    "date_service_terminated": {"label": "Date terminated", "sql": "date_service_terminated"},
    "payment_status_override": {"label": "Payment status override", "sql": "payment_status_override"},
    "customer_id": {"label": "Customer ID (internal)", "sql": "customer_id"},
    "first_name": {"label": "First name", "sql": "first_name"},
    "last_name": {"label": "Last name", "sql": "last_name"},
    "middle_name": {"label": "Middle name", "sql": "middle_name"},
    "gender": {"label": "Gender", "sql": "gender"},
    "gps_lat": {"label": "GPS latitude", "sql": "gps_lat"},
    "gps_lon": {"label": "GPS longitude", "sql": "gps_lon"},
    "plot_number": {"label": "Plot number", "sql": "plot_number"},
    "national_id": {"label": "National ID", "sql": "national_id"},
    "cell_phone_1": {"label": "Cell phone 1", "sql": "cell_phone_1"},
    "cell_phone_2": {"label": "Cell phone 2", "sql": "cell_phone_2"},
    "email": {"label": "Email", "sql": "email"},
    "customer_id_legacy": {"label": "Legacy customer ID", "sql": "customer_id_legacy"},
    "survey_id": {"label": "Survey ID", "sql": "survey_id"},
    "customer_commissioned": {"label": "Commissioned", "sql": "customer_commissioned"},
    "customer_commissioned_date": {
        "label": "Commissioned date",
        "sql": "customer_commissioned_date",
    },
    "contract_signed": {"label": "Contract signed", "sql": "contract_signed"},
    "contract_signed_date": {"label": "Contract signed date", "sql": "contract_signed_date"},
}

# Default optional columns matching the on-screen table (excluding mandatory).
DEFAULT_EXPORT_COLUMNS: List[str] = [
    "site",
    "phone",
    "customer_type",
    "cohort_status",
    "payments_connection_fee",
    "payments_readyboard_fee",
    "payments_fee_repayment_via_electricity",
    "payments_electricity",
    "total_paid",
    "date_service_connected",
]


def _export_column_catalog() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for col_id, spec in _EXPORT_SELECT.items():
        out.append({
            "id": col_id,
            "label": spec["label"],
            "mandatory": bool(spec.get("mandatory")),
        })
    return out


def _resolve_export_columns(requested: Optional[List[str]]) -> List[str]:
    """Return ordered, de-duplicated export column ids (mandatory first)."""
    ordered: List[str] = []
    seen: set = set()
    for col_id, spec in _EXPORT_SELECT.items():
        if spec.get("mandatory"):
            if col_id not in seen:
                ordered.append(col_id)
                seen.add(col_id)
    for raw in requested or []:
        col_id = (raw or "").strip()
        if col_id in _EXPORT_SELECT and col_id not in seen:
            ordered.append(col_id)
            seen.add(col_id)
    return ordered


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


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s)",
        (table_name, column_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _not_metered_predicate(cursor) -> str:
    """True when connected but no meter installed (column or meters table)."""
    if cursor is not None and _column_exists(cursor, "customers", "meter_installed"):
        return (
            "c.date_service_connected IS NOT NULL "
            "AND NOT COALESCE(c.meter_installed, false)"
        )
    return """
            c.date_service_connected IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM meters m
                INNER JOIN accounts a_nm ON a_nm.account_number = m.account_number
                WHERE a_nm.customer_id = c.id
                  AND LOWER(COALESCE(m.status, 'active')) = 'active'
            )""".strip()


def _cohort_override_expr(cursor) -> str:
    if cursor is not None and not _column_exists(cursor, "customers", "cohort_status_override"):
        return "NULL::text"
    return "c.cohort_status_override"


def _cohort_status_case_sql(cursor) -> str:
    """CASE expression for ``cohort_status`` (manual override wins when set)."""
    override = _cohort_override_expr(cursor)
    not_metered = _not_metered_predicate(cursor)
    return f"""
                CASE
                    WHEN {override} IS NOT NULL AND TRIM({override}) <> ''
                        THEN TRIM({override})
                    WHEN c.date_service_terminated IS NOT NULL THEN 'terminated'
                    WHEN COALESCE(pt.total_paid, 0) <= 0
                         OR c.payment_status_override = 'not_paid'
                        THEN 'not_paid'
                    WHEN (c.payment_status_override = 'fully_paid'
                          OR COALESCE(pt.total_paid, 0) >= %s)
                         AND ({not_metered})
                        THEN 'fully_paid_not_metered'
                    WHEN (c.payment_status_override = 'fully_paid'
                          OR COALESCE(pt.total_paid, 0) >= %s)
                         AND c.date_service_connected IS NOT NULL
                        THEN 'fully_paid_connected'
                    WHEN (c.payment_status_override = 'fully_paid'
                          OR COALESCE(pt.total_paid, 0) >= %s)
                         AND c.date_service_connected IS NULL
                        THEN 'fully_paid_not_connected'
                    WHEN ({not_metered})
                        THEN 'partially_paid_not_metered'
                    WHEN c.date_service_connected IS NOT NULL
                        THEN 'partially_paid_connected'
                    ELSE 'partially_paid_not_connected'
                END AS cohort_status"""


def _cohort_core_select(cursor=None) -> str:
    """Projection for paginated list UI (must match outer SELECT and sort keys)."""
    status_case = _cohort_status_case_sql(cursor)
    override_col = _cohort_override_expr(cursor)
    return f"""
                c.id AS customer_id,
                c.first_name,
                c.last_name,
                c.phone,
                c.community AS site,
                COALESCE(NULLIF(UPPER(TRIM(c.customer_type)), ''), 'UNKNOWN') AS customer_type,
                c.date_service_connected,
                c.date_service_terminated,
                c.payment_status_override,
                {override_col} AS cohort_status_override,
                a.account_number,
                COALESCE(pt.total_paid, 0)::numeric AS total_paid,
                COALESCE(pt.payments_connection_fee, 0)::numeric AS payments_connection_fee,
                COALESCE(pt.payments_readyboard_fee, 0)::numeric AS payments_readyboard_fee,
                COALESCE(pt.payments_fee_repayment_via_electricity, 0)::numeric
                    AS payments_fee_repayment_via_electricity,
                COALESCE(pt.payments_electricity, 0)::numeric AS payments_electricity,
                {status_case}"""


# (table, column, sql expression, NULL fallback type) for CSV export only.
_COHORT_EXPORT_EXTRA: List[Tuple[str, str, str, str]] = [
    ("customers", "middle_name", "c.middle_name", "text"),
    ("customers", "gender", "c.gender", "text"),
    ("customers", "gps_lat", "c.gps_lat", "double precision"),
    ("customers", "gps_lon", "c.gps_lon", "double precision"),
    ("customers", "plot_number", "c.plot_number", "text"),
    ("customers", "national_id", "c.national_id", "text"),
    ("customers", "cell_phone_1", "c.cell_phone_1", "text"),
    ("customers", "cell_phone_2", "c.cell_phone_2", "text"),
    ("customers", "email", "c.email", "text"),
    ("customers", "customer_id_legacy", "c.customer_id_legacy", "text"),
    ("customers", "customer_commissioned", "c.customer_commissioned", "boolean"),
    ("customers", "customer_commissioned_date", "c.customer_commissioned_date", "date"),
    ("customers", "contract_signed", "c.contract_signed", "boolean"),
    ("customers", "contract_signed_date", "c.contract_signed_date", "date"),
    ("accounts", "survey_id", "a.survey_id", "text"),
]


def _cohort_extended_select(cursor) -> str:
    """Export cohort projection: core columns plus optional attrs (NULL if missing)."""
    parts = [_cohort_core_select(cursor).strip()]
    for table, col, expr, null_type in _COHORT_EXPORT_EXTRA:
        if _column_exists(cursor, table, col):
            parts.append(expr)
        else:
            parts.append(f"NULL::{null_type} AS {col}")
    return ",\n                ".join(parts)


def _connection_filter_clause(statuses: Optional[List[str]]) -> str:
    """SQL AND-clause on ``customers c`` (empty when unfiltered)."""
    if not statuses:
        return ""
    valid = [s for s in statuses if s in CONNECTION_STATUSES]
    if not valid:
        return ""
    parts: List[str] = []
    for s in valid:
        if s == "not_connected":
            parts.append(
                "(c.date_service_connected IS NULL AND c.date_service_terminated IS NULL)"
            )
        elif s == "connected":
            parts.append(
                "(c.date_service_connected IS NOT NULL AND c.date_service_terminated IS NULL)"
            )
        elif s == "terminated":
            parts.append("(c.date_service_terminated IS NOT NULL)")
    return f" AND ({' OR '.join(parts)})"


def _contract_filter_clause(cursor, statuses: Optional[List[str]]) -> str:
    """SQL AND-clause on ``customers c``; no-op when ``contract_signed`` missing."""
    if not statuses:
        return ""
    valid = [s for s in statuses if s in CONTRACT_STATUSES]
    if not valid:
        return ""
    if cursor is not None and not _column_exists(cursor, "customers", "contract_signed"):
        return ""
    parts: List[str] = []
    for s in valid:
        if s == "signed":
            parts.append("c.contract_signed IS TRUE")
        else:
            parts.append("(c.contract_signed IS NOT TRUE)")
    return f" AND ({' OR '.join(parts)})"


def _txn_ref(cursor, column: str) -> str:
    """Qualified transaction column, or NULL literal if migration not applied."""
    if cursor is None or _column_exists(cursor, "transactions", column):
        return f"t.{column}"
    return "NULL"


def _paid_totals_cte(cursor) -> str:
    """Aggregate payments only for customers in the scoped CTE (site-filtered)."""
    fee_rep = _txn_ref(cursor, "fee_repayment_portion")
    adv = _txn_ref(cursor, "advance_portion")
    fin = _txn_ref(cursor, "financing_portion")
    elec = _txn_ref(cursor, "electricity_portion")
    return f"""
        scoped AS (
            SELECT DISTINCT c.id AS customer_id
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.community IN ({{site_placeholders}})
              {{ct_clause}}
              {{search_clause}}
              {{connection_clause}}
              {{contract_clause}}
        ),
        paid_totals AS (
            SELECT sc.customer_id,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                     THEN t.transaction_amount ELSE 0 END), 0) AS total_paid,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                       AND t.payment_category = 'connection_fee'
                                     THEN t.transaction_amount ELSE 0 END), 0)
                       AS payments_connection_fee,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                       AND t.payment_category = 'readyboard_fee'
                                     THEN t.transaction_amount ELSE 0 END), 0)
                       AS payments_readyboard_fee,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                       AND t.payment_category IN ('electricity', 'uncategorized')
                                     THEN COALESCE({fee_rep}, 0) ELSE 0 END), 0)
                       AS payments_fee_repayment_via_electricity,
                   COALESCE(SUM(CASE WHEN t.is_payment AND t.transaction_amount > 0
                                       AND t.payment_category IN ('electricity', 'uncategorized')
                                     THEN GREATEST(0::numeric,
                                         COALESCE({elec},
                                             t.transaction_amount
                                             - COALESCE({fee_rep}, 0)
                                             - COALESCE({adv}, 0)
                                             - COALESCE({fin}, 0)))
                                     WHEN t.is_payment AND t.transaction_amount > 0
                                       AND t.payment_category IS NULL
                                     THEN t.transaction_amount
                                     ELSE 0 END), 0) AS payments_electricity
            FROM scoped sc
            INNER JOIN accounts a ON a.customer_id = sc.customer_id
            LEFT JOIN transactions t ON t.account_number = a.account_number
            GROUP BY sc.customer_id
        )"""


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


def _build_query(
    q: CohortQuery,
    *,
    count_only: bool,
    cursor=None,
    extended_cohort: bool = False,
    export_columns: Optional[List[str]] = None,
) -> Tuple[str, list]:
    """Return ``(sql, params)`` for count, paginated list, or CSV export."""
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
    #   scoped CTE: sites, customer types, search
    #   cohort CTE: fee_threshold (two %s in CASE cohort_status)
    #   outer SELECT: statuses, LIMIT/OFFSET
    params: list = list(sites)
    params.extend(ct_params)
    params.extend(search_params)
    params.extend([fee_threshold, fee_threshold, fee_threshold])

    if extended_cohort:
        if cursor is None:
            raise ValueError("extended_cohort requires a database cursor")
        cohort_body = _cohort_extended_select(cursor)
    else:
        cohort_body = _cohort_core_select(cursor).strip()

    connection_clause = _connection_filter_clause(f.connection_statuses)
    contract_clause = _contract_filter_clause(cursor, f.contract_statuses)

    paid_block = _paid_totals_cte(cursor).format(
        site_placeholders=site_placeholders,
        ct_clause=ct_clause,
        search_clause=search_clause,
        connection_clause=connection_clause,
        contract_clause=contract_clause,
    )

    base_cte = f"""
        WITH {paid_block},
        cohort AS (
            SELECT
                {cohort_body}
            FROM scoped sc
            INNER JOIN customers c ON c.id = sc.customer_id
            LEFT JOIN accounts a ON a.customer_id = c.id
            LEFT JOIN paid_totals pt ON pt.customer_id = sc.customer_id
        )
    """

    if count_only:
        sql = base_cte + f"SELECT COUNT(*) FROM cohort WHERE 1=1 {status_clause}"
        if statuses:
            params.extend(statuses)
        return sql, params

    sort_col = _SORT_COLS.get(q.sort_by, _SORT_COLS["site"])
    sort_dir = "DESC" if (q.sort_dir or "").lower() == "desc" else "ASC"

    if export_columns:
        select_parts = [_EXPORT_SELECT[c]["sql"] for c in export_columns]
        sql = base_cte + f"""
        SELECT {", ".join(select_parts)}
        FROM cohort
        WHERE 1=1 {status_clause}
        ORDER BY {sort_col} {sort_dir} NULLS LAST, customer_id ASC
        LIMIT %s
    """
        if statuses:
            params.extend(statuses)
        params.append(EXPORT_MAX_ROWS)
        return sql, params

    page = max(1, int(q.page or 1))
    page_size = max(1, min(500, int(q.page_size or 50)))
    offset = (page - 1) * page_size

    sql = base_cte + f"""
        SELECT customer_id, first_name, last_name, phone, site, customer_type,
               date_service_connected, date_service_terminated,
               payment_status_override, cohort_status_override, account_number, total_paid,
               payments_connection_fee, payments_readyboard_fee,
               payments_fee_repayment_via_electricity, payments_electricity,
               cohort_status
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


def _build_export_query(
    q: CohortExportRequest, columns: List[str], cursor,
) -> Tuple[str, list]:
    """All matching rows (capped at EXPORT_MAX_ROWS) with selected export columns."""
    base = CohortQuery(
        filters=q.filters,
        sort_by=q.sort_by,
        sort_dir=q.sort_dir,
    )
    return _build_query(
        base,
        count_only=False,
        cursor=cursor,
        extended_cohort=True,
        export_columns=columns,
    )


def _format_cohort_row(d: Dict[str, Any]) -> Dict[str, Any]:
    if d.get("date_service_connected"):
        d["date_service_connected"] = d["date_service_connected"].isoformat()
    if d.get("date_service_terminated"):
        d["date_service_terminated"] = d["date_service_terminated"].isoformat()
    if d.get("customer_commissioned_date"):
        d["customer_commissioned_date"] = d["customer_commissioned_date"].isoformat()
    if d.get("contract_signed_date"):
        d["contract_signed_date"] = d["contract_signed_date"].isoformat()
    if d.get("total_paid") is not None:
        d["total_paid"] = float(d["total_paid"])
    for k in (
        "payments_connection_fee",
        "payments_readyboard_fee",
        "payments_fee_repayment_via_electricity",
        "payments_electricity",
    ):
        if d.get(k) is not None:
            d[k] = float(d[k])
    for k in ("gps_lat", "gps_lon"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    if d.get("customer_commissioned") is not None:
        d["customer_commissioned"] = bool(d["customer_commissioned"])
    if d.get("contract_signed") is not None:
        d["contract_signed"] = bool(d["contract_signed"])
    return d


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _set_query_timeout(cursor) -> None:
    cursor.execute(
        "SET statement_timeout = %s",
        (str(QUERY_STATEMENT_TIMEOUT_MS),),
    )


def _cohort_http_error(exc: Exception, *, action: str) -> HTTPException:
    """Map Postgres errors to HTTP status (504 on statement timeout)."""
    pgcode = getattr(exc, "pgcode", None)
    msg = (getattr(exc, "pgerror", None) or str(exc) or "").lower()
    if pgcode == "57014" or "statement timeout" in msg or "canceling statement" in msg:
        return HTTPException(
            504,
            f"Cohort {action} timed out — select fewer sites or add filters and try again.",
        )
    detail = getattr(exc, "pgerror", None) or str(exc)
    return HTTPException(500, f"Cohort {action} failed: {detail}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/statuses")
def list_statuses(user: CurrentUser = Depends(require_employee)):
    """Return filter vocabularies and export column catalog."""
    return {
        "statuses": COHORT_STATUSES,
        "connection_statuses": CONNECTION_STATUSES,
        "contract_statuses": CONTRACT_STATUSES,
        "customer_types": _CUSTOMER_TYPES,
        "sort_columns": list(_SORT_COLS.keys()),
        "export_columns": _export_column_catalog(),
        "default_export_columns": DEFAULT_EXPORT_COLUMNS,
    }


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

    # Lazy import keeps the unit test suite (which builds queries but never
    # executes them) free of the full DB / FastAPI app stack.
    from customer_api import get_connection  # noqa: WPS433

    rows: List[Dict[str, Any]] = []
    total = 0
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _set_query_timeout(cur)
            count_sql, count_params = _build_query(q, count_only=True, cursor=cur)
            sql, params = _build_query(q, count_only=False, cursor=cur)

            cur.execute(count_sql, tuple(count_params))
            total = int(cur.fetchone()[0] or 0)

            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                rows.append(_format_cohort_row(_row_to_dict(cur, r)))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Cohort query failed")
            raise _cohort_http_error(exc, action="query") from exc

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
            "connection_statuses": q.filters.connection_statuses,
            "contract_statuses": q.filters.contract_statuses,
            "search": q.filters.search,
            "sort_by": q.sort_by,
            "sort_dir": q.sort_dir,
            "fee_threshold": _resolve_fee_threshold(q.filters.country),
        },
    }


@router.post("/export")
def export_cohort(
    body: CohortExportRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Return all customers matching filters for CSV export (not paginated UI rows)."""
    if body.sort_by not in _SORT_COLS:
        raise HTTPException(400, f"sort_by must be one of {list(_SORT_COLS.keys())}")

    columns = _resolve_export_columns(body.columns)
    if not columns:
        raise HTTPException(400, "No export columns selected")

    count_q = CohortQuery(filters=body.filters, sort_by=body.sort_by, sort_dir=body.sort_dir)

    from customer_api import get_connection  # noqa: WPS433

    rows: List[Dict[str, Any]] = []
    total = 0
    truncated = False
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _set_query_timeout(cur)
            count_sql, count_params = _build_query(count_q, count_only=True, cursor=cur)
            cur.execute(count_sql, tuple(count_params))
            total = int(cur.fetchone()[0] or 0)
            truncated = total > EXPORT_MAX_ROWS

            sql, params = _build_export_query(body, columns, cur)
            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                rows.append(_format_cohort_row(_row_to_dict(cur, r)))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Cohort export failed")
            raise _cohort_http_error(exc, action="export") from exc

    return {
        "rows": rows,
        "total": total,
        "exported": len(rows),
        "truncated": truncated,
        "columns": columns,
        "column_labels": {c: _EXPORT_SELECT[c]["label"] for c in columns},
    }
