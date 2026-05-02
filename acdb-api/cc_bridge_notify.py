"""
POST JSON to the WhatsApp Customer Care bridge (same host as CC API or localhost).

Routing is **country-aware** so each country can use a separate bridge instance
(different phones / tracker groups). The active country comes from ``country_config``
(``COUNTRY_CODE``) unless overridden per call.

Env pattern (``CC`` = ISO country code, e.g. ``LS``, ``BN``, ``ZM``):
  CC_BRIDGE_NOTIFY_URL_CC — optional; per-country bridge URL (e.g. ``CC_BRIDGE_NOTIFY_URL_ZM``)
  CC_BRIDGE_SECRET_CC     — optional; per-country secret

Fallback for any country:
  CC_BRIDGE_NOTIFY_URL — used when no country-specific URL is set
  CC_BRIDGE_SECRET     — used when no country-specific secret is set

Lesotho hosts often set only the unsuffixed pair; ``CC_BRIDGE_NOTIFY_URL_LS`` is optional.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("cc-api.bridge-notify")


def bridge_credentials(country_code: str) -> tuple[str, str]:
    """Return (notify_url, secret) for WhatsApp bridge HTTP inbound for this country."""
    cc = (country_code or "LS").upper()
    url = os.environ.get(f"CC_BRIDGE_NOTIFY_URL_{cc}") or os.environ.get(
        "CC_BRIDGE_NOTIFY_URL", ""
    )
    secret = os.environ.get(f"CC_BRIDGE_SECRET_{cc}") or os.environ.get(
        "CC_BRIDGE_SECRET", ""
    )
    return url, secret


def notify_cc_bridge(
    payload: Dict[str, Any],
    *,
    country_code: Optional[str] = None,
) -> None:
    """POST payload to the bridge ``/notify`` for the given country (default: active ``COUNTRY``)."""
    from country_config import COUNTRY

    code = (country_code or COUNTRY.code).upper()
    merged = {**payload, "country_code": code}
    url, secret = bridge_credentials(code)
    if not url or not secret:
        logger.debug(
            "bridge_notify skipped: no URL/secret for country=%s (url set=%s secret set=%s)",
            code,
            bool(url),
            bool(secret),
        )
        return
    data = json.dumps(merged).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            logger.info("bridge_notify country=%s status=%s", code, resp.status)
    except urllib.error.URLError as e:
        logger.warning("bridge_notify country=%s failed: %s", code, e)


def _broadcast_url_for(country_code: str) -> tuple[str, str]:
    """Derive the ``/broadcast`` URL from the configured ``/notify`` URL for a country.

    The bridge serves both routes from the same HTTP listener (see
    ``whatsapp-bridge/whatsapp-customer-care.js`` ``startInboundHttpServer``),
    so we just swap the path. Returns ``("", "")`` if the bridge isn't
    configured for this country.
    """
    notify_url, secret = bridge_credentials(country_code)
    if not notify_url or not secret:
        return "", ""
    if notify_url.endswith("/notify/"):
        bcast_url = notify_url[: -len("/notify/")] + "/broadcast"
    elif notify_url.endswith("/notify"):
        bcast_url = notify_url[: -len("/notify")] + "/broadcast"
    else:
        # Caller configured a non-``/notify`` URL; assume it's already the
        # broadcast endpoint or close enough.
        bcast_url = notify_url
    return bcast_url, secret


def broadcast_to_bridge(
    text: str,
    *,
    country_code: Optional[str] = None,
    jid: Optional[str] = None,
) -> bool:
    """POST a **verbatim** text message to the bridge's ``/broadcast`` route.

    Unlike :func:`notify_cc_bridge`, the bridge does not decorate the text with
    "[App / meter relay]" / Country / Source headers -- ``text`` is delivered
    exactly as supplied. Used for the monthly staff-PIN broadcast and any
    other operator messages that need a clean look.

    Returns True on a 2xx response, False otherwise (logged at WARN).
    """
    from country_config import COUNTRY

    code = (country_code or COUNTRY.code).upper()
    if not text or not text.strip():
        logger.warning("broadcast_to_bridge: empty text, skipped (country=%s)", code)
        return False
    url, secret = _broadcast_url_for(code)
    if not url or not secret:
        logger.warning(
            "broadcast_to_bridge skipped: no URL/secret for country=%s "
            "(set CC_BRIDGE_NOTIFY_URL[_%s] and CC_BRIDGE_SECRET[_%s])",
            code, code, code,
        )
        return False
    body: Dict[str, Any] = {"text": text}
    if jid:
        body["jid"] = jid
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = 200 <= resp.status < 300
            logger.info("bridge_broadcast country=%s status=%s ok=%s", code, resp.status, ok)
            return ok
    except urllib.error.URLError as e:
        logger.warning("bridge_broadcast country=%s failed: %s", code, e)
        return False
