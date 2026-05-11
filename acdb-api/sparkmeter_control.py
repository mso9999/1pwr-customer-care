"""
SparkMeter relay control module — CC → SM meter disconnect/reconnect.

Pushes relay open/close commands to SparkMeter's billing systems so that
meters can be remotely disconnected for safety/maintenance reasons.

Platforms:
  - Koios v1 API  — POST /api/v1/meters/disconnect  (country-aware)
  - ThunderCloud v0 API — MAK/LAB on sparkcloud-u740425.sparkmeter.cloud

Multi-country support:
  Each country has its own Koios organisation and API key pair.
  Credentials are resolved by site code → country → env vars:
    KOIOS_WRITE_API_KEY_LS / KOIOS_WRITE_API_SECRET_LS  (Lesotho)
    KOIOS_WRITE_API_KEY_BN / KOIOS_WRITE_API_SECRET_BN  (Benin)
  Falls back to the un-suffixed KOIOS_WRITE_API_KEY / KOIOS_WRITE_API_SECRET.

Follows the same credential and site-routing patterns as sparkmeter_credit.py.
"""

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("cc-api.sm-control")

# ---------------------------------------------------------------------------
# Shared config (mirrors sparkmeter_credit.py)
# ---------------------------------------------------------------------------

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")

_GLOBAL_WRITE_KEY = os.environ.get(
    "KOIOS_WRITE_API_KEY",
    os.environ.get("KOIOS_API_KEY", ""),
)
_GLOBAL_WRITE_SECRET = os.environ.get(
    "KOIOS_WRITE_API_SECRET",
    os.environ.get("KOIOS_API_SECRET", ""),
)

TC_API_BASE = os.environ.get(
    "TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud"
)
TC_AUTH_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
TC_USERNAME = os.environ.get("THUNDERCLOUD_USERNAME", "")
TC_PASSWORD = os.environ.get("THUNDERCLOUD_PASSWORD", "")

THUNDERCLOUD_SITES = {"MAK", "LAB"}

API_TIMEOUT = 30  # shorter than credit timeout — UI-driven

_tc_token_lock = threading.Lock()
_tc_session_token: Optional[str] = None


# ---------------------------------------------------------------------------
# Country → credentials map
# ---------------------------------------------------------------------------

def _build_site_country_map() -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    from country_config import BENIN, LESOTHO, _REGISTRY

    site_to_country: Dict[str, str] = {}
    for cc, cfg in _REGISTRY.items():
        for site in cfg.site_abbrev:
            site_to_country[site] = cc

    country_creds: Dict[str, Tuple[str, str]] = {}
    for cc in _REGISTRY:
        key = os.environ.get(
            f"KOIOS_WRITE_API_KEY_{cc}", _GLOBAL_WRITE_KEY,
        )
        secret = os.environ.get(
            f"KOIOS_WRITE_API_SECRET_{cc}", _GLOBAL_WRITE_SECRET,
        )
        country_creds[cc] = (key, secret)

    return site_to_country, country_creds


_site_to_country, _country_creds = _build_site_country_map()


def _extract_site(account_number: str) -> str:
    m = re.search(r"([A-Z]{3})$", (account_number or "").upper())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ControlResult:
    success: bool
    platform: str       # "koios", "thundercloud", or "unknown"
    action: str         # "disconnect" or "reconnect"
    error: Optional[str] = None
    raw_response: Optional[dict] = None


# ---------------------------------------------------------------------------
# ThunderCloud helpers
# ---------------------------------------------------------------------------

def _tc_login() -> Optional[str]:
    if not TC_USERNAME or not TC_PASSWORD:
        return None
    try:
        r = requests.post(
            f"{TC_API_BASE}/login",
            data={"email": TC_USERNAME, "password": TC_PASSWORD},
            allow_redirects=False,
            timeout=API_TIMEOUT,
        )
        cookies = r.cookies
        token = cookies.get("session") or cookies.get("remember_token")
        if token:
            return token
        auth_header = r.headers.get("Authentication-Token")
        if auth_header:
            return auth_header
        set_cookie = r.headers.get("Set-Cookie", "")
        if "session=" in set_cookie:
            for part in set_cookie.split(";"):
                if part.strip().startswith("session="):
                    return part.strip().split("=", 1)[1]
    except Exception as e:
        logger.warning("TC login failed: %s", e)
    return None


def _tc_get_token() -> Optional[str]:
    global _tc_session_token
    if TC_AUTH_TOKEN:
        return TC_AUTH_TOKEN
    with _tc_token_lock:
        if _tc_session_token:
            return _tc_session_token
        _tc_session_token = _tc_login()
        return _tc_session_token


# ---------------------------------------------------------------------------
# Koios helpers
# ---------------------------------------------------------------------------

def _koios_headers(country_code: str) -> dict:
    key, secret = _country_creds.get(
        country_code, (_GLOBAL_WRITE_KEY, _GLOBAL_WRITE_SECRET),
    )
    return {
        "Content-Type": "application/json",
        "X-API-KEY": key,
        "X-API-SECRET": secret,
    }


def _koios_disconnect(meter_serial: str, country_code: str) -> ControlResult:
    """Disconnect a SparkMeter via the Koios v1 API."""
    payload = {"meter_serial": meter_serial}
    try:
        r = requests.post(
            f"{KOIOS_BASE}/api/v1/meters/disconnect",
            json=payload,
            headers=_koios_headers(country_code),
            timeout=API_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Koios disconnect request failed for %s: %s", meter_serial, e)
        return ControlResult(
            success=False, platform="koios", action="disconnect", error=str(e),
        )

    if not r.content:
        ok = 200 <= r.status_code < 300
        return ControlResult(
            success=ok, platform="koios", action="disconnect",
            error=None if ok else f"HTTP {r.status_code} (empty body)",
        )

    try:
        body = r.json()
    except ValueError:
        snippet = (r.text or "")[:400]
        logger.warning("Koios non-JSON HTTP %d for disconnect %s: %s",
                       r.status_code, meter_serial, snippet)
        return ControlResult(
            success=False, platform="koios", action="disconnect",
            error=f"HTTP {r.status_code} (invalid JSON body)",
        )

    errors = body.get("errors")
    if errors:
        err = errors[0].get("title", str(errors)) if isinstance(errors, list) else str(errors)
        logger.warning("Koios rejected disconnect for %s: %s", meter_serial, err)
        return ControlResult(
            success=False, platform="koios", action="disconnect", error=err,
        )

    ok = 200 <= r.status_code < 300
    if not ok:
        detail = body.get("detail")
        msg = str(detail) if detail else f"HTTP {r.status_code}"
        return ControlResult(
            success=False, platform="koios", action="disconnect", error=msg,
        )

    return ControlResult(
        success=True, platform="koios", action="disconnect",
        raw_response=body.get("data") or {},
    )


def _koios_reconnect(meter_serial: str, country_code: str) -> ControlResult:
    """Reconnect a SparkMeter via the Koios v1 API."""
    payload = {"meter_serial": meter_serial}
    try:
        r = requests.post(
            f"{KOIOS_BASE}/api/v1/meters/reconnect",
            json=payload,
            headers=_koios_headers(country_code),
            timeout=API_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Koios reconnect request failed for %s: %s", meter_serial, e)
        return ControlResult(
            success=False, platform="koios", action="reconnect", error=str(e),
        )

    if not r.content:
        ok = 200 <= r.status_code < 300
        return ControlResult(
            success=ok, platform="koios", action="reconnect",
            error=None if ok else f"HTTP {r.status_code} (empty body)",
        )

    try:
        body = r.json()
    except ValueError:
        snippet = (r.text or "")[:400]
        logger.warning("Koios non-JSON HTTP %d for reconnect %s: %s",
                       r.status_code, meter_serial, snippet)
        return ControlResult(
            success=False, platform="koios", action="reconnect",
            error=f"HTTP {r.status_code} (invalid JSON body)",
        )

    errors = body.get("errors")
    if errors:
        err = errors[0].get("title", str(errors)) if isinstance(errors, list) else str(errors)
        logger.warning("Koios rejected reconnect for %s: %s", meter_serial, err)
        return ControlResult(
            success=False, platform="koios", action="reconnect", error=err,
        )

    ok = 200 <= r.status_code < 300
    if not ok:
        detail = body.get("detail")
        msg = str(detail) if detail else f"HTTP {r.status_code}"
        return ControlResult(
            success=False, platform="koios", action="reconnect", error=msg,
        )

    return ControlResult(
        success=True, platform="koios", action="reconnect",
        raw_response=body.get("data") or {},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def disconnect_sparkmeter(meter_id: str, account_number: str = "") -> ControlResult:
    """Disconnect a SparkMeter — force the relay open regardless of credit balance.

    Routes to ThunderCloud v0 or Koios v1 based on the site code extracted
    from the account number or looked up from the meters table.
    """
    account_number = (account_number or "").strip().upper()
    site = _extract_site(account_number)

    if not site:
        return ControlResult(
            success=False, platform="unknown", action="disconnect",
            error=f"Cannot determine site from account '{account_number}'",
        )

    country = _site_to_country.get(site)
    if not country:
        return ControlResult(
            success=False, platform="unknown", action="disconnect",
            error=f"Site '{site}' not mapped to any country",
        )

    try:
        if site in THUNDERCLOUD_SITES:
            # ThunderCloud — use meter_id directly (may be numeric or serial)
            return _tc_disconnect(meter_id)
        else:
            return _koios_disconnect(meter_id, country)
    except Exception as e:
        platform = "thundercloud" if site in THUNDERCLOUD_SITES else "koios"
        logger.error("SM disconnect failed for %s: %s", meter_id, e)
        return ControlResult(
            success=False, platform=platform, action="disconnect", error=str(e),
        )


def reconnect_sparkmeter(meter_id: str, account_number: str = "") -> ControlResult:
    """Reconnect a SparkMeter — restore normal billing-controlled relay state."""
    account_number = (account_number or "").strip().upper()
    site = _extract_site(account_number)

    if not site:
        return ControlResult(
            success=False, platform="unknown", action="reconnect",
            error=f"Cannot determine site from account '{account_number}'",
        )

    country = _site_to_country.get(site)
    if not country:
        return ControlResult(
            success=False, platform="unknown", action="reconnect",
            error=f"Site '{site}' not mapped to any country",
        )

    try:
        if site in THUNDERCLOUD_SITES:
            return _tc_reconnect(meter_id)
        else:
            return _koios_reconnect(meter_id, country)
    except Exception as e:
        platform = "thundercloud" if site in THUNDERCLOUD_SITES else "koios"
        logger.error("SM reconnect failed for %s: %s", meter_id, e)
        return ControlResult(
            success=False, platform=platform, action="reconnect", error=str(e),
        )


# ThunderCloud v0 disconnect/reconnect (stubs — extend as API is documented)
def _tc_disconnect(meter_id: str) -> ControlResult:
    token = _tc_get_token()
    if not token:
        return ControlResult(
            success=False, platform="thundercloud", action="disconnect",
            error="No ThunderCloud auth token configured",
        )
    # TODO: verify the ThunderCloud v0 disconnect endpoint
    logger.warning(
        "ThunderCloud disconnect not yet implemented for meter %s — flag set, "
        "manual TC intervention may be needed", meter_id,
    )
    return ControlResult(
        success=False, platform="thundercloud", action="disconnect",
        error="ThunderCloud disconnect endpoint not yet configured",
    )


def _tc_reconnect(meter_id: str) -> ControlResult:
    token = _tc_get_token()
    if not token:
        return ControlResult(
            success=False, platform="thundercloud", action="reconnect",
            error="No ThunderCloud auth token configured",
        )
    # TODO: verify the ThunderCloud v0 reconnect endpoint
    logger.warning(
        "ThunderCloud reconnect not yet implemented for meter %s — flag set, "
        "manual TC intervention may be needed", meter_id,
    )
    return ControlResult(
        success=False, platform="thundercloud", action="reconnect",
        error="ThunderCloud reconnect endpoint not yet configured",
    )
