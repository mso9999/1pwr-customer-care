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
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/site-sync", tags=["site-sync"])

SITE_CODE_RE = re.compile(r"^[A-Z]{3}$")

# PR emits ISO-3 from its org map; CC lanes are keyed by the internal
# ISO-2-ish codes used in country_config (BN for Benin, not BJ).
ISO3_TO_LANE = {"LSO": "LS", "ZMB": "ZM", "BEN": "BN"}


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


@router.post("/ingest")
def ingest_site_event(event: SiteEventIn, x_api_key: Optional[str] = Header(None)):
    if not _authorized(x_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    from country_config import COUNTRY, reset_live_site_cache

    site = event.site
    org = site.organizationId.strip().lower()
    code = site.code.strip().upper()

    if not org.startswith("1pwr_"):
        return _ignored("non_operating_org", event)
    if not SITE_CODE_RE.match(code):
        return _ignored("code_not_three_letters", event)
    lane = ISO3_TO_LANE.get(site.countryCode.strip().upper(), "")
    if lane != COUNTRY.code:
        return _ignored("other_lane", event)

    from customer_api import get_connection

    ugp_ids = [p.ugpProjectId for p in site.ugpProjects if p.ugpProjectId]
    canonical_ugp = (site.canonicalUgpProjectId or "").strip() or None
    name = site.name.strip()
    district = (site.district or "").strip() or None

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
                "VALUES (%s, %s, %s, %s, FALSE, 'pr', 'pr-site-sync', %s, %s) "
                "ON CONFLICT (country_code, code) DO NOTHING",
                (COUNTRY.code, code, name, district, json.dumps(ugp_ids), canonical_ugp),
            )
            action = "staged"
        conn.commit()

    reset_live_site_cache()
    logger.info("site-sync %s %s:%s (ugp=%s)", action, COUNTRY.code, code, canonical_ugp or ugp_ids)
    return {"ok": True, "applied": True, "action": action}
