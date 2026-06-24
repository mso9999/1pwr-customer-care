"""1PDB data access for the LPG tracking module.

Thin psycopg2 layer matching the style of gensite/store.py — no ORM. All
queries use the pooled connection from ``customer_api.get_connection()``.

Domain model (see migrations/045_lpg_tracking.sql):
  * lpg_batches         — LPG deliveries (N x 48kg cylinders, unit price).
  * lpg_generator_runs  — genset runs (start->stop, SOC, reasons, depletion).

Balance is derived, not stored: a site's remaining cylinders = SUM of
cylinders_remaining over its non-archived batches. A site is CRITICAL when its
remaining cylinders have dropped to its last cylinder (<= 1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2.extras

logger = logging.getLogger("cc-api.lpg.store")

# When a site's total remaining cylinders fall to this count it is "critical"
# (down to its last cylinder), per the operations flowchart.
CRITICAL_REMAINING_THRESHOLD = 1


def _conn():
    from customer_api import get_connection
    return get_connection()


def _currency_for_site(site_code: str) -> Optional[str]:
    try:
        from country_config import get_currency_for_site
        return get_currency_for_site(site_code)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Batch number generation
# ---------------------------------------------------------------------------

def _next_batch_number(cur, site_code: str, arrived_at: datetime) -> str:
    """Generate a human-readable, unique batch number:
    ``LPG-<SITE>-<YYYYMMDD>-<NN>`` where NN is the next sequence for that
    (site, calendar day). Computed inside the caller's transaction so the
    UNIQUE constraint on batch_number is the final backstop.
    """
    day = arrived_at.astimezone(timezone.utc).strftime("%Y%m%d")
    prefix = f"LPG-{site_code.upper()}-{day}-"
    cur.execute(
        "SELECT COUNT(*) AS n FROM lpg_batches WHERE batch_number LIKE %s",
        (prefix + "%",),
    )
    row = cur.fetchone()
    # Caller's cursor may be a RealDictCursor — read the aggregate by alias.
    seq = int((row["n"] if isinstance(row, dict) else row[0]) or 0) + 1
    return f"{prefix}{seq:02d}"


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

def create_batch(
    *,
    site_code: str,
    cylinders_total: int,
    arrived_at: Optional[datetime] = None,
    unit_price: Optional[float] = None,
    currency: Optional[str] = None,
    cylinder_kg: float = 48.0,
    created_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    arrived_at = arrived_at or datetime.now(timezone.utc)
    currency = currency or _currency_for_site(site_code)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            batch_number = _next_batch_number(cur, site_code, arrived_at)
            cur.execute(
                """
                INSERT INTO lpg_batches (
                    site_code, batch_number, arrived_at,
                    cylinders_total, cylinders_remaining, cylinder_kg,
                    unit_price, currency, status, created_by, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                RETURNING *
                """,
                (
                    site_code.upper(), batch_number, arrived_at,
                    cylinders_total, cylinders_total, cylinder_kg,
                    unit_price, currency, created_by, notes,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def list_batches(site_code: str, include_archived: bool = True) -> List[Dict[str, Any]]:
    q = "SELECT * FROM lpg_batches WHERE site_code = %s"
    if not include_archived:
        q += " AND status <> 'archived'"
    q += " ORDER BY arrived_at DESC, id DESC"
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(q, (site_code.upper(),))
            return [dict(r) for r in cur.fetchall()]


def get_batch(batch_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lpg_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def archive_batch(batch_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE lpg_batches SET status = 'archived' WHERE id = %s RETURNING *",
                (batch_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Generator runs
# ---------------------------------------------------------------------------

def start_run(
    *,
    site_code: str,
    batch_id: Optional[int],
    started_at: Optional[datetime] = None,
    start_soc_pct: Optional[float] = None,
    start_reason: Optional[str] = None,
    start_operator: Optional[str] = None,
    start_instructor: Optional[str] = None,
    generator_label: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    started_at = started_at or datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO lpg_generator_runs (
                    site_code, batch_id, generator_label, status,
                    started_at, start_soc_pct, start_reason,
                    start_operator, start_instructor, created_by
                )
                VALUES (%s, %s, %s, 'running', %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    site_code.upper(), batch_id, generator_label,
                    started_at, start_soc_pct, start_reason,
                    start_operator, start_instructor, created_by,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lpg_generator_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def stop_run(
    run_id: int,
    *,
    ended_at: Optional[datetime] = None,
    stop_soc_pct: Optional[float] = None,
    stop_reason: Optional[str] = None,
    stop_operator: Optional[str] = None,
    stop_instructor: Optional[str] = None,
    lpg_depleted: bool = False,
    cylinders_consumed: Optional[int] = None,
) -> Dict[str, Any]:
    """Close out a run and, if a cylinder was depleted, decrement the run's
    batch atomically. Returns a dict with the updated run, the (optional)
    updated batch, and a ``critical_triggered`` flag set when the site has just
    crossed into critical (so the caller can fire a one-shot alert).

    All mutations happen in one transaction so a crash can't leave the run
    closed but the batch un-decremented.
    """
    ended_at = ended_at or datetime.now(timezone.utc)
    # The flow decrements by one cylinder per depletion event; allow an explicit
    # override for the rare case more than one cylinder was swapped in a run.
    consumed = 0
    if lpg_depleted:
        consumed = cylinders_consumed if cylinders_consumed is not None else 1
    if consumed < 0:
        consumed = 0

    result: Dict[str, Any] = {"run": None, "batch": None, "critical_triggered": False}

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM lpg_generator_runs WHERE id = %s FOR UPDATE",
                (run_id,),
            )
            run = cur.fetchone()
            if not run:
                conn.rollback()
                raise ValueError(f"run {run_id} not found")
            if run["status"] == "stopped":
                conn.rollback()
                raise ValueError(f"run {run_id} is already stopped")

            runtime_seconds = None
            started_at = run.get("started_at")
            if isinstance(started_at, datetime):
                runtime_seconds = max(0, int((ended_at - started_at).total_seconds()))

            cur.execute(
                """
                UPDATE lpg_generator_runs SET
                    status = 'stopped',
                    ended_at = %s,
                    stop_soc_pct = %s,
                    stop_reason = %s,
                    stop_operator = %s,
                    stop_instructor = %s,
                    lpg_depleted = %s,
                    cylinders_consumed = %s,
                    runtime_seconds = %s
                WHERE id = %s
                RETURNING *
                """,
                (
                    ended_at, stop_soc_pct, stop_reason, stop_operator,
                    stop_instructor, lpg_depleted, consumed, runtime_seconds, run_id,
                ),
            )
            updated_run = dict(cur.fetchone())

            batch_row: Optional[Dict[str, Any]] = None
            critical_triggered = False
            batch_id = run.get("batch_id")
            if consumed > 0 and batch_id is not None:
                # Remaining cylinders across the site BEFORE decrement (for the
                # critical-edge test). Lock the target batch row first.
                cur.execute(
                    "SELECT * FROM lpg_batches WHERE id = %s FOR UPDATE",
                    (batch_id,),
                )
                batch = cur.fetchone()
                if batch:
                    before_site = _site_remaining(cur, run["site_code"])
                    new_remaining = max(0, int(batch["cylinders_remaining"]) - consumed)
                    new_status = "depleted" if new_remaining == 0 else batch["status"]
                    cur.execute(
                        """
                        UPDATE lpg_batches SET
                            cylinders_remaining = %s,
                            status = %s
                        WHERE id = %s
                        RETURNING *
                        """,
                        (new_remaining, new_status, batch_id),
                    )
                    batch_row = dict(cur.fetchone())
                    after_site = before_site - consumed
                    # Critical edge: crossed down to the last cylinder this run,
                    # and we haven't already alerted for this batch.
                    if (
                        after_site <= CRITICAL_REMAINING_THRESHOLD
                        and before_site > CRITICAL_REMAINING_THRESHOLD
                        and batch_row.get("critical_alert_sent_at") is None
                    ):
                        critical_triggered = True

            result["run"] = updated_run
            result["batch"] = batch_row
            result["critical_triggered"] = critical_triggered
            result["site_remaining"] = _site_remaining(cur, run["site_code"])
        conn.commit()
    return result


def mark_critical_alert_sent(batch_id: int) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lpg_batches SET critical_alert_sent_at = NOW() "
                "WHERE id = %s AND critical_alert_sent_at IS NULL",
                (batch_id,),
            )
        conn.commit()


def list_runs(site_code: str, *, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.*, b.batch_number
                FROM lpg_generator_runs r
                LEFT JOIN lpg_batches b ON b.id = r.batch_id
                WHERE r.site_code = %s
                ORDER BY r.started_at DESC, r.id DESC
                LIMIT %s OFFSET %s
                """,
                (site_code.upper(), limit, offset),
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Derived balances / summaries
# ---------------------------------------------------------------------------

def _site_remaining(cur, site_code: str) -> int:
    """Remaining cylinders across a site's non-archived batches (uses the
    caller's cursor so it participates in the active transaction)."""
    cur.execute(
        """
        SELECT COALESCE(SUM(cylinders_remaining), 0) AS remaining
        FROM lpg_batches
        WHERE site_code = %s AND status <> 'archived'
        """,
        (site_code.upper(),),
    )
    row = cur.fetchone()
    val = row["remaining"] if isinstance(row, dict) else row[0]
    return int(val or 0)


def site_summaries(country: Optional[str] = None) -> List[Dict[str, Any]]:
    """Per-site LPG balance + recent consumption/cost, for the overview table.

    Only includes sites that have at least one batch or run logged (i.e. sites
    operations actually track LPG for), so the table isn't cluttered with every
    customer minigrid.
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                WITH bal AS (
                    SELECT
                        b.site_code,
                        SUM(b.cylinders_remaining) FILTER (WHERE b.status <> 'archived') AS cylinders_remaining,
                        SUM(b.cylinders_total)     FILTER (WHERE b.status <> 'archived') AS cylinders_total,
                        MAX(b.cylinder_kg)                                              AS cylinder_kg,
                        MAX(b.arrived_at)                                               AS last_delivery_at,
                        -- price of the most recent active batch (best current unit cost)
                        (ARRAY_AGG(b.unit_price ORDER BY b.arrived_at DESC) FILTER (WHERE b.unit_price IS NOT NULL))[1] AS last_unit_price,
                        (ARRAY_AGG(b.currency   ORDER BY b.arrived_at DESC) FILTER (WHERE b.currency   IS NOT NULL))[1] AS currency
                    FROM lpg_batches b
                    GROUP BY b.site_code
                ),
                cons AS (
                    SELECT
                        r.site_code,
                        SUM(r.cylinders_consumed) FILTER (WHERE r.started_at >= NOW() - INTERVAL '30 days') AS cyl_30d,
                        SUM(COALESCE(r.runtime_seconds, 0)) FILTER (WHERE r.started_at >= NOW() - INTERVAL '30 days') AS runtime_30d,
                        MAX(r.started_at) AS last_run_at,
                        COUNT(*) FILTER (WHERE r.status = 'running') AS open_runs
                    FROM lpg_generator_runs r
                    GROUP BY r.site_code
                )
                SELECT
                    s.code, s.display_name, s.country, s.district,
                    COALESCE(bal.cylinders_remaining, 0) AS cylinders_remaining,
                    COALESCE(bal.cylinders_total, 0)     AS cylinders_total,
                    COALESCE(bal.cylinder_kg, 48)        AS cylinder_kg,
                    bal.last_delivery_at,
                    bal.last_unit_price,
                    bal.currency,
                    COALESCE(cons.cyl_30d, 0)     AS cylinders_consumed_30d,
                    COALESCE(cons.runtime_30d, 0) AS runtime_seconds_30d,
                    cons.last_run_at,
                    COALESCE(cons.open_runs, 0)   AS open_runs
                FROM sites s
                JOIN bal ON bal.site_code = s.code
                LEFT JOIN cons ON cons.site_code = s.code
                WHERE (%s IS NULL OR s.country = %s)
                ORDER BY
                    (COALESCE(bal.cylinders_remaining, 0) <= %s) DESC,  -- critical first
                    s.code
                """,
                (country.upper() if country else None,
                 country.upper() if country else None,
                 CRITICAL_REMAINING_THRESHOLD),
            )
            rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        remaining = int(r.get("cylinders_remaining") or 0)
        r["is_critical"] = remaining <= CRITICAL_REMAINING_THRESHOLD
        kg = float(r.get("cylinder_kg") or 48)
        r["kg_remaining"] = round(remaining * kg, 1)
        unit = r.get("last_unit_price")
        r["value_remaining"] = round(remaining * float(unit), 2) if unit is not None else None
        cyl_30d = float(r.get("cylinders_consumed_30d") or 0)
        r["cost_30d"] = round(cyl_30d * float(unit), 2) if unit is not None else None
    return rows


def live_battery_soc(site_code: str) -> Dict[str, Any]:
    """Most recent non-null battery SOC for a site, from gensite telemetry
    (inverter_readings). Used to auto-prefill the genset start/stop SOC field.
    Returns {"soc_pct": float|None, "ts_utc": datetime|None}."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT battery_soc_pct, ts_utc
                FROM inverter_readings
                WHERE site_code = %s AND battery_soc_pct IS NOT NULL
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (site_code.upper(),),
            )
            row = cur.fetchone()
    if not row:
        return {"soc_pct": None, "ts_utc": None}
    soc = row.get("battery_soc_pct")
    return {
        "soc_pct": float(soc) if soc is not None else None,
        "ts_utc": row.get("ts_utc"),
    }


def report(country: Optional[str], start_utc: datetime, end_utc: datetime) -> List[Dict[str, Any]]:
    """Per-site consumption + cost over a window (for reporting/export)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    s.code, s.display_name, s.country,
                    COUNT(r.id)                                  AS run_count,
                    COALESCE(SUM(r.cylinders_consumed), 0)       AS cylinders_consumed,
                    COALESCE(SUM(r.runtime_seconds), 0)          AS runtime_seconds,
                    (ARRAY_AGG(b.unit_price ORDER BY b.arrived_at DESC)
                        FILTER (WHERE b.unit_price IS NOT NULL))[1] AS unit_price,
                    (ARRAY_AGG(b.currency ORDER BY b.arrived_at DESC)
                        FILTER (WHERE b.currency IS NOT NULL))[1]   AS currency,
                    MAX(b.cylinder_kg)                           AS cylinder_kg
                FROM lpg_generator_runs r
                JOIN sites s ON s.code = r.site_code
                LEFT JOIN lpg_batches b ON b.site_code = r.site_code
                WHERE r.started_at >= %s AND r.started_at < %s
                  AND (%s IS NULL OR s.country = %s)
                GROUP BY s.code, s.display_name, s.country
                ORDER BY s.code
                """,
                (start_utc, end_utc,
                 country.upper() if country else None,
                 country.upper() if country else None),
            )
            rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        cyl = float(r.get("cylinders_consumed") or 0)
        kg = float(r.get("cylinder_kg") or 48)
        unit = r.get("unit_price")
        r["kg_consumed"] = round(cyl * kg, 1)
        r["est_cost"] = round(cyl * float(unit), 2) if unit is not None else None
        r["runtime_hours"] = round(float(r.get("runtime_seconds") or 0) / 3600.0, 1)
    return rows
