"""
SparkMeter customer sync module — the CC → SM customer creation pipe.

QUARANTINED NON-POLICY MODULE (for event-parity scope):
- Valuable customer-provisioning work, but not part of current payment event-parity baseline.
- Keep separate from parity PRs unless explicitly requested.
- See docs/ops/non-policy-quarantine-registry.md.

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
reconciliation when TC was correct (`acdb-api/scripts/ops/fix_mak_drift.py`), ongoing edits
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
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

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

# Koios org IDs per country (for /sm/ web session endpoints)
KOIOS_ORG_IDS: Dict[str, str] = {
    "LS": "1cddcb07-6647-40aa-aaaa-70d762922029",
    "BN": "0123589c-7f1f-4eb4-8888-d8f8aa706ea4",
}


def _koios_org_id(site_code: str) -> str:
    """Return the Koios org UUID for a site code."""
    cc = _site_to_country.get(site_code, "LS")
    return KOIOS_ORG_IDS.get(cc, KOIOS_ORG_IDS["LS"])


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


# ---------------------------------------------------------------------------
# Koios web session (email/password + CSRF login)
# ---------------------------------------------------------------------------

_koios_web_session: Optional[requests.Session] = None
_koios_web_session_country: Optional[str] = None


def _get_koios_web_session(site_code: str = "") -> Optional[requests.Session]:
    """Return a Koios-web-authenticated session, or None if creds not configured.

    Resolves credentials per-country: KOIOS_WEB_EMAIL_{CC} → KOIOS_WEB_EMAIL.
    Caches one session per country so LS and BN can use different logins.
    """
    global _koios_web_session, _koios_web_session_country

    cc = _site_to_country.get(site_code, "LS")
    email = (
        os.environ.get(f"KOIOS_WEB_EMAIL_{cc}")
        or os.environ.get("KOIOS_WEB_EMAIL", "")
    )
    password = (
        os.environ.get(f"KOIOS_WEB_PASSWORD_{cc}")
        or os.environ.get("KOIOS_WEB_PASSWORD", "")
    )
    if not email or not password:
        return None

    # Return cached session if same country
    if _koios_web_session is not None and _koios_web_session_country == cc:
        return _koios_web_session

    sess = requests.Session()
    try:
        r = sess.get(f"{KOIOS_BASE}/login", timeout=30)
        r.raise_for_status()
        csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
        if not csrf:
            logger.error("Koios web login: CSRF token not found")
            return None
        r = sess.post(
            f"{KOIOS_BASE}/login",
            data={
                "csrf_token": csrf.group(1),
                "email": email,
                "password": password,
            },
            timeout=30,
        )
        if r.status_code != 200 or "/login" in r.url:
            logger.error("Koios web login failed: HTTP %s", r.status_code)
            return None
        _koios_web_session = sess
        _koios_web_session_country = cc
        logger.info("Koios web session established for country=%s", cc)
        return _koios_web_session
    except Exception as e:
        logger.error("Koios web login error: %s", e)
        return None


def _koios_web_create_customer(
    account_number: str, name: str, site_code: str, phone: Optional[str] = None,
) -> CustomerSyncResult:
    """Create a Koios customer via web session auth (bypasses API key permission bug).

    The Koios manage API key creates customers that return 201 but become immediately
    inaccessible (404 on GET, not in code search). The web UI session has full user
    permissions and creates properly accessible customers.
    """
    sess = _get_koios_web_session(site_code)
    if not sess:
        return CustomerSyncResult(
            success=False, platform="koios",
            error="KOIOS_WEB_EMAIL/PASSWORD not configured",
        )

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
        r = sess.post(
            f"{KOIOS_BASE}/api/v1/customers",
            json=payload,
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        logger.error("Koios web customer create failed for %s: %s", account_number, e)
        return CustomerSyncResult(success=False, platform="koios", error=str(e))

    if r.status_code == 201:
        data = r.json().get("data", {})
        sm_id = data.get("id", "")
        logger.info(
            "Koios web customer created: %s -> %s (sm_id=%s)",
            account_number, name, sm_id,
        )
        return CustomerSyncResult(
            success=True, platform="koios", sm_customer_id=str(sm_id),
        )

    errors = r.json().get("errors", [])
    error_msg = errors[0].get("title", str(errors)) if errors else f"HTTP {r.status_code}"
    details = errors[0].get("details", "") if errors else ""

    # Idempotent create: customer code already exists upstream.
    if "already exists" in str(error_msg).lower() or "already exists" in str(details).lower():
        existing = _koios_web_lookup_customer(account_number, site_code)
        if existing:
            existing_id = str(existing.get("id", ""))
            logger.info(
                "Koios web customer already exists for %s (sm_id=%s)",
                account_number, existing_id,
            )
            return CustomerSyncResult(
                success=True,
                platform="koios",
                sm_customer_id=existing_id or None,
            )
        logger.info("Koios web customer already exists for %s", account_number)
        return CustomerSyncResult(success=True, platform="koios", skipped=True)

    logger.warning(
        "Koios web customer create failed for %s: %s", account_number, error_msg,
    )
    return CustomerSyncResult(success=False, platform="koios", error=error_msg)


def _koios_web_lookup_customer(account_number: str, site_code: str) -> Optional[dict]:
    """Look up a customer via Koios web session /sm/ endpoints.

    The public API /api/v1/customers?code= only returns customers with meters
    attached. New customers without meters appear only in the /sm/ "unconfigured-
    customers" list. This function checks both /sm/ endpoints to find any customer.
    """
    sess = _get_koios_web_session(site_code)
    if not sess:
        return None

    org_id = _koios_org_id(site_code)

    # Check configured customers first
    try:
        r = sess.get(
            f"{KOIOS_BASE}/sm/organizations/{org_id}/customers",
            params={"page": 1, "pageSize": 200},
            timeout=API_TIMEOUT,
        )
        if r.status_code == 200:
            for c in r.json().get("customers", []):
                if c.get("code") == account_number:
                    return c
    except Exception as e:
        logger.warning("Koios web configured-customer lookup error: %s", e)

    # Check unconfigured customers
    try:
        r = sess.get(
            f"{KOIOS_BASE}/sm/organizations/{org_id}/unconfigured-customers",
            params={"page": 1, "pageSize": 200},
            timeout=API_TIMEOUT,
        )
        if r.status_code == 200:
            for c in r.json().get("customers", []):
                if c.get("code") == account_number:
                    return c
    except Exception as e:
        logger.warning("Koios web unconfigured-customer lookup error: %s", e)

    return None


def _koios_create_customer(
    account_number: str, name: str, site_code: str, phone: Optional[str] = None,
) -> CustomerSyncResult:
    """Create a Koios customer — web session first, API key as fallback.

    The Koios manage API key has a permission bug where customers are created
    (HTTP 201) but immediately inaccessible. The web UI session creates properly
    accessible customers, so we try that first.
    """
    # 1. Try web session (full permissions)
    web_result = _koios_web_create_customer(account_number, name, site_code, phone)
    if web_result.success:
        return web_result
    if web_result.error and "not configured" not in web_result.error:
        logger.info(
            "Koios web create failed for %s (%s), falling back to API key",
            account_number, web_result.error,
        )

    # 2. Fall back to API key
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

    retries = 3
    last_error = None
    last_status = None
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{KOIOS_BASE}/api/v1/customers",
                json=payload,
                headers=_koios_headers(site_code),
                timeout=API_TIMEOUT,
            )
        except Exception as e:
            logger.error(
                "Koios API customer create failed for %s (attempt %d/%d): %s",
                account_number, attempt + 1, retries, e,
            )
            last_error = str(e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        last_status = r.status_code
        if r.status_code == 201:
            data = r.json().get("data", {})
            sm_id = data.get("id", "")
            logger.info(
                "Koios API customer created: %s -> %s (sm_id=%s)",
                account_number, name, sm_id,
            )
            return CustomerSyncResult(
                success=True, platform="koios", sm_customer_id=str(sm_id),
            )

        if r.status_code in (502, 504):
            last_error = f"HTTP {r.status_code} (attempt {attempt + 1}/{retries})"
            logger.warning(
                "Koios HTTP %d for %s, attempt %d/%d",
                r.status_code, account_number, attempt + 1, retries,
            )
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        errors = r.json().get("errors", [])
        error_msg = errors[0].get("title", str(errors)) if errors else f"HTTP {r.status_code}"
        details = errors[0].get("details", "") if errors else ""

        # "already exists" — find it via web lookup
        if "already exists" in str(details).lower() or "already exists" in str(error_msg).lower():
            logger.info(
                "Koios customer %s already exists, searching via web session",
                account_number,
            )
            existing = _koios_web_lookup_customer(account_number, site_code)
            if existing:
                existing_id = str(existing.get("id", ""))
                logger.info(
                    "Found existing Koios customer for %s: %s",
                    account_number, existing_id,
                )
                return CustomerSyncResult(
                    success=True, platform="koios",
                    sm_customer_id=existing_id,
                )
            logger.warning(
                "Koios customer %s already exists but could not be found",
                account_number,
            )

        logger.warning(
            "Koios API customer create failed for %s: %s", account_number, error_msg,
        )
        return CustomerSyncResult(success=False, platform="koios", error=error_msg)

    return CustomerSyncResult(
        success=False, platform="koios",
        error=last_error or f"HTTP {last_status}",
    )


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


def attach_koios_meter(
    account_number: str,
    meter_serial: str,
    customer_uuid: Optional[str] = None,
) -> CustomerSyncResult:
    """Assign a physical meter to a Koios v1 customer (Nova / LS + BN sites).

    Resolves SparkMeter ``customer_id`` via ``GET /api/v1/customers?code=`` when
    ``customer_uuid`` is not supplied.

    **422 validation:** log/inspect the JSON body (often ``detail`` or ``errors[]``)
    against the live Koios Swagger ``Meter`` model before changing the PUT payload.

    TODO: Koios OpenAPI ``Meter`` JSON uses field names we infer as ``serial``;
    if live API returns 422, try ``serial_number`` or cross-check Swagger.
    """
    site = _extract_site(account_number)
    if site in THUNDERCLOUD_SITES:
        return CustomerSyncResult(
            success=True, platform="none", skipped=True,
            error="Not a Koios site",
        )
    if site not in KOIOS_SERVICE_AREAS:
        return CustomerSyncResult(
            success=True, platform="none", skipped=True,
            error="No Koios mapping for site",
        )

    uid = (customer_uuid or "").strip()
    if not uid:
        row = lookup_sparkmeter_customer(account_number)
        if not row:
            return CustomerSyncResult(
                success=False, platform="koios",
                error="Customer not found in Koios — create customer first",
            )
        uid = str(row.get("id") or "").strip()
    if not uid:
        return CustomerSyncResult(
            success=False, platform="koios",
            error="Koios customer record missing id",
        )

    body = {"serial": str(meter_serial).strip()}
    url = f"{KOIOS_BASE}/api/v1/customers/{uid}/meter"
    retries = 6
    last_error = None
    for attempt in range(retries):
        try:
            r = requests.put(
                url,
                json=body,
                headers=_koios_headers(site),
                timeout=API_TIMEOUT,
            )
        except Exception as e:
            logger.error(
                "Koios meter attach PUT failed for %s (attempt %d/%d): %s",
                account_number, attempt + 1, retries, e,
            )
            last_error = str(e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        if r.status_code in (502, 504):
            logger.warning(
                "Koios meter attach HTTP %d for %s, attempt %d/%d",
                r.status_code, account_number, attempt + 1, retries,
            )
            last_error = f"HTTP {r.status_code}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)
        if r.status_code == 404:
            # Koios can lag right after customer creation; the id exists but is not
            # immediately resolvable for meter attach. Retry before surfacing failure.
            preview = (r.text or "")[:200].strip()
            logger.warning(
                "Koios meter attach HTTP 404 for %s, attempt %d/%d: %s",
                account_number, attempt + 1, retries, preview,
            )
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            last_error = "Resource Not Found"
            return CustomerSyncResult(success=False, platform="koios", error=last_error)
        break

    if r.status_code in (200, 201, 204):
        logger.info("Koios meter attach OK: acct=%s meter=%s", account_number, meter_serial)
        return CustomerSyncResult(
            success=True, platform="koios", sm_customer_id=str(uid),
        )
    if r.status_code == 409:
        logger.info(
            "Koios meter attach HTTP 409 for %s (idempotent): %s",
            account_number,
            (r.text or "")[:300],
        )
        return CustomerSyncResult(
            success=True, platform="koios", sm_customer_id=str(uid),
        )
    try:
        err_body = r.json()
        errors = err_body.get("errors", [])
        err_msg = errors[0].get("title", str(errors)) if errors else err_body.get("detail", r.text)
    except Exception:
        err_msg = r.text or f"HTTP {r.status_code}"
    logger.warning("Koios meter attach failed for %s: %s", account_number, err_msg)
    return CustomerSyncResult(success=False, platform="koios", error=str(err_msg))


def sync_sparkmeter_customer_and_meter(
    account_number: str,
    name: str,
    meter_serial: Optional[str],
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure upstream SparkMeter has customer + meter when serial is known.

    ThunderCloud: always POST ``/api/v0/customer/`` when ``meter_serial`` is set
    (do not skip solely because a 1PDB row exists).

    Koios: create customer if missing, then PUT ``/api/v1/customers/{id}/meter``.
    """
    out: Dict[str, Any] = {"account_number": account_number, "platform": None}
    if not meter_serial or not str(meter_serial).strip():
        out["skipped"] = True
        out["note"] = "No meter serial — deferred"
        return out

    site = _extract_site(account_number)
    serial = str(meter_serial).strip()

    if site in THUNDERCLOUD_SITES:
        tc = _tc_create_customer(account_number, name, serial)
        out["platform"] = "thundercloud"
        out["customer"] = {
            "success": tc.success,
            "skipped": tc.skipped,
            "error": tc.error,
            "sm_customer_id": tc.sm_customer_id,
        }
        return out

    if site in KOIOS_SERVICE_AREAS:
        out["platform"] = "koios"
        existing = lookup_sparkmeter_customer(account_number)
        if existing:
            out["customer"] = {"already_exists": True, "sm_customer_id": str(existing.get("id", ""))}
            uid = str(existing.get("id") or "")
        else:
            cr = _koios_create_customer(account_number, name, site, phone)
            out["customer"] = {
                "success": cr.success,
                "error": cr.error,
                "sm_customer_id": cr.sm_customer_id,
            }
            if not cr.success or not cr.sm_customer_id:
                return out
            uid = str(cr.sm_customer_id)

        att = attach_koios_meter(account_number, serial, customer_uuid=uid)
        out["meter_attach"] = {
            "success": att.success,
            "skipped": att.skipped,
            "error": att.error,
        }
        return out

    out["skipped"] = True
    out["note"] = f"Site {site!r} has no SparkMeter platform in CC config"
    return out


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
    Tries API key first, falls back to web session (needed for customers
    created via web UI or web session, which API key may not see).
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
            # 1. Try API key lookup
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=_koios_headers(site),
                params={"code": account_number},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    return data[0]

            # 2. Fall back to web session public API
            web_sess = _get_koios_web_session(site)
            if web_sess:
                r2 = web_sess.get(
                    f"{KOIOS_BASE}/api/v1/customers",
                    params={"code": account_number},
                    timeout=API_TIMEOUT,
                )
                if r2.status_code == 200:
                    data2 = r2.json().get("data", [])
                    if data2:
                        logger.info(
                            "SM customer %s found via web session (not visible to API key)",
                            account_number,
                        )
                        return data2[0]

            # 3. Check /sm/ endpoints for unconfigured customers (no meter yet)
            cust = _koios_web_lookup_customer(account_number, site)
            if cust:
                logger.info(
                    "SM customer %s found via /sm/ lookup (unconfigured)",
                    account_number,
                )
                return cust
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
