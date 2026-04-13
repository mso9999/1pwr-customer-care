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
