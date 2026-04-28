"""
Mobile app BFF (Backend-For-Frontend) endpoints.

Public, unauthenticated routes consumed by the 1PWR mobile app
(`1PWRBENIN-v2` / `mionwa`). Scoped under ``/api/app/*`` to keep the
mobile-facing contract visually separate from the CC web portal and
operational endpoints.

Contract is documented in ``1PWR CC/docs/app-bff-contract.md``.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Response

from country_config import _REGISTRY


router = APIRouter(prefix="/api/app", tags=["app-bff"])


# Registry of remote ``CountryConfig`` packs the mobile app should load
# instead of its bundled JSON asset for that country code. v1 is empty
# (rows omit ``appConfigUrl`` and the app uses bundled assets); see
# ``app-bff-contract.md`` for how to populate this once a remote pack
# is ready.
_REMOTE_CONFIG_URLS: Dict[str, str] = {}


def _row_for(code: str, cfg: Any) -> Dict[str, Any]:
    """Shape one country row to match `CountryRegistryClient` in the app."""
    row: Dict[str, Any] = {
        "countryCode": code,
        "displayName": getattr(cfg, "display_name", None) or cfg.name,
        "active": True,
    }
    url: Optional[str] = _REMOTE_CONFIG_URLS.get(code)
    if url:
        row["appConfigUrl"] = url
    return row


@router.get("/active-countries")
def active_countries(response: Response) -> Dict[str, List[Dict[str, Any]]]:
    """Return the list of countries the mobile app may select.

    Filters out rows where ``CountryConfig.active`` is False. The registry
    is static Python (see :mod:`country_config`) so the response only
    changes on deploy — cache aggressively.
    """
    rows: List[Dict[str, Any]] = []
    for code, cfg in _REGISTRY.items():
        if not getattr(cfg, "active", True):
            continue
        rows.append(_row_for(code, cfg))

    response.headers["Cache-Control"] = "public, max-age=300"
    return {"countries": rows}
