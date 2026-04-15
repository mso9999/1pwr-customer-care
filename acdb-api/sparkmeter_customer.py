"""
SparkMeter customer sync module — the CC → SM customer creation pipe.

When a customer is registered in CC/1PDB, this module pushes the customer
record to SparkMeter so the metering platform knows about them immediately.

Platforms:
  - Koios v1 API  — POST /api/v1/customers (LS + BN sites on Koios)
  - ThunderCloud v0 API — POST /api/v0/customer/ (MAK only, requires meter serial)

Constraints:
  - Koios can create customers without a meter assignment.
  - ThunderCloud requires a physical meter serial at creation time.
  - SparkMeter documents no separate “PATCH customer”; we **re-POST** the same
    `POST /api/v0/customer/` payload to push name changes from CC to TC (best-effort).

**Source of truth:** 1PDB / CC is canonical. After a **one-time** TC→1PDB name
reconciliation when TC was correct (`scripts/ops/fix_mak_drift.py`), ongoing edits
in CC call **`sync_thundercloud_customer_name`** so ThunderCloud stays aligned.

Multi-country support:
  Each country uses its own Koios API key pair with customer management permissions.
  Credentials resolved: KOIOS_MANAGE_API_KEY_{CC} → KOIOS_API_KEY_{CC} → KOIOS_API_KEY.

Environment variables (set in /opt/1pdb/.env):
  KOIOS_API_KEY / KOIOS_API_SECRET           — LS read/manage key
  KOIOS_MANAGE_API_KEY_BN / ..._SECRET_BN    — BN manage key (customer creation)
  TC_API_BASE / TC_AUTH_TOKEN                 — ThunderCloud auth
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("cc-api.sm-customer")

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")

TC_API_BASE = os.environ.get(
    "TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud"
)
TC_AUTH_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
TC_TARIFF_NAME = "1PWR-tariff"

THUNDERCLOUD_SITES = {"MAK", "LAB"}

API_TIMEOUT = 90

# ---------------------------------------------------------------------------
# Per-country Koios credential resolution
# ---------------------------------------------------------------------------

_GLOBAL_KEY = os.environ.get("KOIOS_API_KEY", "")
_GLOBAL_SECRET = os.environ.get("KOIOS_API_SECRET", "")


def _build_country_creds() -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    from country_config import _REGISTRY

    site_to_country: Dict[str, str] = {}
    for cc, cfg in _REGISTRY.items():
        for site in cfg.site_abbrev:
            site_to_country[site] = cc

    country_creds: Dict[str, Tuple[str, str]] = {}
    for cc in _REGISTRY:
        key = (
            os.environ.get(f"KOIOS_MANAGE_API_KEY_{cc}")
            or os.environ.get(f"KOIOS_API_KEY_{cc}")
            or _GLOBAL_KEY
        )
        secret = (
            os.environ.get(f"KOIOS_MANAGE_API_SECRET_{cc}")
            or os.environ.get(f"KOIOS_API_SECRET_{cc}")
            or _GLOBAL_SECRET
        )
        country_creds[cc] = (key, secret)

    return site_to_country, country_creds


_site_to_country, _country_creds = _build_country_creds()

# ---------------------------------------------------------------------------
# Koios service_area_id per site (derived from live API probing)
# ---------------------------------------------------------------------------

_LS_DEFAULT_SERVICE_AREA = "e3015e87-8dc8-42f0-9cb7-ac93f9473015"
KOIOS_SERVICE_AREAS: Dict[str, str] = {
    # Lesotho
    "KET": _LS_DEFAULT_SERVICE_AREA,
    "LSB": _LS_DEFAULT_SERVICE_AREA,
    "MAT": _LS_DEFAULT_SERVICE_AREA,
    "SEH": _LS_DEFAULT_SERVICE_AREA,
    "SHG": _LS_DEFAULT_SERVICE_AREA,
    "TLH": _LS_DEFAULT_SERVICE_AREA,
    "RIB": _LS_DEFAULT_SERVICE_AREA,
    "TOS": _LS_DEFAULT_SERVICE_AREA,
    "MAS": "e6efc982-91ea-4721-92ee-97e68dd761bb",
    # Benin
    "GBO": "de00dfbf-64e7-4d0d-ae80-8a4a309fe8ed",
    "SAM": "43a81ea8-f5fd-4df3-ae6b-0b7f54a58fe2",
}


@dataclass
class CustomerSyncResult:
    success: bool
    platform: str
    sm_customer_id: Optional[str] = None
    error: Optional[str] = None
    skipped: bool = False


def _extract_site(account_number: str) -> str:
    m = re.search(r"([A-Z]{3})$", account_number.upper())
    return m.group(1) if m else ""


def is_thundercloud_account(account_number: str) -> bool:
    """True if account code is for a site on ThunderCloud on-prem (MAK/LAB)."""
    return _extract_site(account_number) in THUNDERCLOUD_SITES


def _koios_headers(site_code: str) -> dict:
    cc = _site_to_country.get(site_code, "LS")
    key, secret = _country_creds.get(cc, (_GLOBAL_KEY, _GLOBAL_SECRET))
    return {
        "Content-Type": "application/json",
        "X-API-KEY": key,
        "X-API-SECRET": secret,
    }


def _koios_create_customer(
    account_number: str, name: str, site_code: str, phone: Optional[str] = None,
) -> CustomerSyncResult:
    service_area_id = KOIOS_SERVICE_AREAS.get(site_code)
    if not service_area_id:
        return CustomerSyncResult(
            success=False, platform="koios",
            error=f"No Koios service_area_id mapped for site '{site_code}'",
        )

    payload: dict = {
        "name": name,
        "code": account_number,
        "service_area_id": service_area_id,
    }
    if phone:
        payload["phone_number"] = phone

    try:
        r = requests.post(
            f"{KOIOS_BASE}/api/v1/customers",
            json=payload,
            headers=_koios_headers(site_code),
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        logger.error("Koios customer create failed for %s: %s", account_number, e)
        return CustomerSyncResult(success=False, platform="koios", error=str(e))

    if r.status_code == 201:
        data = r.json().get("data", {})
        sm_id = data.get("id", "")
        logger.info(
            "Koios customer created: %s -> %s (sm_id=%s)",
            account_number, name, sm_id,
        )
        return CustomerSyncResult(
            success=True, platform="koios", sm_customer_id=str(sm_id),
        )

    errors = r.json().get("errors", [])
    error_msg = errors[0].get("title", str(errors)) if errors else f"HTTP {r.status_code}"
    logger.warning(
        "Koios customer create failed for %s: %s", account_number, error_msg,
    )
    return CustomerSyncResult(success=False, platform="koios", error=error_msg)


def _tc_create_customer(
    account_number: str, name: str, meter_serial: Optional[str] = None,
) -> CustomerSyncResult:
    if not meter_serial:
        logger.info(
            "TC customer sync skipped for %s: no meter serial assigned yet",
            account_number,
        )
        return CustomerSyncResult(
            success=True, platform="thundercloud", skipped=True,
            error="No meter serial — will sync when meter is assigned",
        )

    if not TC_AUTH_TOKEN:
        return CustomerSyncResult(
            success=False, platform="thundercloud",
            error="No ThunderCloud auth token configured",
        )

    try:
        r = requests.post(
            f"{TC_API_BASE}/api/v0/customer/",
            data={
                "serial": meter_serial,
                "code": account_number,
                "name": name,
                "meter_tariff_name": TC_TARIFF_NAME,
            },
            headers={
                "Authentication-Token": TC_AUTH_TOKEN,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        logger.error("TC customer create failed for %s: %s", account_number, e)
        return CustomerSyncResult(success=False, platform="thundercloud", error=str(e))

    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    if r.status_code in (200, 201) and body.get("status") == "success":
        sm_id = body.get("customer_id", "")
        logger.info(
            "TC customer push OK: %s -> %s (sm_id=%s, meter=%s)",
            account_number, name, sm_id, meter_serial,
        )
        return CustomerSyncResult(
            success=True, platform="thundercloud", sm_customer_id=str(sm_id),
        )

    error_msg = body.get("error", f"HTTP {r.status_code}")
    logger.warning(
        "TC customer push failed for %s: %s", account_number, error_msg,
    )
    return CustomerSyncResult(
        success=False, platform="thundercloud", error=error_msg,
    )


def sync_thundercloud_customer_name(
    account_number: str,
    first_name: Optional[str],
    last_name: Optional[str],
    meter_serial: Optional[str] = None,
) -> CustomerSyncResult:
    """Push current CC/1PDB name to ThunderCloud (MAK/LAB) using the same POST as create.

    Call after customer name changes so TC matches the portal. No-op for non-TC sites.
    Requires an active meter serial; otherwise returns skipped=True.
    """
    site = _extract_site(account_number)
    if site not in THUNDERCLOUD_SITES:
        return CustomerSyncResult(
            success=True, platform="none", skipped=True,
            error="Not a ThunderCloud site",
        )
    name = " ".join(filter(None, [first_name or "", last_name or ""])).strip()
    if not name:
        return CustomerSyncResult(
            success=False, platform="thundercloud", error="Empty customer name",
        )
    return _tc_create_customer(account_number, name, meter_serial)


def create_sparkmeter_customer(
    account_number: str,
    name: str,
    meter_serial: Optional[str] = None,
    phone: Optional[str] = None,
) -> CustomerSyncResult:
    """Push a new customer to SparkMeter.

    Routes to ThunderCloud (MAK/LAB) or Koios (all other LS sites)
    based on the site code in the account number.

    For Koios sites, creation succeeds even without a meter.
    For ThunderCloud sites, a meter serial is required; if absent
    the sync is deferred (returns success=True, skipped=True).
    """
    site = _extract_site(account_number)
    if not site:
        return CustomerSyncResult(
            success=False, platform="unknown",
            error=f"Cannot determine site from '{account_number}'",
        )

    try:
        if site in THUNDERCLOUD_SITES:
            return _tc_create_customer(account_number, name, meter_serial)
        elif site in KOIOS_SERVICE_AREAS:
            return _koios_create_customer(account_number, name, site, phone)
        else:
            logger.info(
                "SM customer sync skipped for %s: site '%s' not mapped to any SM platform",
                account_number, site,
            )
            return CustomerSyncResult(
                success=True, platform="none", skipped=True,
                error=f"Site '{site}' has no SparkMeter platform configured",
            )
    except Exception as e:
        logger.error("SM customer sync failed for %s: %s", account_number, e)
        return CustomerSyncResult(
            success=False,
            platform="thundercloud" if site in THUNDERCLOUD_SITES else "koios",
            error=str(e),
        )


def lookup_sparkmeter_customer(account_number: str) -> Optional[dict]:
    """Check if a customer already exists in SparkMeter by account code.

    Returns the SM customer dict if found, None otherwise.
    """
    site = _extract_site(account_number)
    if not site:
        return None

    try:
        if site in THUNDERCLOUD_SITES:
            r = requests.get(
                f"{TC_API_BASE}/api/v0/customer/{account_number}",
                headers={"Authentication-Token": TC_AUTH_TOKEN},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                customers = r.json().get("customers", [])
                return customers[0] if customers else None
            return None
        elif site in KOIOS_SERVICE_AREAS:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=_koios_headers(site),
                params={"code": account_number},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                return data[0] if data else None
            return None
    except Exception as e:
        logger.warning("SM customer lookup failed for %s: %s", account_number, e)
    return None


def is_configured() -> dict:
    """Return a dict summarising which platforms have customer-sync credentials."""
    result: dict = {"thundercloud": bool(TC_AUTH_TOKEN)}
    for cc, (key, secret) in _country_creds.items():
        result[f"koios_{cc}"] = bool(key and secret)
    return result
