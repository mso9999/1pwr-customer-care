"""
1PDB coverage gap admin endpoints.

Mounted at ``/api/admin/coverage/...`` (superadmin only). The same SQL
that powers ``scripts/ops/audit_coverage_gaps.py`` is exposed live so ops
can self-serve the audit from the portal without SSH'ing the host.

Endpoints
---------

* ``GET  /api/admin/coverage/audit``               -- live audit (no DB write)
* ``POST /api/admin/coverage/snapshot``            -- live audit + persist to ``coverage_snapshots``
* ``GET  /api/admin/coverage/snapshots``           -- list recent snapshots (lightweight)
* ``GET  /api/admin/coverage/snapshots/{id}``      -- one snapshot in full
* ``GET  /api/admin/coverage/upstream-freshness``  -- POST against Koios v2 ``data/freshness`` per site, diff vs our last reading. Network-bound; cached for ~5 min.

Querying live audit data is cheap (~30s on a populated DB); snapshots are
written by a daily systemd timer (`cc-coverage-snapshot.timer`) so the
admin UI can chart trends without re-running the queries on every load.

Implementation note: instead of duplicating the SQL from the ops script,
this module **imports** the script's ``run_audit`` and re-renders. The
script lives in ``scripts/ops/`` for two reasons: it must work standalone
for systemd / cron, AND it predates this in-portal layer.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from customer_api import get_connection
from middleware import require_role
from models import CCRole, CurrentUser
from mutations import try_log_mutation


logger = logging.getLogger("cc-api.coverage")

router = APIRouter(prefix="/api/admin/coverage", tags=["coverage"])


# ---------------------------------------------------------------------------
# Bridge to ``scripts/ops/audit_coverage_gaps.py``
# ---------------------------------------------------------------------------

def _ops_script_dir() -> str:
    """Locate ``scripts/ops/`` relative to this backend module.

    Production layout: backend at ``/opt/cc-portal/backend``, script at
    ``/opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py`` (deploy
    workflow rsyncs the ``acdb-api/`` tree, AND ``scripts/ops/`` lives in
    the repo root). Honour ``CC_OPS_SCRIPTS_DIR`` for tests.
    """
    if "CC_OPS_SCRIPTS_DIR" in os.environ:
        return os.environ["CC_OPS_SCRIPTS_DIR"]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "scripts", "ops")),
        "/opt/cc-portal/backend/scripts/ops",
        "/opt/1pdb/scripts/ops",
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.exists(os.path.join(c, "audit_coverage_gaps.py")):
            return c
    raise RuntimeError(
        "Cannot locate scripts/ops/audit_coverage_gaps.py. "
        "Set CC_OPS_SCRIPTS_DIR to override."
    )


def _import_audit_script():
    """Lazy-import the ops audit script so we don't pull psycopg2 etc.
    until the endpoint is hit.
    """
    sd = _ops_script_dir()
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import audit_coverage_gaps  # type: ignore[import-not-found]
    return audit_coverage_gaps


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class _AuditQueryParams(BaseModel):
    country: str = Field("LS", description="Country DB to audit (LS, BN).")
    window_months: int = Field(8, ge=1, le=36)
    stale_days: int = Field(30, ge=1, le=365)
    deficit_threshold: float = Field(0.50, ge=0.0, le=1.0)


class _SnapshotRequest(_AuditQueryParams):
    notes: Optional[str] = None
    include_upstream: bool = Field(False, description="Also probe Koios v2 freshness (slow).")


class _SnapshotSummary(BaseModel):
    id: int
    snapshot_at: str
    country_code: str
    active_meters: int
    zero_coverage_meters: int
    stale_meters: int
    monthly_deficits_flagged: int
    sites_with_active_meters: int
    sites_with_data: int
    triggered_by: Optional[str] = None
    notes: Optional[str] = None
    upstream_checked_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Live audit
# ---------------------------------------------------------------------------

def _resolve_db_url(country: str) -> str:
    cc = country.strip().upper()
    if cc == "BN":
        # BN-API host already has DATABASE_URL set to onepower_bj. On the
        # LS-API host, we fall back to the explicit DATABASE_URL_BN env.
        return os.environ.get("DATABASE_URL_BN") or os.environ.get(
            "DATABASE_URL", "postgresql://cc_api@localhost:5432/onepower_bj",
        )
    return os.environ.get("DATABASE_URL", "postgresql://cc_api@localhost:5432/onepower_cc")


def _run_live_audit(params: _AuditQueryParams) -> Dict[str, Any]:
    audit_mod = _import_audit_script()
    db_url = _resolve_db_url(params.country)
    return audit_mod.run_audit(
        params.country, db_url,
        window_months=params.window_months,
        stale_days=params.stale_days,
        deficit_threshold=params.deficit_threshold,
    )


@router.get("/audit")
def live_audit(
    country: str = Query("LS"),
    window_months: int = Query(8, ge=1, le=36),
    stale_days: int = Query(30, ge=1, le=365),
    deficit_threshold: float = Query(0.50, ge=0.0, le=1.0),
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Run the audit live and return the JSON payload (no DB write)."""
    try:
        params = _AuditQueryParams(
            country=country, window_months=window_months,
            stale_days=stale_days, deficit_threshold=deficit_threshold,
        )
        return _run_live_audit(params)
    except Exception as e:
        logger.exception("live_audit failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Audit failed: {e}")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def _persist_snapshot(payload: Dict[str, Any], *, triggered_by: str, notes: Optional[str]) -> int:
    """Insert one row into ``coverage_snapshots`` and return the new id."""
    totals = payload.get("totals", {})
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO coverage_snapshots (
                country_code,
                active_meters, zero_coverage_meters, stale_meters,
                monthly_deficits_flagged, sites_with_active_meters, sites_with_data,
                window_months, stale_days, deficit_threshold,
                monthly_coverage, monthly_deficits, last_ingest,
                zero_coverage_summary, cross_country_meters,
                declared_sites_missing, orphan_sites,
                zero_coverage_meters_detail, stale_meters_detail,
                upstream_freshness, upstream_checked_at,
                triggered_by, notes
            ) VALUES (
                %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            )
            RETURNING id
            """,
            (
                payload["country"],
                totals.get("active_meters", 0),
                totals.get("zero_coverage_meters", 0),
                totals.get("stale_meters", 0),
                totals.get("monthly_deficits_flagged", 0),
                totals.get("sites_with_active_meters", 0),
                totals.get("sites_with_data", 0),
                payload["window_months"], payload["stale_days"], payload["deficit_threshold"],
                json.dumps(payload.get("monthly_coverage", {})),
                json.dumps(payload.get("monthly_deficits", [])),
                json.dumps(payload.get("last_ingest", {})),
                json.dumps(payload.get("zero_coverage_summary", {})),
                json.dumps(payload.get("cross_country_meters", [])),
                json.dumps(payload.get("declared_sites_missing_data", [])),
                json.dumps(payload.get("orphan_sites", [])),
                json.dumps(payload.get("zero_coverage_meters", []), default=str),
                json.dumps(payload.get("stale_meters", []), default=str),
                json.dumps(payload.get("upstream_freshness")) if payload.get("upstream_freshness") else None,
                payload.get("upstream_checked_at"),
                triggered_by,
                notes,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return int(new_id)


@router.post("/snapshot")
def take_snapshot(
    req: _SnapshotRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Run the audit and persist one row in ``coverage_snapshots``."""
    try:
        payload = _run_live_audit(req)
        if req.include_upstream:
            try:
                payload["upstream_freshness"] = _fetch_koios_freshness(req.country)
                payload["upstream_checked_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                logger.warning("Upstream freshness probe failed: %s", e)
                payload["upstream_freshness"] = {"error": str(e)}
                payload["upstream_checked_at"] = datetime.now(timezone.utc).isoformat()
        snap_id = _persist_snapshot(
            payload, triggered_by=f"admin:{user.user_id}", notes=req.notes,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("take_snapshot failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {e}")

    try_log_mutation(
        user, "create", "coverage_snapshots", str(snap_id),
        new_values={"country": req.country, "totals": payload.get("totals", {})},
        metadata={"origin": "coverage_admin"},
    )
    return {"snapshot_id": snap_id, "totals": payload.get("totals", {})}


@router.get("/snapshots", response_model=List[_SnapshotSummary])
def list_snapshots(
    country: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=365),
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """List recent snapshots (lightweight; no detail blobs)."""
    sql = (
        "SELECT id, snapshot_at, country_code, "
        "       active_meters, zero_coverage_meters, stale_meters, "
        "       monthly_deficits_flagged, sites_with_active_meters, sites_with_data, "
        "       triggered_by, notes, upstream_checked_at "
        "  FROM coverage_snapshots"
    )
    params: List[Any] = []
    if country:
        sql += " WHERE country_code = %s"
        params.append(country.strip().upper())
    sql += " ORDER BY snapshot_at DESC LIMIT %s"
    params.append(limit)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        _SnapshotSummary(
            id=r[0],
            snapshot_at=r[1].isoformat() if r[1] else "",
            country_code=r[2],
            active_meters=r[3], zero_coverage_meters=r[4], stale_meters=r[5],
            monthly_deficits_flagged=r[6],
            sites_with_active_meters=r[7], sites_with_data=r[8],
            triggered_by=r[9], notes=r[10],
            upstream_checked_at=r[11].isoformat() if r[11] else None,
        )
        for r in rows
    ]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: int,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Return one full snapshot (detail blobs included)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, snapshot_at, country_code, "
            "       active_meters, zero_coverage_meters, stale_meters, "
            "       monthly_deficits_flagged, sites_with_active_meters, sites_with_data, "
            "       window_months, stale_days, deficit_threshold, "
            "       monthly_coverage, monthly_deficits, last_ingest, "
            "       zero_coverage_summary, cross_country_meters, "
            "       declared_sites_missing, orphan_sites, "
            "       zero_coverage_meters_detail, stale_meters_detail, "
            "       upstream_freshness, upstream_checked_at, "
            "       triggered_by, notes "
            "  FROM coverage_snapshots WHERE id = %s",
            (snapshot_id,),
        )
        r = cur.fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found.")
    return {
        "id": r[0],
        "snapshot_at": r[1].isoformat() if r[1] else "",
        "country_code": r[2],
        "totals": {
            "active_meters": r[3],
            "zero_coverage_meters": r[4],
            "stale_meters": r[5],
            "monthly_deficits_flagged": r[6],
            "sites_with_active_meters": r[7],
            "sites_with_data": r[8],
        },
        "window_months": r[9], "stale_days": r[10], "deficit_threshold": r[11],
        "monthly_coverage": r[12],
        "monthly_deficits": r[13],
        "last_ingest": r[14],
        "zero_coverage_summary": r[15],
        "cross_country_meters": r[16],
        "declared_sites_missing_data": r[17],
        "orphan_sites": r[18],
        "zero_coverage_meters": r[19],
        "stale_meters": r[20],
        "upstream_freshness": r[21],
        "upstream_checked_at": r[22].isoformat() if r[22] else None,
        "triggered_by": r[23],
        "notes": r[24],
    }


# ---------------------------------------------------------------------------
# Koios upstream-freshness probe (cached)
# ---------------------------------------------------------------------------

# In-process cache: ``{country_code: (timestamp, payload)}``. Keeps the
# Koios API call rate-friendly (per CONTEXT.md, 30k req/day per org).
_UPSTREAM_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_UPSTREAM_CACHE_TTL_SECONDS = int(os.environ.get("CC_COVERAGE_UPSTREAM_CACHE_TTL", "300"))


def _koios_creds(country_code: str) -> tuple[str, str, str]:
    """Return ``(base_url, api_key, api_secret)`` for Koios v2 freshness."""
    cc = country_code.strip().upper()
    base = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
    key = (
        os.environ.get(f"KOIOS_API_KEY_{cc}")
        or os.environ.get("KOIOS_API_KEY", "")
    )
    secret = (
        os.environ.get(f"KOIOS_API_SECRET_{cc}")
        or os.environ.get("KOIOS_API_SECRET", "")
    )
    return base, key, secret


def _fetch_koios_freshness(country_code: str) -> Dict[str, Any]:
    """Probe Koios v2 ``data/freshness`` for every site we know in the country.

    Returns ``{site_code: {koios_min, koios_max}}`` along with a summary
    that diffs against our DB's last known reading per site.
    """
    cc = country_code.strip().upper()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from country_config import _REGISTRY  # type: ignore[attr-defined]
    finally:
        # Don't leave the path littered if it wasn't already there.
        pass

    cfg = _REGISTRY.get(cc)
    if not cfg or not cfg.koios_org_id:
        raise RuntimeError(f"No Koios org configured for {cc}")

    base, key, secret = _koios_creds(cc)
    if not key or not secret:
        raise RuntimeError(f"KOIOS_API_KEY[_ {cc}] / KOIOS_API_SECRET[_ {cc}] not set")

    headers = {"X-API-KEY": key, "X-API-SECRET": secret, "Content-Type": "application/json"}
    url = f"{base}/api/v2/organizations/{cfg.koios_org_id}/data/freshness"
    out: Dict[str, Dict[str, Any]] = {}
    sites = cfg.koios_sites or {}
    for site_code, site_uuid in sites.items():
        try:
            r = requests.post(url, json={"filters": {"sites": [site_uuid]}},
                              headers=headers, timeout=20)
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or body
            if isinstance(data, list) and data:
                row = data[0]
            elif isinstance(data, dict):
                row = data
            else:
                row = {}
            out[site_code] = {
                "koios_first_date": row.get("first_date") or row.get("from"),
                "koios_last_date": row.get("last_date") or row.get("to"),
                "raw": row,
            }
        except requests.RequestException as e:
            out[site_code] = {"error": str(e)}
    return {
        "country": cc,
        "org_id": cfg.koios_org_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "per_site": out,
    }


@router.get("/upstream-freshness")
def upstream_freshness(
    country: str = Query("LS"),
    refresh: bool = Query(False, description="Bypass the in-process cache."),
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Probe Koios v2 freshness for every configured site in *country*.

    Result is cached in-process for ``CC_COVERAGE_UPSTREAM_CACHE_TTL``
    seconds (default 300). Pass ``refresh=true`` to bypass.
    """
    cc = country.strip().upper()
    now = time.time()
    if not refresh:
        cached = _UPSTREAM_CACHE.get(cc)
        if cached and (now - cached[0]) < _UPSTREAM_CACHE_TTL_SECONDS:
            return {"cached": True, "age_seconds": int(now - cached[0]), **cached[1]}

    try:
        payload = _fetch_koios_freshness(cc)
    except Exception as e:
        logger.warning("Koios freshness probe failed for %s: %s", cc, e)
        raise HTTPException(status_code=502, detail=f"Koios freshness probe failed: {e}")

    _UPSTREAM_CACHE[cc] = (now, payload)
    return {"cached": False, **payload}


# ---------------------------------------------------------------------------
# Trend / dashboard helpers
# ---------------------------------------------------------------------------

@router.get("/trend")
def coverage_trend(
    country: str = Query("LS"),
    days: int = Query(60, ge=7, le=365),
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
):
    """Return one row per snapshot showing how the headline counters
    moved over the last *days* days. Powers the trend chart on the
    admin UI.
    """
    cc = country.strip().upper()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_at, active_meters, zero_coverage_meters, stale_meters, "
            "       monthly_deficits_flagged "
            "  FROM coverage_snapshots "
            " WHERE country_code = %s AND snapshot_at >= %s "
            " ORDER BY snapshot_at ASC",
            (cc, cutoff),
        )
        rows = cur.fetchall()
    return {
        "country": cc,
        "days": days,
        "points": [
            {
                "snapshot_at": r[0].isoformat() if r[0] else "",
                "active_meters": r[1],
                "zero_coverage_meters": r[2],
                "stale_meters": r[3],
                "monthly_deficits_flagged": r[4],
            }
            for r in rows
        ],
    }
