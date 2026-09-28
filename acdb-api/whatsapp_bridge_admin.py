"""Admin status for this process's WhatsApp bridge.

GET /api/admin/whatsapp-bridge proxies the bridge ``GET /link-status``.
The bridge secret stays on the server.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends

from cc_bridge_notify import bridge_link_credentials
from country_config import COUNTRY
from middleware import effective_roles, raise_privilege_denied, require_employee
from models import CCRole, CurrentUser

logger = logging.getLogger("cc-api.whatsapp-bridge")

router = APIRouter(prefix="/api/admin/whatsapp-bridge", tags=["admin", "whatsapp-bridge"])

_BRIDGE_ADMIN_ROLES = (CCRole.superadmin.value, CCRole.onm_team.value)
SOP = "docs/whatsapp-customer-care.md"


def _require_bridge_admin(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
    if "administer_cc" in (user.privilege_actions or []):
        return user
    if set(effective_roles(user)).intersection(_BRIDGE_ADMIN_ROLES):
        return user
    raise_privilege_denied(user, _BRIDGE_ADMIN_ROLES, "view the WhatsApp bridge")
    return user


def _link_status_url(notify_url: str) -> str:
    if notify_url.endswith("/notify/"):
        return notify_url[: -len("/notify/")] + "/link-status"
    if notify_url.endswith("/notify"):
        return notify_url[: -len("/notify")] + "/link-status"
    return notify_url.rstrip("/") + "/link-status"


@router.get("")
def whatsapp_bridge_status(user: CurrentUser = Depends(_require_bridge_admin)):
    code = COUNTRY.code.upper()
    url, secret = bridge_link_credentials(code)
    base = {
        "configured": bool(url and secret),
        "country_code": code,
        "linked": None,
        "qr": None,
        "pairing_code": None,
        "tracker_group": None,
        "sop": SOP,
    }
    if not url or not secret:
        return base
    req = urllib.request.Request(
        _link_status_url(url),
        headers={"X-Bridge-Secret": secret},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("whatsapp bridge link-status failed country=%s: %s", code, exc)
        return {**base, "reachable": False, "detail": "bridge link-status unavailable"}
    return {
        **base,
        "reachable": True,
        "linked": bool(payload.get("linked")),
        "qr": payload.get("qr") or None,
        "pairing_code": payload.get("pairing_code") or None,
        "tracker_group": payload.get("tracker_group") or None,
    }
