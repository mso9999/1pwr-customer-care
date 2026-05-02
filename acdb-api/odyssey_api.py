"""
Odyssey Standard API
====================

Public, pull-based API that the **Odyssey** sponsor-monitoring platform calls
to ingest electricity payment and meter-metric data for customers under a
funder program (initial use case: **UEF / ZEDSI** in Zambia).

Two datasets are exposed (mirrors the validator at
https://platform.odysseyenergysolutions.com/#/standard-api/validator):

* ``GET /api/odyssey/v1/electricity-payment``
* ``GET /api/odyssey/v1/meter-metrics``

Both are bearer-token authenticated; tokens are issued per
*(program, country)* pair via the admin endpoints in :mod:`programs`. The
token resolves to a ``program_id``; only accounts that are members of that
program are visible -- there is no cross-program leakage even on the same
backend.

Conventions chosen to match the published Odyssey reference implementation
(MicroPowerManager Odyssey integration -- the closest documented Standard
API consumer):

* ``Authorization: Bearer <token>``
* Query window bounded by ``from`` / ``to`` (ISO 8601). Maximum span is
  configurable via :data:`ODYSSEY_MAX_WINDOW_HOURS` (default 25h to give a
  small safety margin over the 24h MPM convention).
* Page-based pagination with ``page`` / ``page_size`` (max 1000) and a
  ``next_page`` URL emitted in the response when more data is available.

The module is **read-only** by design -- it never writes to the customer DB.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from customer_api import get_connection

logger = logging.getLogger("cc-api.odyssey")

router = APIRouter(prefix="/api/odyssey/v1", tags=["odyssey"])

# Maximum (to - from) span an Odyssey caller may request. Aligns with the
# 24-hour convention published by the MPM reference implementation; we add an
# hour of slack so DST / partial-day windows don't fail at the boundary.
ODYSSEY_MAX_WINDOW_HOURS = int(os.environ.get("ODYSSEY_MAX_WINDOW_HOURS", "25"))

# Default + maximum page sizes. Odyssey paginates aggressively; keep the
# default small so first-page latency stays low.
DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Bearer token auth
# ---------------------------------------------------------------------------

class _AuthContext:
    """Resolved Odyssey bearer-token context for a single request."""

    __slots__ = ("token_id", "program_id", "program_code", "country_code")

    def __init__(
        self,
        token_id: int,
        program_id: int,
        program_code: str,
        country_code: Optional[str],
    ) -> None:
        self.token_id = token_id
        self.program_id = program_id
        self.program_code = program_code
        self.country_code = country_code


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def verify_odyssey_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> _AuthContext:
    """FastAPI dependency that validates the bearer token and returns the
    *(program, country)* binding. Refuses revoked, expired, or unknown tokens.

    Updates ``last_used_at`` / ``last_used_ip`` on the token row as a
    side-effect (best-effort -- never fails the request).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>').",
        )
    plaintext = authorization.split(None, 1)[1].strip()
    if not plaintext:
        raise HTTPException(status_code=401, detail="Empty bearer token.")

    th = _hash_token(plaintext)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.id, t.program_id, t.expires_at, t.revoked_at,
                   p.code, p.country_code, p.active
              FROM odyssey_api_tokens t
              JOIN programs p ON p.id = t.program_id
             WHERE t.token_hash = %s
             LIMIT 1
            """,
            (th,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid bearer token.")

        token_id, program_id, expires_at, revoked_at, code, country_code, prog_active = row
        now = datetime.now(timezone.utc)
        if revoked_at is not None:
            raise HTTPException(status_code=401, detail="Token has been revoked.")
        if expires_at is not None and expires_at <= now:
            raise HTTPException(status_code=401, detail="Token has expired.")
        if not prog_active:
            raise HTTPException(status_code=403, detail="Program is inactive.")

        try:
            cur.execute(
                "UPDATE odyssey_api_tokens "
                "   SET last_used_at = NOW(), last_used_ip = %s "
                " WHERE id = %s",
                (_client_ip(request), token_id),
            )
            conn.commit()
        except Exception:  # noqa: BLE001 - never fail the request on usage logging
            conn.rollback()

        return _AuthContext(
            token_id=token_id,
            program_id=program_id,
            program_code=code,
            country_code=country_code,
        )


# ---------------------------------------------------------------------------
# Common query helpers
# ---------------------------------------------------------------------------

def _parse_iso(label: str, value: str) -> datetime:
    """Parse an ISO-8601 string into a tz-aware UTC datetime.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM:SS``, with or without a ``Z``
    suffix or explicit offset.
    """
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"Missing required parameter: {label}")
    # Normalize trailing 'Z' which fromisoformat doesn't accept on Py < 3.11.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ISO-8601 value for {label}: {value!r} ({exc})",
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_window(frm: datetime, to: datetime) -> None:
    if to <= frm:
        raise HTTPException(status_code=400, detail="`to` must be strictly greater than `from`.")
    span = to - frm
    if span > timedelta(hours=ODYSSEY_MAX_WINDOW_HOURS):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested window exceeds {ODYSSEY_MAX_WINDOW_HOURS}h "
                "(Odyssey requires per-day pagination). Use multiple "
                "requests with smaller windows."
            ),
        )


def _coerce_page(page: int, page_size: int) -> Tuple[int, int]:
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1.")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"page_size must be between 1 and {MAX_PAGE_SIZE}.",
        )
    return page, page_size


def _next_page_url(request: Request, current_page: int, page_size: int, total_count: int) -> Optional[str]:
    if current_page * page_size >= total_count:
        return None
    qp = dict(request.query_params)
    qp["page"] = str(current_page + 1)
    qp["page_size"] = str(page_size)
    qs = "&".join(f"{k}={v}" for k, v in qp.items())
    return f"{request.url.scheme}://{request.url.netloc}{request.url.path}?{qs}"


# ---------------------------------------------------------------------------
# /health -- public
# ---------------------------------------------------------------------------

@router.get("/health")
def health() -> Dict[str, Any]:
    """Public liveness probe. Reports DB connectivity and program count."""
    out: Dict[str, Any] = {
        "status": "ok",
        "service": "odyssey-standard-api",
        "version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM programs WHERE active = TRUE")
            out["active_programs"] = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        out["status"] = "db_error"
        out["error"] = str(exc)
    return out


# ---------------------------------------------------------------------------
# /electricity-payment
# ---------------------------------------------------------------------------

def _payment_query(program_id: int, frm: datetime, to: datetime, limit: int, offset: int):
    """SQL + params tuple for the paged transactions slice.

    Joined data:
      * ``transactions``        -- the row itself
      * ``accounts``            -- to map to the customer
      * ``customers``           -- name / phone / lat-lon / customer-id-legacy
      * ``program_memberships`` -- restricts to the bound program
      * ``meters`` (LATERAL)    -- preferred meter for that account
    """
    sql = """
        WITH eligible AS (
            SELECT pm.account_number
              FROM program_memberships pm
             WHERE pm.program_id = %(program_id)s
        )
        SELECT
            t.id              AS txn_id,
            t.account_number  AS account_number,
            t.transaction_date AS transaction_date,
            t.transaction_amount AS amount,
            t.kwh_value        AS kwh_value,
            t.is_payment       AS is_payment,
            t.source           AS source,
            t.payment_reference AS payment_reference,
            t.sms_payer_phone  AS sms_payer_phone,
            t.meter_id         AS txn_meter_id,
            a.community        AS site_id,
            c.id               AS pg_customer_id,
            c.customer_id_legacy AS customer_id_legacy,
            c.first_name       AS first_name,
            c.middle_name      AS middle_name,
            c.last_name        AS last_name,
            c.phone            AS phone,
            c.cell_phone_1     AS cell_phone_1,
            c.gps_lat          AS latitude,
            c.gps_lon          AS longitude,
            m.meter_id         AS meter_serial,
            COUNT(*) OVER ()   AS total_count
          FROM transactions t
          JOIN eligible        e ON e.account_number = t.account_number
          JOIN accounts        a ON a.account_number = t.account_number
          LEFT JOIN customers  c ON c.id = a.customer_id
          LEFT JOIN LATERAL (
                SELECT meter_id
                  FROM meters
                 WHERE meters.account_number = t.account_number
                 ORDER BY (status = 'active') DESC, meter_id
                 LIMIT 1
          ) m ON TRUE
         WHERE t.is_payment = TRUE
           AND t.transaction_date >= %(frm)s
           AND t.transaction_date <  %(to)s
         ORDER BY t.transaction_date ASC, t.id ASC
         LIMIT %(limit)s OFFSET %(offset)s
    """
    params = {
        "program_id": program_id,
        "frm": frm,
        "to": to,
        "limit": limit,
        "offset": offset,
    }
    return sql, params


def _format_payment_record(row: psycopg2.extras.RealDictRow, currency: str) -> Dict[str, Any]:
    name = " ".join(
        s for s in (row.get("first_name"), row.get("middle_name"), row.get("last_name")) if s
    ).strip()
    phone = row.get("phone") or row.get("cell_phone_1") or None
    txn_date: Optional[datetime] = row.get("transaction_date")
    ts_iso = txn_date.astimezone(timezone.utc).isoformat() if txn_date else None
    amount = row.get("amount")
    return {
        "external_id": row.get("payment_reference") or f"txn-{row.get('txn_id')}",
        "transaction_id": row.get("txn_id"),
        "timestamp": ts_iso,
        "amount": float(amount) if amount is not None else None,
        "currency": currency,
        "kwh_value": float(row.get("kwh_value")) if row.get("kwh_value") is not None else None,
        "payment_type": _classify_payment_type(row.get("source")),
        "source": row.get("source"),
        "payment_reference": row.get("payment_reference"),
        "agent_id": None,  # 1PWR has no per-agent attribution; SMS gateway / portal user logged via cc_mutations.
        "customer_id": str(row.get("customer_id_legacy") or row.get("pg_customer_id") or "").strip() or None,
        "customer_name": name or None,
        "customer_phone": phone,
        "account_number": row.get("account_number"),
        "meter_serial": row.get("meter_serial") or row.get("txn_meter_id"),
        "site_id": row.get("site_id"),
        "latitude": float(row.get("latitude")) if row.get("latitude") is not None else None,
        "longitude": float(row.get("longitude")) if row.get("longitude") is not None else None,
    }


def _classify_payment_type(source: Optional[str]) -> str:
    """Bucket the internal ``transactions.source`` value into a small set of
    payment types Odyssey downstream consumers expect.
    """
    if not source:
        return "other"
    s = source.lower()
    if s in ("mpesa", "ecocash", "momo", "airtel", "sms", "sms_gateway"):
        return "mobile_money"
    if s in ("cash",):
        return "cash"
    if s in ("portal", "manual"):
        return "manual"
    if s in ("koios", "thundercloud", "balance_seed"):
        return "system"
    return "other"


@router.get("/electricity-payment")
def get_electricity_payments(
    request: Request,
    auth: _AuthContext = Depends(verify_odyssey_token),
    from_: str = Query(..., alias="from", description="ISO-8601 inclusive start of the window."),
    to: str = Query(..., description="ISO-8601 exclusive end of the window."),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """Return paginated electricity-payment transactions for the program tied
    to the bearer token, filtered to ``from <= transaction_date < to``.
    """
    frm_dt = _parse_iso("from", from_)
    to_dt = _parse_iso("to", to)
    _validate_window(frm_dt, to_dt)
    page, page_size = _coerce_page(page, page_size)

    # Resolve currency from country_config (token-bound country wins; falls
    # back to per-site lookup for cross-country safety).
    from country_config import get_currency_for_site, COUNTRY  # local import: avoid load-time cycles
    default_currency = COUNTRY.currency

    sql, params = _payment_query(
        auth.program_id, frm_dt, to_dt, page_size, (page - 1) * page_size
    )

    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()

    total = int(rows[0]["total_count"]) if rows else 0
    data = [
        _format_payment_record(
            r,
            currency=get_currency_for_site(r.get("site_id") or "") or default_currency,
        )
        for r in rows
    ]

    return {
        "dataset": "electricity-payment",
        "program": auth.program_code,
        "country": auth.country_code,
        "from": frm_dt.isoformat(),
        "to": to_dt.isoformat(),
        "page": page,
        "page_size": page_size,
        "total": total,
        "count": len(data),
        "next_page": _next_page_url(request, page, page_size, total),
        "data": data,
    }


# ---------------------------------------------------------------------------
# /meter-metrics
# ---------------------------------------------------------------------------

def _meter_metrics_query(program_id: int, frm: datetime, to: datetime, limit: int, offset: int):
    """SQL + params for daily kWh roll-ups per meter.

    Resolves the *active* meter per account (so swapped meters don't double-
    count) and aggregates ``hourly_consumption`` into UTC days.
    """
    sql = """
        WITH eligible AS (
            SELECT pm.account_number
              FROM program_memberships pm
             WHERE pm.program_id = %(program_id)s
        ),
        active_meters AS (
            SELECT DISTINCT ON (m.account_number)
                   m.account_number, m.meter_id
              FROM meters m
              JOIN eligible e ON e.account_number = m.account_number
             ORDER BY m.account_number,
                      (m.status = 'active') DESC,
                      m.meter_id
        ),
        daily AS (
            SELECT h.account_number,
                   date_trunc('day', h.reading_hour AT TIME ZONE 'UTC') AS reading_day,
                   SUM(h.kwh)::float                                    AS kwh_delivered,
                   COUNT(*)                                             AS reading_count,
                   MAX(h.reading_hour)                                  AS last_reading
              FROM hourly_consumption h
              JOIN eligible e ON e.account_number = h.account_number
             WHERE h.reading_hour >= %(frm)s
               AND h.reading_hour <  %(to)s
             GROUP BY h.account_number, reading_day
        )
        SELECT
            d.reading_day        AS reading_day,
            d.account_number     AS account_number,
            d.kwh_delivered      AS kwh_delivered,
            d.reading_count      AS reading_count,
            d.last_reading       AS last_reading,
            am.meter_id          AS meter_serial,
            a.community          AS site_id,
            c.id                 AS pg_customer_id,
            c.customer_id_legacy AS customer_id_legacy,
            c.first_name         AS first_name,
            c.middle_name        AS middle_name,
            c.last_name          AS last_name,
            c.gps_lat            AS latitude,
            c.gps_lon            AS longitude,
            COUNT(*) OVER ()     AS total_count
          FROM daily d
          JOIN accounts        a  ON a.account_number = d.account_number
          LEFT JOIN active_meters am ON am.account_number = d.account_number
          LEFT JOIN customers    c  ON c.id = a.customer_id
         ORDER BY d.reading_day ASC, d.account_number ASC
         LIMIT %(limit)s OFFSET %(offset)s
    """
    params = {
        "program_id": program_id,
        "frm": frm,
        "to": to,
        "limit": limit,
        "offset": offset,
    }
    return sql, params


def _format_meter_metric_record(row: psycopg2.extras.RealDictRow) -> Dict[str, Any]:
    name = " ".join(
        s for s in (row.get("first_name"), row.get("middle_name"), row.get("last_name")) if s
    ).strip()
    day: Optional[datetime] = row.get("reading_day")
    ts_iso = day.astimezone(timezone.utc).isoformat() if day else None
    reading_count = int(row.get("reading_count") or 0)
    return {
        "external_id": (
            f"{row.get('meter_serial') or row.get('account_number')}-"
            f"{day.strftime('%Y-%m-%d') if day else ''}"
        ),
        "timestamp": ts_iso,
        "interval": "P1D",  # ISO 8601 duration: per-day rollup
        "kwh_delivered": float(row.get("kwh_delivered") or 0.0),
        "reading_count": reading_count,
        "error_type": "normal" if reading_count > 0 else "offline",
        "meter_serial": row.get("meter_serial"),
        "account_number": row.get("account_number"),
        "site_id": row.get("site_id"),
        "customer_id": str(row.get("customer_id_legacy") or row.get("pg_customer_id") or "").strip() or None,
        "customer_name": name or None,
        "latitude": float(row.get("latitude")) if row.get("latitude") is not None else None,
        "longitude": float(row.get("longitude")) if row.get("longitude") is not None else None,
    }


@router.get("/meter-metrics")
def get_meter_metrics(
    request: Request,
    auth: _AuthContext = Depends(verify_odyssey_token),
    from_: str = Query(..., alias="from", description="ISO-8601 inclusive start of the window."),
    to: str = Query(..., description="ISO-8601 exclusive end of the window."),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """Return paginated daily kWh metrics per meter for the program tied to
    the bearer token, filtered to ``from <= reading_hour < to``.
    """
    frm_dt = _parse_iso("from", from_)
    to_dt = _parse_iso("to", to)
    _validate_window(frm_dt, to_dt)
    page, page_size = _coerce_page(page, page_size)

    sql, params = _meter_metrics_query(
        auth.program_id, frm_dt, to_dt, page_size, (page - 1) * page_size
    )

    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()

    total = int(rows[0]["total_count"]) if rows else 0
    data = [_format_meter_metric_record(r) for r in rows]

    return {
        "dataset": "meter-metrics",
        "program": auth.program_code,
        "country": auth.country_code,
        "from": frm_dt.isoformat(),
        "to": to_dt.isoformat(),
        "page": page,
        "page_size": page_size,
        "total": total,
        "count": len(data),
        "next_page": _next_page_url(request, page, page_size, total),
        "data": data,
    }
