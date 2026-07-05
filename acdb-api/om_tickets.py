"""
OM-first ticket proxy endpoints.

Makes om.1pwrafrica.com (the UGP adapter's OM ticket store) the source of
truth while CC provides an in-app API surface for the Maintenance Log UI and
customer-grievance intake.

The CC frontend speaks the legacy `tickets` table shape (site_code,
account_number, ticket_name, ...). This module translates bidirectionally
between that shape and the OM ticket schema (site_id, customer_id,
fault_description, ticket_class, ...), so the UI keeps working unchanged
while the store moves.

Ticket-class rule (Phase 3 of the OM portal refactor): CC-originated tickets
with an account_number become `customer_grievance` (customer link required,
asset optional, optional transaction_ref); otherwise `asset_fault`.

Env:
  OM_TICKETS_BASE_URL   default https://om.1pwrafrica.com/api/om
  OM_TICKETS_API_KEY    X-API-Key accepted by om nginx auth gate (OM_SERVER_API_KEYS)
  OM_TICKETS_SOURCE     'om' (proxy, no fallback) | 'legacy' (fallback allowed)
"""

from __future__ import annotations

import io
import os
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from middleware import CurrentUser, require_employee

logger = logging.getLogger("cc-api.om_tickets")

router = APIRouter(prefix="/api/om-tickets", tags=["om-tickets"])

OM_TICKETS_BASE_URL = os.environ.get("OM_TICKETS_BASE_URL", "https://om.1pwrafrica.com/api/om").rstrip("/")
OM_TICKETS_API_KEY = os.environ.get("OM_TICKETS_API_KEY", "").strip()
OM_TICKETS_BEARER_TOKEN = os.environ.get("OM_TICKETS_BEARER_TOKEN", "").strip()
OM_TICKETS_TIMEOUT_SECONDS = float(os.environ.get("OM_TICKETS_TIMEOUT_SECONDS", "20"))
OM_TICKETS_RETRIES = int(os.environ.get("OM_TICKETS_RETRIES", "2"))
OM_TICKETS_SOURCE = os.environ.get("OM_TICKETS_SOURCE", "legacy").strip().lower()


def _build_headers(user: CurrentUser, *, include_json: bool = True) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "X-OM-Source": "cc",
        "X-CC-User-Id": user.user_id,
        "X-CC-User-Role": user.role,
        "X-CC-User-Name": user.name or "",
    }
    if include_json:
        headers["Content-Type"] = "application/json"
    if OM_TICKETS_API_KEY:
        headers["X-API-Key"] = OM_TICKETS_API_KEY
        # The UGP adapter itself trusts X-OM-API-Key (nginx normally injects
        # it); when we call through the public URL the nginx gate validates
        # X-API-Key against CC's own OM_SERVER_API_KEYS and injects the OM key.
    if OM_TICKETS_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {OM_TICKETS_BEARER_TOKEN}"
    return headers


def _request_om(
    *,
    method: str,
    path: str,
    user: CurrentUser,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    stream: bool = False,
):
    url = f"{OM_TICKETS_BASE_URL}{path}"
    last_err: Exception | None = None
    for attempt in range(OM_TICKETS_RETRIES + 1):
        try:
            resp = requests.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=_build_headers(user),
                timeout=OM_TICKETS_TIMEOUT_SECONDS,
                stream=stream,
            )
            if resp.status_code >= 500 and attempt < OM_TICKETS_RETRIES:
                time.sleep(0.25 * (attempt + 1))
                continue
            return resp
        except requests.RequestException as exc:
            last_err = exc
            if attempt < OM_TICKETS_RETRIES:
                time.sleep(0.25 * (attempt + 1))
                continue

    raise HTTPException(
        status_code=503,
        detail={
            "message": "OM ticket backend unavailable",
            "upstream": OM_TICKETS_BASE_URL,
            "error": str(last_err) if last_err else "request_failed",
        },
    )


def _raise_for_upstream_error(resp: requests.Response) -> None:
    if resp.ok:
        return
    detail: Any
    try:
        detail = resp.json()
    except ValueError:
        detail = {"message": (resp.text or "").strip()[:500]}
    raise HTTPException(
        status_code=resp.status_code if resp.status_code < 500 else 502,
        detail={
            "message": "OM ticket API request failed",
            "upstream_status": resp.status_code,
            "upstream_detail": detail,
        },
    )


def _json_or_502(resp: requests.Response):
    try:
        return resp.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "OM ticket API returned non-JSON response",
                "upstream_status": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "body_preview": (resp.text or "")[:300],
            },
        )


def _allow_legacy_fallback() -> bool:
    return OM_TICKETS_SOURCE != "om"


def _legacy_ticket_module():
    # Lazy import to avoid eager cross-module coupling.
    import tickets

    return tickets


def _with_ticket_source(payload: Any, source: str):
    if isinstance(payload, dict):
        out = dict(payload)
        out["ticket_source"] = source
        return out
    return payload


# ---------------------------------------------------------------------------
# CC shape <-> OM shape translation
# ---------------------------------------------------------------------------
# CC statuses: open / in_progress / pending / resolved
# OM statuses: reported triaged assigned diagnosed in_progress pending
#              resolved verified closed

_CC_TO_OM_STATUS = {
    "open": "reported",
    "in_progress": "in_progress",
    "pending": "pending",
    "resolved": "resolved",
}

_OM_TO_CC_STATUS = {
    "reported": "open",
    "triaged": "open",
    "assigned": "open",
    "diagnosed": "open",
    "in_progress": "in_progress",
    "pending": "pending",
    "resolved": "resolved",
    "verified": "resolved",
    "closed": "resolved",
}

_OM_PRIORITIES = {"P1", "P2", "P3", "P4"}


def _om_to_cc(t: Dict[str, Any]) -> Dict[str, Any]:
    """Map an OM ticket dict to the legacy CC row shape the UI renders."""
    return {
        "id": t.get("ticket_id", ""),
        "ugp_ticket_id": t.get("ticket_id", ""),
        "source": "om",
        "phone": "",
        "customer_id": None,
        "account_number": t.get("customer_id", "") or "",
        "site_code": t.get("site_id", "") or "",
        "fault_description": t.get("fault_description", "") or "",
        "category": t.get("equipment_category", "") or "",
        "priority": t.get("priority", "") or "",
        "reported_by": t.get("reported_by", "") or "",
        "created_at": t.get("reported_at", "") or t.get("created_at", "") or "",
        "ticket_name": "",
        "failure_time": t.get("reported_at", "") or "",
        "services_affected": t.get("services_affected", "") or "",
        "troubleshooting_steps": t.get("troubleshooting_steps", "") or "",
        "cause_of_fault": t.get("cause_of_fault", "") or "",
        "precautions": t.get("preventive_action", "") or "",
        "restoration_time": t.get("resolved_at", "") or "",
        "resolution_approach": t.get("resolution_notes", "") or "",
        "duration": str(t.get("downtime_hours") or "") if t.get("downtime_hours") else "",
        "status": _OM_TO_CC_STATUS.get(t.get("status", ""), t.get("status", "") or "open"),
        "updated_at": t.get("updated_at", "") or "",
        "resolved_by": t.get("assigned_to", "") or "",
        # Extras surfaced to the UI (ignored by older views, used by grievance UX)
        "ticket_class": t.get("ticket_class", "asset_fault"),
        "transaction_ref": t.get("transaction_ref", "") or "",
        "om_status": t.get("status", ""),
        "om_ticket_id": t.get("ticket_id", ""),
    }


def _cc_to_om_create(body: Dict[str, Any], user: CurrentUser) -> Dict[str, Any]:
    """Map a legacy-shaped create payload to the OM TicketCreate schema."""
    account = (body.get("account_number") or "").strip()
    ticket_name = (body.get("ticket_name") or "").strip()
    fault = (body.get("fault_description") or "").strip()
    if ticket_name and ticket_name.lower() not in fault.lower():
        fault = f"{ticket_name}: {fault}" if fault else ticket_name
    phone = (body.get("phone") or "").strip()
    if phone:
        fault = f"{fault}\nReporter phone: {phone}" if fault else f"Reporter phone: {phone}"

    priority = (body.get("priority") or "").strip().upper()
    if priority not in _OM_PRIORITIES:
        priority = ""

    ticket_class = (body.get("ticket_class") or "").strip() or (
        "customer_grievance" if account else "asset_fault"
    )

    payload: Dict[str, Any] = {
        "site_id": (body.get("site_code") or "").strip() or None,
        "customer_id": account or None,
        "equipment_category": (body.get("category") or "").strip() or None,
        "fault_description": fault or "(no description)",
        "services_affected": body.get("services_affected") or None,
        "reported_by": body.get("reported_by") or user.name or user.user_id,
        "ticket_type": "corrective",
        "priority": priority or None,
        "ticket_class": ticket_class,
        "transaction_ref": (body.get("transaction_ref") or "").strip() or None,
    }
    return {k: v for k, v in payload.items() if v is not None}


def _cc_to_om_update(body: Dict[str, Any]) -> Dict[str, Any]:
    """Map a legacy-shaped patch payload to the OM TicketUpdate schema."""
    out: Dict[str, Any] = {}
    direct = {
        "fault_description": "fault_description",
        "services_affected": "services_affected",
        "troubleshooting_steps": "troubleshooting_steps",
        "cause_of_fault": "cause_of_fault",
        "precautions": "preventive_action",
        "restoration_time": "resolved_at",
        "site_code": "site_id",
        "account_number": "customer_id",
        "category": "equipment_category",
        "reported_by": "reported_by",
        "resolved_by": "assigned_to",
        "transaction_ref": "transaction_ref",
        "ticket_class": "ticket_class",
    }
    for src, dst in direct.items():
        if src in body and body[src] is not None:
            out[dst] = body[src]

    if body.get("status"):
        om_status = _CC_TO_OM_STATUS.get(str(body["status"]).strip().lower())
        if om_status:
            out["status"] = om_status

    prio = str(body.get("priority") or "").strip().upper()
    if prio in _OM_PRIORITIES:
        out["priority"] = prio

    return out


def _fetch_om_tickets_mapped(
    user: CurrentUser,
    *,
    site_code: Optional[str],
    status: Optional[str],
) -> List[Dict[str, Any]]:
    """Fetch (up to 500) OM tickets with server-side filters, mapped to CC shape."""
    params: Dict[str, Any] = {"limit": 500, "offset": 0}
    if site_code:
        params["site_id"] = site_code
    if status:
        om_status = _CC_TO_OM_STATUS.get(status.strip().lower())
        if om_status == "reported":
            # CC 'open' covers several OM statuses — filter locally instead.
            pass
        elif om_status:
            params["status"] = om_status
    resp = _request_om(method="GET", path="/tickets", user=user, params=params)
    _raise_for_upstream_error(resp)
    data = _json_or_502(resp)
    mapped = [_om_to_cc(t) for t in data.get("tickets", [])]
    if status:
        mapped = [t for t in mapped if t["status"] == status.strip().lower()]
    return mapped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def list_om_tickets(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    site_code: Optional[str] = Query(None),
    account_number: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    try:
        mapped = _fetch_om_tickets_mapped(user, site_code=site_code, status=status)
        if account_number:
            needle = account_number.strip().lower()
            mapped = [t for t in mapped if needle in (t["account_number"] or "").lower()]
        if search:
            needle = search.strip().lower()
            mapped = [
                t for t in mapped
                if needle in (t["fault_description"] or "").lower()
                or needle in (t["account_number"] or "").lower()
                or needle in (t["site_code"] or "").lower()
                or needle in str(t["id"]).lower()
            ]
        total = len(mapped)
        page = mapped[offset:offset + limit]
        return _with_ticket_source(
            {"tickets": page, "total": total, "limit": limit, "offset": offset}, "om"
        )
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500:
            legacy = _legacy_ticket_module()
            logger.warning("OM list failed; using legacy tickets fallback: %s", exc.detail)
            legacy_payload = legacy.list_tickets(
                limit=limit,
                offset=offset,
                site_code=site_code,
                account_number=account_number,
                status=status,
                search=search,
                user=user,
            )
            return _with_ticket_source(legacy_payload, "legacy_fallback")
        raise


@router.get("/export")
def export_om_tickets(
    site_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    quarter: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """Excel export of the Maintenance Log (built here from OM data)."""
    try:
        mapped = _fetch_om_tickets_mapped(user, site_code=site_code, status=status)
        if quarter:
            # quarter format: 2026-Q1
            try:
                year_s, q_s = quarter.upper().split("-Q")
                months = {
                    "1": ("01", "03"), "2": ("04", "06"),
                    "3": ("07", "09"), "4": ("10", "12"),
                }[q_s]
                lo, hi = f"{year_s}-{months[0]}", f"{year_s}-{months[1]}-31T23:59:59"
                mapped = [t for t in mapped if lo <= (t["created_at"] or "") <= hi]
            except Exception:
                pass

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Maintenance Log"
        cols = [
            ("Ticket", "id"), ("Class", "ticket_class"), ("Site", "site_code"),
            ("Account", "account_number"), ("Status", "status"), ("Priority", "priority"),
            ("Reported", "created_at"), ("Description", "fault_description"),
            ("Category", "category"), ("Services affected", "services_affected"),
            ("Troubleshooting", "troubleshooting_steps"), ("Cause", "cause_of_fault"),
            ("Preventive action", "precautions"), ("Restored", "restoration_time"),
            ("Transaction ref", "transaction_ref"), ("Reported by", "reported_by"),
        ]
        ws.append([c[0] for c in cols])
        for t in mapped:
            ws.append([t.get(c[1], "") for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=Maintenance_Log.xlsx"},
        )
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500:
            legacy = _legacy_ticket_module()
            logger.warning("OM export failed; using legacy export fallback: %s", exc.detail)
            return legacy.export_tickets(
                site_code=site_code,
                status=status,
                quarter=quarter,
                user=user,
            )
        raise


@router.get("/health")
def om_tickets_health(user: CurrentUser = Depends(require_employee)):
    """Lightweight upstream reachability check for operators."""
    resp = _request_om(method="GET", path="/statistics", user=user)
    if not resp.ok:
        return Response(status_code=502)
    return {"status": "ok", "upstream": OM_TICKETS_BASE_URL, "source": OM_TICKETS_SOURCE}


@router.get("/ref/{ticket_ref}")
def get_om_ticket(
    ticket_ref: str,
    user: CurrentUser = Depends(require_employee),
):
    try:
        resp = _request_om(
            method="GET",
            path=f"/tickets/{ticket_ref}",
            user=user,
        )
        _raise_for_upstream_error(resp)
        data = _json_or_502(resp)
        ticket = data.get("ticket") if isinstance(data, dict) and "ticket" in data else data
        return _om_to_cc(ticket)
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500:
            legacy = _legacy_ticket_module()
            logger.warning("OM get failed; using legacy ticket fallback: %s", exc.detail)
            return legacy.get_ticket(ticket_ref=ticket_ref, user=user)
        raise


@router.post("")
def create_om_ticket(
    body: Dict[str, Any],
    user: CurrentUser = Depends(require_employee),
):
    try:
        om_body = _cc_to_om_create(body, user)
        resp = _request_om(
            method="POST",
            path="/tickets",
            user=user,
            json_body=om_body,
        )
        _raise_for_upstream_error(resp)
        result = _json_or_502(resp)
        ticket = result.get("ticket") if isinstance(result, dict) else None
        # Activity trigger: a ticket on an account flags it for a prompt live balance pull.
        acct = body.get("account_number") if isinstance(body, dict) else None
        if acct:
            try:
                from balance_live import mark_account_due

                mark_account_due(acct)
            except Exception:
                pass
        if ticket:
            return {"success": True, "ticket": _om_to_cc(ticket), "id": ticket.get("ticket_id")}
        return result
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500:
            legacy = _legacy_ticket_module()
            logger.warning("OM create failed; using legacy ticket fallback: %s", exc.detail)
            payload = legacy.TicketCreate(**body)
            return legacy.create_ticket(body=payload, user=user)
        raise


@router.patch("/{ticket_ref}")
def update_om_ticket(
    ticket_ref: str,
    body: Dict[str, Any],
    user: CurrentUser = Depends(require_employee),
):
    try:
        om_body = _cc_to_om_update(body)
        om_body["updated_by"] = user.name or user.user_id
        resp = _request_om(
            method="PUT",
            path=f"/tickets/{ticket_ref}",
            user=user,
            json_body=om_body,
        )
        _raise_for_upstream_error(resp)
        result = _json_or_502(resp)
        ticket = result.get("ticket") if isinstance(result, dict) else None
        if ticket:
            return {"success": True, "ticket": _om_to_cc(ticket)}
        return result
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500 and ticket_ref.isdigit():
            legacy = _legacy_ticket_module()
            logger.warning("OM update failed; using legacy ticket fallback: %s", exc.detail)
            payload = legacy.TicketUpdate(**body)
            return legacy.update_ticket(ticket_id=int(ticket_ref), body=payload, user=user)
        raise


@router.post("/{ticket_ref}/comments")
def add_om_ticket_comment(
    ticket_ref: str,
    body: Dict[str, Any],
    user: CurrentUser = Depends(require_employee),
):
    payload = {
        "user": body.get("user") or user.name or user.user_id,
        "text": body.get("text", ""),
    }
    resp = _request_om(
        method="POST",
        path=f"/tickets/{ticket_ref}/comments",
        user=user,
        json_body=payload,
    )
    _raise_for_upstream_error(resp)
    return _json_or_502(resp)
