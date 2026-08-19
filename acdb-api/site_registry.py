"""UI-managed canonical site registry.

Adding a deployment site used to require editing ``country_config.py`` and
redeploying the country lane — which meant only a developer could do it and
country teams were blocked on Engineering/IS&T having repo access.  This
router exposes a claim-gated admin API (Nexus ``manage_site_registry``
action, level C — held by Engineering and IS&T) so the same people who
commission hardware can create the canonical site code from the CC admin UI.

Safety invariants ported from the code-defined roster:

* Codes are exactly three uppercase letters (``MAK``, ``GBO``, …).
* A code is globally unique across countries and is never reused: gateway
  thing names (``<SITE>-GW-####``) and customer account suffixes bind to it
  for life.  Retiring deactivates; it never deletes.
* Rows are pinned to the serving lane's ``COUNTRY_CODE`` so one country's
  admin can never create or edit another country's roster.
* Static ``country_config`` entries always win on conflict — the UI can add
  sites but can never shadow or rename a code-defined site.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware import require_action
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/country-sites", tags=["admin", "site-registry"])

SITE_CODE_RE = re.compile(r"^[A-Z]{3}$")

CC_SITE_REGISTRY_GATE = require_action(
    "manage_site_registry",
    system="cc",
    action="create and retire canonical deployment site codes",
    required_level="C",
    fallback_roles=(CCRole.superadmin, CCRole.engineering),
)


class SiteCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=3)
    name: str = Field(..., min_length=2, max_length=120)
    district: Optional[str] = Field(None, max_length=120)


class SiteUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    district: Optional[str] = Field(None, max_length=120)
    active: Optional[bool] = None
    # Required True when activating a site that has no canonical uGP design
    # linked — the explicit "someone missed a step" acknowledgement.
    confirm_missing_ugp_link: Optional[bool] = None


def _lane_country() -> str:
    from country_config import COUNTRY

    return COUNTRY.code


def _config_codes() -> dict[str, str]:
    from country_config import ALL_SITE_ABBREV

    return dict(ALL_SITE_ABBREV)


def _row_to_dict(row, *, source: str) -> dict:
    ugp_ids = row[11] if len(row) > 11 else None
    return {
        "country_code": row[0],
        "code": row[1],
        "name": row[2],
        "district": row[3],
        "active": bool(row[4]),
        "source": source,
        "created_by": row[5],
        "created_at": row[6].isoformat() if row[6] else None,
        "updated_at": row[7].isoformat() if row[7] else None,
        "retired_by": row[8],
        "retired_at": row[9].isoformat() if row[9] else None,
        "ugp_project_ids": list(ugp_ids) if ugp_ids else [],
        "canonical_ugp_project_id": row[12] if len(row) > 12 else None,
    }


@router.get("")
def list_country_sites(user: CurrentUser = Depends(CC_SITE_REGISTRY_GATE)):
    """Merged roster for this lane: code-defined seeds plus UI-managed rows."""
    from country_config import COUNTRY

    out: dict[str, dict] = {}
    for code, name in sorted(COUNTRY.site_abbrev.items()):
        out[code] = {
            "country_code": COUNTRY.code,
            "code": code,
            "name": name,
            "district": COUNTRY.site_districts.get(code),
            "active": True,
            "source": "config",
            "created_by": None,
            "created_at": None,
            "updated_at": None,
            "retired_by": None,
            "retired_at": None,
            "ugp_project_ids": [],
            "canonical_ugp_project_id": None,
        }

    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT country_code, code, name, district, active, created_by, "
            "       created_at, updated_at, retired_by, retired_at, source, "
            "       ugp_project_ids, canonical_ugp_project_id "
            "FROM country_sites WHERE country_code = %s ORDER BY code",
            (COUNTRY.code,),
        )
        for row in cur.fetchall():
            # Static config wins on identity conflict — the UI may never
            # shadow a code-defined site.  The DB row's uGP association
            # metadata still overlays so legacy sites show the design link.
            if row[1] in out:
                out[row[1]]["ugp_project_ids"] = list(row[11]) if row[11] else []
                out[row[1]]["canonical_ugp_project_id"] = row[12]
                continue
            out[row[1]] = _row_to_dict(row, source=row[10] or "ui")

    return {"country_code": COUNTRY.code, "sites": sorted(out.values(), key=lambda s: s["code"])}


@router.post("", status_code=201)
def create_country_site(payload: SiteCreate, user: CurrentUser = Depends(CC_SITE_REGISTRY_GATE)):
    """Emergency-only local creation.  Sites are born in PR (pre-survey
    spend) and arrive here via the site-sync fanout staged inactive; this
    path exists for superadmin break-glass situations only."""
    if user.role != CCRole.superadmin:
        raise HTTPException(
            status_code=403,
            detail="Sites are created in PR (Admin → Reference Data → Sites) and sync here automatically. "
                   "Local creation is a superadmin-only emergency path.",
        )
    from country_config import reset_live_site_cache

    code = payload.code.strip().upper()
    name = payload.name.strip()
    district = (payload.district or "").strip() or None
    country = _lane_country()

    if not SITE_CODE_RE.match(code):
        raise HTTPException(
            status_code=400,
            detail=f"Site code '{code}' must be exactly three uppercase letters (e.g. 'CHI').",
        )

    config_codes = _config_codes()
    if code in config_codes:
        raise HTTPException(
            status_code=409,
            detail=f"'{code}' is already a code-defined site ({config_codes[code]}). "
                   "Code-defined sites cannot be shadowed from the UI.",
        )

    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        # Global uniqueness across every country, active or retired — a
        # retired code stays reserved forever (thing names bind for life).
        cur.execute("SELECT country_code, active FROM country_sites WHERE code = %s", (code,))
        existing = cur.fetchall()
        if any(bool(r[1]) for r in existing):
            raise HTTPException(status_code=409, detail=f"Site code '{code}' is already in use.")
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Site code '{code}' was retired and stays reserved. "
                       "Reactivate the existing record instead of creating a new one.",
            )

        cur.execute(
            "INSERT INTO country_sites (country_code, code, name, district, active, source, created_by) "
            "VALUES (%s, %s, %s, %s, TRUE, 'ui', %s)",
            (country, code, name, district, user.email or user.user_id),
        )
        conn.commit()

    reset_live_site_cache()
    try_log_mutation(
        user,
        "insert",
        "country_sites",
        f"{country}:{code}",
        new_values={"country_code": country, "code": code, "name": name, "district": district},
        metadata={"kind": "country_site_create"},
    )
    logger.info("country site created: %s:%s (%s) by %s", country, code, name, user.email)
    return {"ok": True, "country_code": country, "code": code, "name": name, "district": district}


@router.patch("/{code}")
def update_country_site(code: str, payload: SiteUpdate, user: CurrentUser = Depends(CC_SITE_REGISTRY_GATE)):
    from country_config import reset_live_site_cache

    code = code.strip().upper()
    country = _lane_country()

    if code in _config_codes():
        raise HTTPException(
            status_code=409,
            detail=f"'{code}' is code-defined in country_config; edit it via the repo, not the UI.",
        )

    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT country_code, code, name, district, active, created_by, "
            "       created_at, updated_at, retired_by, retired_at, source, "
            "       ugp_project_ids, canonical_ugp_project_id "
            "FROM country_sites WHERE country_code = %s AND code = %s",
            (country, code),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Site '{code}' not found in {country}.")
        before = _row_to_dict(row, source=row[10] or "ui")

        # PR-sourced rows: identity (name/district) is canonical in PR and
        # syncs via fanout — only the lane-local activation state is editable.
        if before["source"] == "pr" and (
            (payload.name is not None and payload.name.strip() != before["name"])
            or (payload.district is not None and (payload.district.strip() or None) != before["district"])
        ):
            raise HTTPException(
                status_code=409,
                detail=f"'{code}' is managed in PR; edit its name/district there and the change syncs here. "
                       "Only activation is decided locally.",
            )

        new_name = payload.name.strip() if payload.name else before["name"]
        new_district = (
            payload.district.strip() or None
            if payload.district is not None
            else before["district"]
        )
        new_active = before["active"] if payload.active is None else bool(payload.active)

        if new_active == before["active"] and new_name == before["name"] and new_district == before["district"]:
            raise HTTPException(status_code=400, detail="No changes supplied.")

        if new_active and not before["active"]:
            # Activation at commissioning: the site should already carry its
            # canonical uGP design link (PR registry → fanout).  Missing link
            # means someone skipped the association step in uGP — require an
            # explicit acknowledgement and audit it.
            has_ugp_link = bool(before["canonical_ugp_project_id"] or before["ugp_project_ids"])
            if not has_ugp_link and not payload.confirm_missing_ugp_link:
                raise HTTPException(
                    status_code=409,
                    detail=f"Site '{code}' has no canonical uGP design linked. Remediation: open the "
                           "design in uGridPLAN and set its site association (or ask Engineering), then "
                           "retry. To activate without the link anyway, resubmit with "
                           "confirm_missing_ugp_link=true.",
                )
            # Reactivation: the global active-code index protects uniqueness.
            cur.execute(
                "UPDATE country_sites SET active = TRUE, retired_by = NULL, retired_at = NULL, "
                "name = %s, district = %s, updated_at = now() "
                "WHERE country_code = %s AND code = %s",
                (new_name, new_district, country, code),
            )
        elif not new_active and before["active"]:
            cur.execute(
                "UPDATE country_sites SET active = FALSE, retired_by = %s, retired_at = now(), "
                "name = %s, district = %s, updated_at = now() "
                "WHERE country_code = %s AND code = %s",
                (user.email or user.user_id, new_name, new_district, country, code),
            )
        else:
            cur.execute(
                "UPDATE country_sites SET name = %s, district = %s, updated_at = now() "
                "WHERE country_code = %s AND code = %s",
                (new_name, new_district, country, code),
            )
        conn.commit()

    reset_live_site_cache()
    after = {**before, "name": new_name, "district": new_district, "active": new_active}
    try_log_mutation(
        user,
        "update",
        "country_sites",
        f"{country}:{code}",
        old_values=before,
        new_values=after,
        metadata={
            "kind": "country_site_update",
            **({"activated_without_ugp_link": True}
               if new_active and not before["active"]
               and not (before["canonical_ugp_project_id"] or before["ugp_project_ids"])
               else {}),
        },
    )
    logger.info("country site updated: %s:%s by %s -> %s", country, code, user.email, after)
    return {"ok": True, **after}
