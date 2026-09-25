"""PR → CC canonical site sync receiver.

Sites are born in PR (pre-survey spend stage).  PR's ``fanoutSiteChanges``
Firestore trigger POSTs a canonical site event to every CC lane on each
``referenceData_sites`` write.  This endpoint is the lane-local receiver:

* Authenticated by a shared ``X-API-Key`` (``CC_SITE_SYNC_API_KEY`` env) —
  machine caller, no user session.
* Lane self-filter: PR fans out to every lane URL; a lane only accepts
  payloads whose country maps to its own ``COUNTRY.code``.  Everything else
  is a logged 202, never an error (PR retries non-2xx).
* Only operating-company orgs (``1pwr_*``) flow to CC — partner-org mirrors
  (mgb, pueco, …) of the same physical site would double-register codes.
* New sites land **staged inactive**: the code is reserved but the site is
  not selectable for provisioning/onboarding until someone with
  ``manage_site_registry`` activates it locally at commissioning time.
* Code-defined (static ``country_config``) sites always win — a PR event can
  refresh their uGP association metadata but never renames or retires them.
* ``site.deactivated`` retires the row; it never deletes (thing names and
  account suffixes bind to the code for life).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from middleware import require_action
from models import CCRole, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/site-sync", tags=["site-sync"])

SITE_CODE_RE = re.compile(r"^[A-Z]{3}$")
REGISTRY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,40}$")

RECONCILE_GATE = require_action(
    "manage_site_registry",
    system="cc",
    action="reconcile canonical sites from the PR / Nexus master list",
    required_level="C",
    fallback_roles=(CCRole.superadmin, CCRole.engineering),
)

DEFAULT_PR_CATALOG_SITES_URL = (
    "https://us-central1-pr-system-4ea55.cloudfunctions.net/prCatalogApi/api/sites"
)

# PR emits ISO-3 from its org map; CC lanes are keyed by the internal
# ISO-2-ish codes used in country_config (BN for Benin, not BJ).
ISO3_TO_LANE = {
    "LSO": "LS",
    "LS": "LS",
    "ZMB": "ZM",
    "ZM": "ZM",
    "BEN": "BN",
    "BN": "BN",
    "BJ": "BN",
}


class UgpProjectLinkIn(BaseModel):
    ugpProjectId: str
    ugpProjectCode: Optional[str] = None
    ugpProjectName: Optional[str] = None


class SitePayloadIn(BaseModel):
    organizationId: str
    countryCode: str = ""
    code: str
    name: str
    active: bool = True
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    district: Optional[str] = None
    ugpProjects: list[UgpProjectLinkIn] = Field(default_factory=list)
    canonicalUgpProjectId: Optional[str] = None
    createdBy: Optional[str] = None
    createdAt: Optional[str] = None


class SiteEventIn(BaseModel):
    source: str = "pr_admin"
    eventType: str  # site.created | site.updated | site.deactivated
    site: SitePayloadIn
    idempotencyKey: str = ""
    updatedAt: str = ""


def _expected_key() -> str:
    return os.environ.get("CC_SITE_SYNC_API_KEY", "").strip()


def _authorized(x_api_key: Optional[str]) -> bool:
    expected = _expected_key()
    if not expected:
        return False
    return hmac.compare_digest((x_api_key or "").strip(), expected)


def _ignored(reason: str, event: SiteEventIn) -> dict:
    logger.info("site-sync ignored (%s): %s/%s", reason, event.site.organizationId, event.site.code)
    return {"ok": True, "applied": False, "reason": reason}


def ugp_registry_key(site: SitePayloadIn, code: str) -> tuple[str, bool]:
    """Key ``load_project`` accepts: CODE, CODE_minigrid, or a named design.

    Returns ``(key, explicit)``. ``explicit`` is True when PR supplied a
    canonical / linked uGrid key; otherwise the 3-letter site code is a
    fallback (SIN loads SIN / SIN_minigrid).
    """
    candidates = []
    if site.canonicalUgpProjectId:
        candidates.append(site.canonicalUgpProjectId.strip())
    for link in site.ugpProjects:
        if link.ugpProjectCode:
            candidates.append(link.ugpProjectCode.strip())
        if link.ugpProjectId:
            candidates.append(link.ugpProjectId.strip())
    for raw in candidates:
        if raw and REGISTRY_KEY_RE.match(raw):
            return raw, True
    return code, False


def upsert_cc_site_project(code: str, name: str, registry_key: str, explicit: bool = True) -> None:
    """Keep the New Customer / Sync picker in step with the PR master list.

    Only an explicit PR uGrid link may replace an existing mapping. The
    bare-code fallback fills gaps but never overwrites curated keys such as
    LS NKU → NKA or LSB → Lesobeng (PR sites carry no uGP link yet).
    """
    if not code or not registry_key:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    alias = (name or "").strip().upper().replace(" ", "")
    from db_auth import get_auth_db

    if not explicit:
        with get_auth_db() as conn:
            existing = conn.execute(
                "SELECT project_id FROM cc_site_projects WHERE site_code = ?", (code,)
            ).fetchone()
        if existing and existing[0]:
            registry_key = existing[0]
    rows = [(code, registry_key, name or code, now)]
    if alias and alias != code and alias.isalnum() and 3 <= len(alias) <= 16:
        rows.append((alias, registry_key, name or code, now))
    on_conflict = (
        """DO UPDATE SET
             project_id = excluded.project_id,
             site_name = excluded.site_name,
             updated_at = excluded.updated_at"""
        if explicit
        else "DO NOTHING"
    )
    with get_auth_db() as conn:
        for site_code, project_id, site_name, updated_at in rows:
            conn.execute(
                f"""INSERT INTO cc_site_projects (site_code, project_id, site_name, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(site_code) {on_conflict}""",
                (site_code, project_id, site_name, updated_at),
            )


def apply_site_event(event: SiteEventIn) -> dict:
    """Apply a canonical PR/uGP site event to this lane. Used by push and pull."""
    from country_config import COUNTRY, reset_live_site_cache

    site = event.site
    org = site.organizationId.strip().lower()
    code = site.code.strip().upper()

    if not org.startswith("1pwr_"):
        return _ignored("non_operating_org", event)
    if not SITE_CODE_RE.match(code):
        return _ignored("code_not_three_letters", event)
    lane = ISO3_TO_LANE.get(site.countryCode.strip().upper(), "")
    if not lane:
        lane = {
            "1pwr_lesotho": "LS",
            "1pwr_benin": "BN",
            "1pwr_zambia": "ZM",
        }.get(org, "")
    if lane != COUNTRY.code:
        return _ignored("other_lane", event)

    from customer_api import get_connection

    ugp_ids = [p.ugpProjectId for p in site.ugpProjects if p.ugpProjectId]
    canonical_ugp = (site.canonicalUgpProjectId or "").strip() or None
    name = site.name.strip()
    district = (site.district or "").strip() or None
    registry_key, explicit_ugp = ugp_registry_key(site, code)


    with get_connection() as conn:
        cur = conn.cursor()

        if event.idempotencyKey:
            cur.execute(
                "INSERT INTO site_sync_events (idempotency_key, event_type, site_code, organization_id) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
                (event.idempotencyKey, event.eventType, code, org),
            )
            if cur.rowcount == 0:
                conn.commit()
                return _ignored("duplicate_delivery", event)

        cur.execute(
            "SELECT active, source FROM country_sites WHERE country_code = %s AND code = %s",
            (COUNTRY.code, code),
        )
        row = cur.fetchone()

        if event.eventType == "site.deactivated":
            if row:
                cur.execute(
                    "UPDATE country_sites SET active = FALSE, retired_by = 'pr-site-sync', "
                    "retired_at = now(), updated_at = now() "
                    "WHERE country_code = %s AND code = %s",
                    (COUNTRY.code, code),
                )
            conn.commit()
            reset_live_site_cache()
            logger.info("site-sync deactivated %s:%s", COUNTRY.code, code)
            return {"ok": True, "applied": True, "action": "deactivated"}

        if row:
            # Update identity + uGP association; preserve the lane-local
            # activation state (PR does not decide when CC commissions).
            cur.execute(
                "UPDATE country_sites SET name = %s, district = %s, ugp_project_ids = %s, "
                "canonical_ugp_project_id = %s, updated_at = now() "
                "WHERE country_code = %s AND code = %s",
                (name, district, json.dumps(ugp_ids), canonical_ugp, COUNTRY.code, code),
            )
            action = "updated"
        else:
            # Staged inactive — activated locally at commissioning time.
            cur.execute(
                "INSERT INTO country_sites (country_code, code, name, district, active, source, "
                "created_by, ugp_project_ids, canonical_ugp_project_id) "
                "VALUES (%s, %s, %s, %s, FALSE, 'pr', %s, %s, %s) "
                "ON CONFLICT (country_code, code) DO NOTHING",
                (COUNTRY.code, code, name, district,
                 site.createdBy or 'pr-site-sync', json.dumps(ugp_ids), canonical_ugp),
            )
            action = "staged"
        conn.commit()

    try:
        upsert_cc_site_project(code, name, registry_key, explicit_ugp)
    except Exception:
        logger.exception("site-sync cc_site_projects upsert failed for %s:%s", COUNTRY.code, code)

    reset_live_site_cache()
    logger.info("site-sync %s %s:%s (ugp=%s)", action, COUNTRY.code, code, registry_key)
    return {"ok": True, "applied": True, "action": action}


@router.post("/ingest")
def ingest_site_event(event: SiteEventIn, x_api_key: Optional[str] = Header(None)):
    if not _authorized(x_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return apply_site_event(event)


def _pr_catalog_sites() -> list[dict]:
    url = os.environ.get("PR_CATALOG_SITES_URL", DEFAULT_PR_CATALOG_SITES_URL).strip()
    key = (
        os.environ.get("PR_CATALOG_API_KEY", "").strip()
        or os.environ.get("CC_SITE_SYNC_API_KEY", "").strip()
    )
    if not key:
        raise HTTPException(
            status_code=503,
            detail="PR_CATALOG_API_KEY is not set; cannot pull the Nexus / PR master site list.",
        )
    resp = requests.get(url, headers={"X-API-Key": key}, timeout=45)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PR catalog sites failed ({resp.status_code}): {resp.text[:200]}",
        )
    data = resp.json()
    rows = data.get("sites") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="PR catalog sites payload was not a list")
    return rows


@router.post("/reconcile")
def reconcile_from_pr(user: CurrentUser = Depends(RECONCILE_GATE)):
    """Pull the PR / Nexus master site list and apply it to this lane.

    Push from ``fanoutSiteChanges`` is still the live path. This catch-up
    covers sites created in PR or uGP that never reached CC (missing
    coordinates on the fanout gate, empty CC endpoint list, or a missed
    delivery).
    """
    rows = _pr_catalog_sites()
    applied = []
    ignored = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        org = str(row.get("organizationId") or "").strip()
        code = str(row.get("code") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        country = str(row.get("countryCode") or "").strip().upper()
        active = row.get("active") is not False
        canonical = (row.get("canonicalUgpProjectId") or "") or None
        if isinstance(canonical, str):
            canonical = canonical.strip() or None
        event = SiteEventIn(
            source="pr_admin",
            eventType="site.created" if active else "site.deactivated",
            site=SitePayloadIn(
                organizationId=org or "unknown",
                countryCode=country,
                code=code or "XXX",
                name=name or code or "unknown",
                active=active,
                canonicalUgpProjectId=canonical,
                ugpProjects=(
                    [UgpProjectLinkIn(ugpProjectId=canonical)] if canonical else []
                ),
                createdBy="pr-catalog-reconcile",
            ),
            idempotencyKey="",
        )
        result = apply_site_event(event)
        entry = {"code": code, "name": name, **result}
        if result.get("applied"):
            applied.append(entry)
        else:
            ignored.append(entry)
    logger.info(
        "site-sync reconcile by %s: %d applied, %d ignored of %d catalog rows",
        getattr(user, "email", None) or user.user_id,
        len(applied),
        len(ignored),
        len(rows),
    )
    return {
        "ok": True,
        "catalog": len(rows),
        "applied": applied,
        "applied_count": len(applied),
        "ignored_count": len(ignored),
    }
