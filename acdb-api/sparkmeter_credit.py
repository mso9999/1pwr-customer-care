"""
SparkMeter crediting module — the CC → SM pipe.

Pushes payment credits to SparkMeter's billing systems so that meter
balances actually update when payments are recorded in CC/1PDB.

Two platforms:
  - Koios v1 API  — sites on sparkmeter.cloud  (MAT, MAS, TLH, SHG, KET, …)
  - ThunderCloud v0 API — MAK/LAB on sparkcloud-u740425.sparkmeter.cloud

Environment variables (set in /opt/1pdb/.env):
  KOIOS_WRITE_API_KEY     — Koios API key with write (payment) scope
  KOIOS_WRITE_API_SECRET  — matching secret
  TC_API_BASE             — ThunderCloud API base URL
  TC_AUTH_TOKEN           — ThunderCloud Authentication-Token (Flask session)
  THUNDERCLOUD_USERNAME   — fallback: login credentials
  THUNDERCLOUD_PASSWORD   — fallback: login credentials
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("cc-api.sm-credit")

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
KOIOS_WRITE_KEY = os.environ.get(
    "KOIOS_WRITE_API_KEY",
    os.environ.get("KOIOS_API_KEY", ""),
)
KOIOS_WRITE_SECRET = os.environ.get(
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

_tc_token_lock = threading.Lock()
_tc_session_token: Optional[str] = None


@dataclass
class CreditResult:
    success: bool
    platform: str
    sm_transaction_id: Optional[str] = None
    error: Optional[str] = None
    customer_id: Optional[str] = None


def _extract_site(account_number: str) -> str:
    m = re.search(r"([A-Z]{3})$", account_number.upper())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# ThunderCloud v0 helpers
# ---------------------------------------------------------------------------

def _tc_login() -> Optional[str]:
    """Login to ThunderCloud and return the session token."""
    if not TC_USERNAME or not TC_PASSWORD:
        return None
    try:
        r = requests.post(
            f"{TC_API_BASE}/login",
            data={"email": TC_USERNAME, "password": TC_PASSWORD},
            allow_redirects=False,
            timeout=30,
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


def _tc_invalidate_token():
    global _tc_session_token
    with _tc_token_lock:
        _tc_session_token = None


def _tc_get_customer_id(account_code: str) -> Optional[str]:
    token = _tc_get_token()
    if not token:
        return None
    r = requests.get(
        f"{TC_API_BASE}/api/v0/customer/{account_code}",
        headers={"Authentication-Token": token},
        timeout=30,
    )
    body = r.json()
    if body.get("error"):
        return None
    customers = body.get("customers", [])
    return customers[0]["id"] if customers else None


def _tc_credit(
    customer_id: str, amount: float, external_id: str = ""
) -> CreditResult:
    token = _tc_get_token()
    if not token:
        return CreditResult(
            success=False, platform="thundercloud",
            error="No ThunderCloud auth token available",
        )
    form = {
        "customer_id": customer_id,
        "amount": str(amount),
        "source": "cash",
    }
    if external_id:
        form["external_id"] = external_id

    r = requests.post(
        f"{TC_API_BASE}/api/v0/transaction/",
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authentication-Token": token,
        },
        timeout=30,
    )
    body = r.json()
    if body.get("error"):
        if "auth" in str(body["error"]).lower() or r.status_code in (401, 403):
            _tc_invalidate_token()
        return CreditResult(
            success=False, platform="thundercloud",
            error=str(body["error"]), customer_id=customer_id,
        )
    return CreditResult(
        success=True, platform="thundercloud",
        sm_transaction_id=str(body.get("transaction_id", "")),
        customer_id=customer_id,
    )


# ---------------------------------------------------------------------------
# Koios v1 helpers
# ---------------------------------------------------------------------------

def _koios_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-API-KEY": KOIOS_WRITE_KEY,
        "X-API-SECRET": KOIOS_WRITE_SECRET,
    }


def _koios_get_customer_id(account_code: str) -> Optional[str]:
    r = requests.get(
        f"{KOIOS_BASE}/api/v1/customers",
        params={"code": account_code},
        headers=_koios_headers(),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0]["id"] if data else None


def _koios_credit(
    customer_id: str, amount: float, memo: str = "", external_id: str = ""
) -> CreditResult:
    payload: dict = {"amount": str(amount)}
    if memo:
        payload["memo"] = memo
    if external_id:
        payload["external_id"] = external_id

    r = requests.post(
        f"{KOIOS_BASE}/api/v1/customers/{customer_id}/payments",
        json=payload,
        headers=_koios_headers(),
        timeout=30,
    )
    body = r.json()
    errors = body.get("errors")
    if errors:
        return CreditResult(
            success=False, platform="koios",
            error=errors[0].get("title", str(errors)),
            customer_id=customer_id,
        )
    data = body.get("data", {})
    return CreditResult(
        success=True, platform="koios",
        sm_transaction_id=str(data.get("id", "")),
        customer_id=customer_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def credit_sparkmeter(
    account_number: str,
    amount: float,
    memo: str = "",
    external_id: str = "",
) -> CreditResult:
    """Credit a customer's SparkMeter prepaid balance.

    Routes to ThunderCloud v0 or Koios v1 based on the site code
    extracted from the account number.  Returns a CreditResult with
    success/failure info — the caller decides how to surface it.
    """
    if amount <= 0:
        return CreditResult(
            success=False, platform="unknown",
            error="Amount must be positive",
        )

    site = _extract_site(account_number)
    if not site:
        return CreditResult(
            success=False, platform="unknown",
            error=f"Cannot determine site from '{account_number}'",
        )

    try:
        if site in THUNDERCLOUD_SITES:
            cid = _tc_get_customer_id(account_number)
            if not cid:
                return CreditResult(
                    success=False, platform="thundercloud",
                    error=f"Customer '{account_number}' not found on ThunderCloud",
                )
            return _tc_credit(cid, amount, external_id)
        else:
            cid = _koios_get_customer_id(account_number)
            if not cid:
                return CreditResult(
                    success=False, platform="koios",
                    error=f"Customer '{account_number}' not found on Koios",
                )
            return _koios_credit(cid, amount, memo, external_id)
    except Exception as e:
        platform = "thundercloud" if site in THUNDERCLOUD_SITES else "koios"
        logger.error("SM credit failed for %s: %s", account_number, e)
        return CreditResult(success=False, platform=platform, error=str(e))


def is_configured() -> dict:
    """Return a dict summarising which platforms have credentials set."""
    return {
        "koios": bool(KOIOS_WRITE_KEY and KOIOS_WRITE_SECRET),
        "thundercloud": bool(TC_AUTH_TOKEN or (TC_USERNAME and TC_PASSWORD)),
    }
