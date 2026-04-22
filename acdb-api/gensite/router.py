"""
FastAPI router for gensite commissioning and telemetry.

Routes (all under /api/gensite, role: employee for reads,
superadmin or onm_team for writes):

    GET  /api/gensite/vendors
         Vendor descriptors + credential schemas (drives the wizard).

    GET  /api/gensite/sites
         List of sites with last telemetry timestamp.

    GET  /api/gensite/sites/{code}
         Full site detail: equipment, credentials (masked), last readings.

    GET  /api/gensite/sites/{code}/live
         Most recent reading per equipment.

    POST /api/gensite/commission
         Commission-site wizard submit. Upserts site, equipment, credentials
         in one logical operation. Verifies each credential; records
         cc_mutations; optionally pushes Comm_Date to UGP powerhouse element.

    POST /api/gensite/sites/{code}/credentials/{vendor}/{backend}/verify
         Force a verify() round-trip against a stored credential. Does not
         mutate the secret.

    POST /api/gensite/sites/{code}/credentials/{vendor}/{backend}/rotate
         Overwrite secret/api_key in place; re-verify.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware import require_employee
from models import CCRole, CurrentUser

from .adapters import REGISTRY
from .adapters.base import AdapterError, SiteCredential
from .crypto import CredentialCryptoError, key_is_configured
from . import store

logger = logging.getLogger("cc-api.gensite")

router = APIRouter(prefix="/api/gensite", tags=["gensite"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_write_role(user: CurrentUser) -> None:
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(
            status_code=403,
            detail="Gensite writes require superadmin or onm_team role.",
        )


def _resolve_adapter(vendor: str):
    adapter = REGISTRY.get(vendor.lower())
    if not adapter:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown vendor '{vendor}'. Known: {sorted(REGISTRY.keys())}",
        )
    return adapter


def _require_crypto():
    if not key_is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Credential encryption is not configured on this host. "
                "Set CC_CREDENTIAL_ENCRYPTION_KEY in /opt/1pdb/.env. "
                "See docs/ops/gensite-credentials.md."
            ),
        )


def _try_log_mutation(user: CurrentUser, action: str, table: str, record_id: str,
                      new_values: Optional[Dict[str, Any]] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> None:
    """Best-effort cc_mutations write — import lazily so tests without the
    mutations module still boot."""
    try:
        from mutations import try_log_mutation
        try_log_mutation(
            user, action, table, record_id,
            new_values=new_values, metadata=metadata,
        )
    except Exception as exc:
        logger.warning("cc_mutations audit skipped: %s", exc)


# ---------------------------------------------------------------------------
# GET /vendors
# ---------------------------------------------------------------------------

@router.get("/vendors")
def get_vendors(user: CurrentUser = Depends(require_employee)) -> Dict[str, Any]:
    """Return vendor descriptors + credential schemas for the commission wizard."""
    result: List[Dict[str, Any]] = []
    seen: set = set()
    for key, adapter in REGISTRY.items():
        if adapter.vendor in seen:
            continue
        seen.add(adapter.vendor)
        result.append({
            "vendor": adapter.vendor,
            "display_name": adapter.display_name,
            "implementation_status": adapter.implementation_status,
            "credential_specs": [
                {
                    "vendor": spec.vendor,
                    "backend": spec.backend,
                    "label": spec.label,
                    "plain_fields": spec.plain_fields,
                    "secret_fields": spec.secret_fields,
                    "extra_fields": spec.extra_fields,
                    "docs_url": spec.docs_url,
                    "notes": spec.notes,
                }
                for spec in adapter.credential_specs()
            ],
        })
    return {
        "vendors": sorted(result, key=lambda v: v["display_name"]),
        "crypto_configured": key_is_configured(),
    }


# ---------------------------------------------------------------------------
# GET /sites
# ---------------------------------------------------------------------------

@router.get("/sites")
def list_sites(
    country: Optional[str] = None,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    try:
        store.seed_sites_from_country_config()
    except Exception as exc:
        # Seeding failure must not break the list endpoint.
        logger.warning("seed_sites_from_country_config failed: %s", exc)

    sites = store.list_sites(country=country)
    # Enrich with last reading ts per site (cheap: one query total).
    from customer_api import get_connection
    ts_by_site: Dict[str, Optional[datetime]] = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT site_code, MAX(ts_utc)
                    FROM inverter_readings
                    GROUP BY site_code
                    """
                )
                for code, ts in cur.fetchall():
                    ts_by_site[code] = ts
    except Exception as exc:
        logger.warning("latest-reading enrichment failed: %s", exc)

    for s in sites:
        s["last_reading_ts"] = ts_by_site.get(s["code"])
    return {"sites": sites, "count": len(sites)}


# ---------------------------------------------------------------------------
# GET /sites/{code}
# ---------------------------------------------------------------------------

@router.get("/sites/{code}")
def get_site_detail(
    code: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    site = store.get_site(code)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{code}' not found.")
    equipment = store.list_equipment(code, include_decommissioned=False)
    credentials = store.list_credentials(code)
    live = store.latest_readings_for_site(code)
    return {
        "site": site,
        "equipment": equipment,
        "credentials": credentials,
        "latest_readings": live,
    }


# ---------------------------------------------------------------------------
# GET /sites/{code}/live
# ---------------------------------------------------------------------------

@router.get("/sites/{code}/live")
def get_live(
    code: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    return {"site_code": code.upper(), "readings": store.latest_readings_for_site(code)}


# ---------------------------------------------------------------------------
# POST /commission
# ---------------------------------------------------------------------------

class EquipmentInput(BaseModel):
    kind: str = Field(..., description="inverter | bms | pv_meter | load_meter | battery | scada | other")
    vendor: str
    model: Optional[str] = None
    serial: Optional[str] = None
    role: Optional[str] = None
    nameplate_kw: Optional[float] = None
    nameplate_kwh: Optional[float] = None
    firmware_version: Optional[str] = None
    notes: Optional[str] = None


class CredentialInput(BaseModel):
    vendor: str
    backend: str
    base_url: Optional[str] = None
    username: Optional[str] = None
    secret: Optional[str] = None         # plaintext in transit only; encrypted at rest
    api_key: Optional[str] = None
    site_id_on_vendor: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class CommissionRequest(BaseModel):
    site_code: str
    country: str = Field(..., description="ISO code: LS | BN | ZM")
    kind: str = Field("minigrid", description="minigrid | health_center | other")
    display_name: str
    district: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    ugp_project_id: Optional[str] = None
    commissioned_at: Optional[datetime] = None
    notes: Optional[str] = None
    equipment: List[EquipmentInput] = Field(default_factory=list)
    credentials: List[CredentialInput] = Field(default_factory=list)


@router.post("/commission")
def commission_site(
    req: CommissionRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    _require_crypto()

    if not req.equipment:
        raise HTTPException(status_code=400, detail="At least one piece of equipment is required.")

    # ---- Phase 1: site upsert ----
    site = store.upsert_site(
        code=req.site_code,
        country=req.country,
        kind=req.kind,
        display_name=req.display_name,
        district=req.district,
        gps_lat=req.gps_lat,
        gps_lon=req.gps_lon,
        ugp_project_id=req.ugp_project_id,
        notes=req.notes,
        commissioned_at=req.commissioned_at or datetime.now(timezone.utc),
    )
    _try_log_mutation(
        user, "upsert", "sites", site["code"],
        new_values={k: site.get(k) for k in ("code", "country", "kind", "display_name")},
        metadata={"kind": "site_commission"},
    )

    # ---- Phase 2: equipment upserts ----
    installed: List[Dict[str, Any]] = []
    for eq in req.equipment:
        if eq.vendor.lower() not in REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown vendor in equipment: {eq.vendor}",
            )
        row = store.insert_equipment(
            site_code=req.site_code,
            kind=eq.kind,
            vendor=eq.vendor.lower(),
            model=eq.model,
            serial=eq.serial,
            role=eq.role,
            nameplate_kw=eq.nameplate_kw,
            nameplate_kwh=eq.nameplate_kwh,
            firmware_version=eq.firmware_version,
            commissioned_at=req.commissioned_at or datetime.now(timezone.utc),
            installed_by=user.user_id,
            notes=eq.notes,
        )
        _try_log_mutation(
            user, "create", "site_equipment", str(row["id"]),
            new_values={"vendor": row["vendor"], "kind": row["kind"], "serial": row.get("serial")},
            metadata={"kind": "site_commission", "site_code": req.site_code},
        )
        installed.append(row)

    # ---- Phase 3: credential upserts + verify() ----
    credential_results: List[Dict[str, Any]] = []
    for cred_in in req.credentials:
        adapter = _resolve_adapter(cred_in.vendor)

        # Run verify() first; store the result alongside the ciphertext.
        cred_for_verify = SiteCredential(
            site_code=req.site_code.upper(),
            vendor=cred_in.vendor.lower(),
            backend=cred_in.backend.lower(),
            base_url=cred_in.base_url,
            username=cred_in.username,
            secret=cred_in.secret,
            api_key=cred_in.api_key,
            site_id_on_vendor=cred_in.site_id_on_vendor,
            extra=cred_in.extra or {},
        )

        verify_ok: bool
        verify_msg: str
        discovered_site_id: Optional[str] = None
        try:
            vr = adapter.verify(cred_for_verify)
            verify_ok = vr.ok
            verify_msg = vr.message
            discovered_site_id = vr.discovered_site_id
        except AdapterError as exc:
            verify_ok = False
            verify_msg = f"adapter error: {exc}"
        except Exception as exc:  # pragma: no cover — defensive
            verify_ok = False
            verify_msg = f"unexpected: {exc}"

        # Encrypt + store regardless of verify result — operators may need
        # to diagnose from the dashboard, and re-verify is cheap.
        try:
            stored = store.upsert_credential(
                site_code=req.site_code,
                vendor=cred_in.vendor.lower(),
                backend=cred_in.backend.lower(),
                base_url=cred_in.base_url,
                username=cred_in.username,
                secret=cred_in.secret,
                api_key=cred_in.api_key,
                site_id_on_vendor=discovered_site_id or cred_in.site_id_on_vendor,
                extra=cred_in.extra or {},
                created_by=user.user_id,
                verify_ok=verify_ok,
                verify_error=None if verify_ok else verify_msg,
            )
        except CredentialCryptoError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        _try_log_mutation(
            user, "create", "site_credentials", str(stored["id"]),
            new_values={
                "vendor": stored["vendor"],
                "backend": stored["backend"],
                "has_secret": stored.get("has_secret"),
                "has_api_key": stored.get("has_api_key"),
                "last_verified_ok": stored.get("last_verified_ok"),
            },
            metadata={"kind": "site_commission", "site_code": req.site_code},
        )
        credential_results.append({
            "credential": stored,
            "verify": {"ok": verify_ok, "message": verify_msg},
        })

    return {
        "site": site,
        "equipment": installed,
        "credentials": credential_results,
    }


# ---------------------------------------------------------------------------
# POST /sites/{code}/credentials/{vendor}/{backend}/verify
# ---------------------------------------------------------------------------

@router.post("/sites/{code}/credentials/{vendor}/{backend}/verify")
def verify_credential(
    code: str,
    vendor: str,
    backend: str,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_crypto()
    adapter = _resolve_adapter(vendor)

    try:
        cred = store.load_credential_for_adapter(code, vendor.lower(), backend.lower())
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found.")

    try:
        vr = adapter.verify(cred)
    except AdapterError as exc:
        vr = type("VR", (), {"ok": False, "message": str(exc), "discovered_site_id": None, "discovered_equipment": []})()

    # Persist latest verify result. Look up the credential id first.
    from customer_api import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM site_credentials "
                "WHERE site_code = %s AND vendor = %s AND backend = %s",
                (code.upper(), vendor.lower(), backend.lower()),
            )
            row = cur.fetchone()
    if row:
        store.update_credential_verify_result(
            row[0],
            ok=vr.ok,
            error=None if vr.ok else vr.message,
            discovered_site_id=getattr(vr, "discovered_site_id", None),
        )

    return {
        "site_code": code.upper(),
        "vendor": vendor.lower(),
        "backend": backend.lower(),
        "ok": vr.ok,
        "message": vr.message,
        "discovered_site_id": getattr(vr, "discovered_site_id", None),
        "discovered_equipment": getattr(vr, "discovered_equipment", []),
    }


# ---------------------------------------------------------------------------
# POST /sites/{code}/credentials/{vendor}/{backend}/rotate
# ---------------------------------------------------------------------------

class RotateCredentialRequest(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    secret: Optional[str] = None
    api_key: Optional[str] = None
    site_id_on_vendor: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


@router.post("/sites/{code}/credentials/{vendor}/{backend}/rotate")
def rotate_credential(
    code: str,
    vendor: str,
    backend: str,
    req: RotateCredentialRequest,
    user: CurrentUser = Depends(require_employee),
) -> Dict[str, Any]:
    _require_write_role(user)
    _require_crypto()
    adapter = _resolve_adapter(vendor)

    # Verify the NEW credential before writing it.
    cred_for_verify = SiteCredential(
        site_code=code.upper(),
        vendor=vendor.lower(),
        backend=backend.lower(),
        base_url=req.base_url,
        username=req.username,
        secret=req.secret,
        api_key=req.api_key,
        site_id_on_vendor=req.site_id_on_vendor,
        extra=req.extra or {},
    )
    try:
        vr = adapter.verify(cred_for_verify)
    except AdapterError as exc:
        vr = type("VR", (), {"ok": False, "message": str(exc), "discovered_site_id": None})()

    try:
        stored = store.upsert_credential(
            site_code=code,
            vendor=vendor.lower(),
            backend=backend.lower(),
            base_url=req.base_url,
            username=req.username,
            secret=req.secret,
            api_key=req.api_key,
            site_id_on_vendor=getattr(vr, "discovered_site_id", None) or req.site_id_on_vendor,
            extra=req.extra or {},
            created_by=user.user_id,
            verify_ok=vr.ok,
            verify_error=None if vr.ok else vr.message,
        )
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    _try_log_mutation(
        user, "update", "site_credentials", str(stored["id"]),
        new_values={
            "vendor": stored["vendor"],
            "backend": stored["backend"],
            "has_secret": stored.get("has_secret"),
            "has_api_key": stored.get("has_api_key"),
            "last_verified_ok": stored.get("last_verified_ok"),
        },
        metadata={"kind": "site_credential_rotate", "site_code": code.upper()},
    )
    return {
        "credential": stored,
        "verify": {"ok": vr.ok, "message": vr.message},
    }

