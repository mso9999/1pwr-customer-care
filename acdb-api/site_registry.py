"""Lane-local activation and retirement of canonical deployment sites.

Sites are born in PR (Nexus master list, ``referenceData_sites``) and reach
each CC lane through ``site_sync_ingest`` (push + nightly pull), staged
inactive. CC never creates site codes; this router (Nexus
``manage_site_registry`` action, level C) lists the lane roster and lets
Engineering/IS&T activate a site at commissioning or retire it.

Safety invariants:

* Codes are exactly three uppercase letters (``MAK``, ``GBO``, …), globally
  unique and never reused: gateway thing names (``<SITE>-GW-####``) and
  customer account suffixes bind to them for life. Retiring deactivates; it
  never deletes.
* Rows are pinned to the serving lane's ``COUNTRY_CODE`` so one country's
  admin can never edit another country's roster.
* Static ``country_config`` entries always win on conflict.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from middleware import require_action
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/country-sites", tags=["admin", "site-registry"])

CC_SITE_REGISTRY_GATE = require_action(
    "manage_site_registry",
    system="cc",
    action="activate and retire canonical deployment site codes",
    required_level="C",
    fallback_roles=(CCRole.superadmin, CCRole.engineering),
)


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
