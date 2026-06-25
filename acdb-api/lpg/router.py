"""FastAPI router for LPG (generator fuel) tracking.

Routes (prefix /api/lpg; reads require employee, writes require superadmin or
onm_team — mirrors the gensite module):

    GET  /api/lpg/sites
         Per-site LPG balance + 30d consumption/cost overview.

    GET  /api/lpg/sites/{code}
         Site detail: balance summary, batches, recent runs.

    POST /api/lpg/sites/{code}/batches
         Stock capture — record an LPG delivery (N x 48kg cylinders + price).

    GET  /api/lpg/sites/{code}/batches
    POST /api/lpg/sites/{code}/batches/{batch_id}/archive

    POST /api/lpg/sites/{code}/runs
         Start a generator run (timer begins).

    POST /api/lpg/runs/{run_id}/stop
         Stop a generator run; capture stoppage data + LPG depletion. Decrements
         the batch and fires a one-shot critical alert when the site reaches its
         last cylinder.

    GET  /api/lpg/sites/{code}/runs
    GET  /api/lpg/report          (JSON)
    GET  /api/lpg/report/export   (CSV)
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from middleware import require_employee
from models import CCRole, CurrentUser

from . import store

logger = logging.getLogger("cc-api.lpg")

router = APIRouter(prefix="/api/lpg", tags=["lpg"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_write_role(user: CurrentUser) -> None:
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(
            status_code=403,
            detail="LPG writes require superadmin or onm_team role.",
        )


def _instructor(user: CurrentUser, provided: Optional[str]) -> str:
    """The 'instructor' is the CC user; autofill from the session but allow an
    explicit override (per the flowchart: 'autofilled but editable')."""
    return provided or user.name or user.user_id


def _try_log_mutation(user: CurrentUser, action: str, table: str, record_id: str,
                      new_values: Optional[Dict[str, Any]] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        from mutations import try_log_mutation
        try_log_mutation(user, action, table, record_id,
                         new_values=new_values, metadata=metadata)
    except Exception as exc:
        logger.warning("cc_mutations audit skipped: %s", exc)


def _maybe_alert_critical(result: Dict[str, Any], site_code: str) -> bool:
    """Fire a one-shot WhatsApp/O&M alert when a site has just gone critical.
    Best-effort: never raises into the request path."""
    if not result.get("critical_triggered"):
        return False
    batch = result.get("batch") or {}
    batch_id = batch.get("id")
    remaining = result.get("site_remaining")
    try:
        from country_config import get_country_for_site
        country = get_country_for_site(site_code)
    except Exception:
        country = None
    text = (
        f"⚠️ LPG CRITICAL — {site_code.upper()} is down to its last cylinder "
        f"({remaining} remaining). Schedule an LPG delivery to avoid a generator outage."
    )
    try:
        from cc_bridge_notify import notify_cc_bridge
        notify_cc_bridge(
            {"source": "lpg", "kind": "lpg_critical", "site_code": site_code.upper(), "text": text},
            country_code=country,
        )
    except Exception as exc:
        logger.warning("lpg critical bridge notify failed for %s: %s", site_code, exc)
    if batch_id is not None:
        try:
            store.mark_critical_alert_sent(int(batch_id))
        except Exception as exc:
            logger.warning("mark_critical_alert_sent failed for batch %s: %s", batch_id, exc)
    return True


def _maybe_alert_low_runway(result: Dict[str, Any], site_code: str) -> bool:
    """Fire a one-shot predictive 'low runway' WhatsApp/O&M alert when a site has
    crossed below the days-left threshold. Best-effort; never raises."""
    if not result.get("low_runway_triggered"):
        return False
    batch_id = result.get("low_runway_batch_id")
    days = result.get("days_remaining")
    remaining = result.get("site_remaining")
    try:
        from country_config import get_country_for_site
        country = get_country_for_site(site_code)
    except Exception:
        country = None
    text = (
        f"⚠️ LPG LOW — {site_code.upper()} has about {days} day(s) of LPG left at the "
        f"current burn rate ({remaining} cylinders in stock). Plan a delivery to avoid an outage."
    )
    try:
        from cc_bridge_notify import notify_cc_bridge
        notify_cc_bridge(
            {"source": "lpg", "kind": "lpg_low_runway", "site_code": site_code.upper(), "text": text},
            country_code=country,
        )
    except Exception as exc:
        logger.warning("lpg low-runway bridge notify failed for %s: %s", site_code, exc)
    if batch_id is not None:
        try:
            store.mark_low_runway_alert_sent(int(batch_id))
        except Exception as exc:
            logger.warning("mark_low_runway_alert_sent failed for batch %s: %s", batch_id, exc)
    return True


def _seed_sites_best_effort() -> None:
    """Ensure the shared sites table is populated (gensite seeds all countries
    into the consolidated DB). Best-effort; never breaks the request."""
    try:
        from gensite import store as gensite_store
        gensite_store.seed_sites_from_country_config()
    except Exception as exc:
        logger.warning("lpg: seed_sites_from_country_config failed: %s", exc)


def _site_or_404(code: str) -> Dict[str, Any]:
    from gensite import store as gensite_store
    site = gensite_store.get_site(code)
    if not site:
        # Could be a valid code that just hasn't been seeded yet.
        _seed_sites_best_effort()
        site = gensite_store.get_site(code)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{code}' not found.")
    return site


# ---------------------------------------------------------------------------
# Overview + detail
# ---------------------------------------------------------------------------

@router.get("/sites")
def list_lpg_sites(
    country: Optional[str] = None,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _seed_sites_best_effort()
    rows = store.site_summaries(country=country)
    critical = [r for r in rows if r.get("is_critical")]
    return {
        "sites": rows,
        "count": len(rows),
        "critical_count": len(critical),
    }


@router.get("/sites/{code}")
def get_lpg_site(
    code: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _site_or_404(code)
    summaries = store.site_summaries()
    summary = next((s for s in summaries if s["code"] == code.upper()), None)
    return {
        "site_code": code.upper(),
        "summary": summary,
        "batches": store.list_batches(code),
        "runs": store.list_runs(code, limit=100),
    }


class SiteSettingsRequest(BaseModel):
    low_runway_warn_days: Optional[int] = Field(
        None, ge=1, le=120,
        description="Per-site low-runway warn threshold (days). Null clears the override (uses default 7).",
    )
    clear: bool = Field(False, description="If true, clear the per-site override (use module default).")


@router.patch("/sites/{code}/settings")
def update_site_settings(
    code: str,
    req: SiteSettingsRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    _site_or_404(code)
    days = None if req.clear else req.low_runway_warn_days
    updated = store.set_low_runway_warn_days(code, days)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Site '{code}' not found.")
    _try_log_mutation(
        user, "update", "sites", code.upper(),
        new_values={"lpg_low_runway_warn_days": updated.get("lpg_low_runway_warn_days")},
        metadata={"kind": "lpg_site_settings"},
    )
    return {"site": updated}


# ---------------------------------------------------------------------------
# Batches (stock capture)
# ---------------------------------------------------------------------------

class CreateBatchRequest(BaseModel):
    cylinders_total: int = Field(..., gt=0, description="Number of 48kg cylinders received")
    arrived_at: Optional[datetime] = None
    unit_price: Optional[float] = Field(None, ge=0, description="Price of ONE cylinder")
    currency: Optional[str] = None
    cylinder_kg: float = Field(48.0, gt=0)
    notes: Optional[str] = None


@router.post("/sites/{code}/batches")
def create_batch(
    code: str,
    req: CreateBatchRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    _site_or_404(code)
    batch = store.create_batch(
        site_code=code,
        cylinders_total=req.cylinders_total,
        arrived_at=req.arrived_at,
        unit_price=req.unit_price,
        currency=req.currency,
        cylinder_kg=req.cylinder_kg,
        created_by=user.user_id,
        notes=req.notes,
    )
    _try_log_mutation(
        user, "create", "lpg_batches", str(batch["id"]),
        new_values={
            "batch_number": batch["batch_number"],
            "cylinders_total": batch["cylinders_total"],
            "unit_price": batch.get("unit_price"),
        },
        metadata={"kind": "lpg_stock_capture", "site_code": code.upper()},
    )
    return {"batch": batch}


@router.get("/sites/{code}/batches")
def list_batches(
    code: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _site_or_404(code)
    return {"site_code": code.upper(), "batches": store.list_batches(code)}


@router.post("/sites/{code}/batches/{batch_id}/archive")
def archive_batch(
    code: str,
    batch_id: int,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    batch = store.get_batch(batch_id)
    if not batch or batch["site_code"] != code.upper():
        raise HTTPException(status_code=404, detail="Batch not found for this site.")
    updated = store.archive_batch(batch_id)
    _try_log_mutation(
        user, "update", "lpg_batches", str(batch_id),
        new_values={"status": "archived"},
        metadata={"kind": "lpg_batch_archive", "site_code": code.upper()},
    )
    return {"batch": updated}


# ---------------------------------------------------------------------------
# Generator runs
# ---------------------------------------------------------------------------

class StartRunRequest(BaseModel):
    batch_id: Optional[int] = Field(None, description="Reference batch the genset is drawing from")
    started_at: Optional[datetime] = None
    start_soc_pct: Optional[float] = Field(None, ge=0, le=100)
    start_reason: Optional[str] = None
    start_operator: Optional[str] = None
    start_instructor: Optional[str] = None
    generator_label: Optional[str] = None


@router.post("/sites/{code}/runs")
def start_run(
    code: str,
    req: StartRunRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    _site_or_404(code)
    if req.batch_id is not None:
        batch = store.get_batch(req.batch_id)
        if not batch or batch["site_code"] != code.upper():
            raise HTTPException(status_code=400, detail="batch_id does not belong to this site.")
    run = store.start_run(
        site_code=code,
        batch_id=req.batch_id,
        started_at=req.started_at,
        start_soc_pct=req.start_soc_pct,
        start_reason=req.start_reason,
        start_operator=req.start_operator,
        start_instructor=_instructor(user, req.start_instructor),
        generator_label=req.generator_label,
        created_by=user.user_id,
    )
    _try_log_mutation(
        user, "create", "lpg_generator_runs", str(run["id"]),
        new_values={"site_code": code.upper(), "batch_id": req.batch_id, "started_at": str(run["started_at"])},
        metadata={"kind": "lpg_run_start", "site_code": code.upper()},
    )
    return {"run": run}


class StopRunRequest(BaseModel):
    ended_at: Optional[datetime] = None
    stop_soc_pct: Optional[float] = Field(None, ge=0, le=100)
    stop_reason: Optional[str] = None
    stop_operator: Optional[str] = None
    stop_instructor: Optional[str] = None
    lpg_depleted: bool = False
    cylinders_consumed: Optional[int] = Field(None, ge=0)


@router.post("/runs/{run_id}/stop")
def stop_run(
    run_id: int,
    req: StopRunRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    existing = store.get_run(run_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Run not found.")
    if existing["status"] == "stopped":
        raise HTTPException(status_code=409, detail="Run is already stopped.")
    try:
        result = store.stop_run(
            run_id,
            ended_at=req.ended_at,
            stop_soc_pct=req.stop_soc_pct,
            stop_reason=req.stop_reason,
            stop_operator=req.stop_operator,
            stop_instructor=_instructor(user, req.stop_instructor),
            lpg_depleted=req.lpg_depleted,
            cylinders_consumed=req.cylinders_consumed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    alerted = _maybe_alert_critical(result, existing["site_code"])
    low_runway_alerted = _maybe_alert_low_runway(result, existing["site_code"])
    _try_log_mutation(
        user, "update", "lpg_generator_runs", str(run_id),
        new_values={
            "status": "stopped",
            "lpg_depleted": req.lpg_depleted,
            "cylinders_consumed": (result.get("run") or {}).get("cylinders_consumed"),
        },
        metadata={
            "kind": "lpg_run_stop",
            "site_code": existing["site_code"],
            "critical_triggered": result.get("critical_triggered"),
        },
    )
    return {
        "run": result.get("run"),
        "batch": result.get("batch"),
        "site_remaining": result.get("site_remaining"),
        "days_remaining": result.get("days_remaining"),
        "cylinders_per_day": result.get("cylinders_per_day"),
        "critical_triggered": result.get("critical_triggered"),
        "low_runway_triggered": result.get("low_runway_triggered"),
        "alert_sent": alerted,
        "low_runway_alert_sent": low_runway_alerted,
    }


@router.get("/sites/{code}/live-soc")
def get_live_soc(
    code: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    """Live battery SOC from gensite telemetry, to auto-prefill run forms."""
    return {"site_code": code.upper(), **store.live_battery_soc(code)}


@router.get("/sites/{code}/runs")
def list_runs(
    code: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _site_or_404(code)
    return {
        "site_code": code.upper(),
        "runs": store.list_runs(code, limit=limit, offset=offset),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _report_window(days: int, to: Optional[datetime]) -> tuple[datetime, datetime]:
    end = to or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


@router.get("/report")
def get_report(
    country: Optional[str] = None,
    days: int = Query(30, ge=1, le=366),
    to: Optional[datetime] = None,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    start, end = _report_window(days, to)
    rows = store.report(country, start, end)
    return {
        "start_utc": start.isoformat(timespec="seconds"),
        "end_utc": end.isoformat(timespec="seconds"),
        "country": country.upper() if country else None,
        "rows": rows,
    }


@router.get("/report/export")
def export_report_csv(
    country: Optional[str] = None,
    days: int = Query(30, ge=1, le=366),
    to: Optional[datetime] = None,
    user: CurrentUser = Depends(require_employee),
):
    start, end = _report_window(days, to)
    rows = store.report(country, start, end)
    sio = io.StringIO()
    w = csv.writer(sio)
    w.writerow([
        "site_code", "display_name", "country", "run_count",
        "cylinders_consumed", "kg_consumed", "runtime_hours",
        "unit_price", "currency", "est_cost",
    ])
    for r in rows:
        w.writerow([
            r.get("code"), r.get("display_name"), r.get("country"), r.get("run_count"),
            r.get("cylinders_consumed"), r.get("kg_consumed"), r.get("runtime_hours"),
            r.get("unit_price"), r.get("currency"), r.get("est_cost"),
        ])
    payload = sio.getvalue().encode("utf-8")
    fname = f"lpg_report_{(country or 'ALL').upper()}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
