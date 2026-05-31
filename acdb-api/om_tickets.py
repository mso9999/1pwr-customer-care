"""
OM-first ticket proxy endpoints.

Makes om.1pwrafrica.com the source of truth while CC provides an in-app API
surface for Maintenance Log UI and related workflows.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from middleware import CurrentUser, require_employee

logger = logging.getLogger("cc-api.om_tickets")

router = APIRouter(prefix="/api/om-tickets", tags=["om-tickets"])

OM_TICKETS_BASE_URL = os.environ.get("OM_TICKETS_BASE_URL", "https://om.1pwrafrica.com/api").rstrip("/")
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
            if attempt >= OM_TICKETS_RETRIES:
                break
            time.sleep(0.25 * (attempt + 1))

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
        params = {
            "limit": limit,
            "offset": offset,
            "site_code": site_code,
            "account_number": account_number,
            "status": status,
            "search": search,
        }
        resp = _request_om(method="GET", path="/tickets", user=user, params=params)
        _raise_for_upstream_error(resp)
        return _json_or_502(resp)
    except HTTPException as exc:
        if _allow_legacy_fallback() and exc.status_code >= 500:
            legacy = _legacy_ticket_module()
            logger.warning("OM list failed; using legacy tickets fallback: %s", exc.detail)
            return legacy.list_tickets(
                limit=limit,
                offset=offset,
                site_code=site_code,
                account_number=account_number,
                status=status,
                search=search,
                user=user,
            )
        raise


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
        return _json_or_502(resp)
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
        resp = _request_om(
            method="POST",
            path="/tickets",
            user=user,
            json_body=body,
        )
        _raise_for_upstream_error(resp)
        return _json_or_502(resp)
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
        resp = _request_om(
            method="PATCH",
            path=f"/tickets/{ticket_ref}",
            user=user,
            json_body=body,
        )
        _raise_for_upstream_error(resp)
        return _json_or_502(resp)
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
    resp = _request_om(
        method="POST",
        path=f"/tickets/{ticket_ref}/comments",
        user=user,
        json_body=body,
    )
    _raise_for_upstream_error(resp)
    return _json_or_502(resp)


@router.get("/export")
def export_om_tickets(
    site_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    quarter: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    try:
        params = {
            "site_code": site_code,
            "status": status,
            "quarter": quarter,
        }
        resp = _request_om(
            method="GET",
            path="/tickets/export",
            user=user,
            params=params,
            stream=True,
        )
        _raise_for_upstream_error(resp)
        content_type = resp.headers.get(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        disposition = resp.headers.get(
            "Content-Disposition",
            "attachment; filename=Maintenance_Log.xlsx",
        )
        return StreamingResponse(
            resp.iter_content(chunk_size=64 * 1024),
            media_type=content_type,
            headers={"Content-Disposition": disposition},
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
    resp = _request_om(method="GET", path="/health", user=user)
    if not resp.ok:
        return Response(status_code=502)
    return {"status": "ok", "upstream": OM_TICKETS_BASE_URL}

