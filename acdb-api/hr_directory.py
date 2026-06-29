"""HR portal (``hr.1pwrafrica.com``) directory integration — the canonical
source for 1PWR employees and their department associations.

Why this exists
---------------
Previously CC derived employee department affiliation from the PR system
(Firestore ``users.department``). The HR portal is the authoritative system of
record for employees, so department affiliation is now read from HR's
read-only directory API instead. See
``/Users/mattmso/Dropbox/AI Projects/1PWR HR/hr_portal/docs/HR_API_INTEGRATION.md``.

What this module provides
-------------------------
* ``get_employee_by_id`` / ``get_employee_by_email`` — full HR directory record.
* ``get_department_for_*`` — readable department string (for display in CC).
* ``get_cc_role_for_*`` — department mapped to a CC role via the CC-side
  ``cc_department_role_mappings`` table (the mapping table itself stays in CC;
  only the *source of the department string* moved from PR to HR).
* ``get_all_pr_departments`` — HR departments shaped for the admin
  department→role mapping picker (kept under the historical name so the admin
  UI and API path are unchanged).
* ``reload`` — invalidate caches (called after admin mapping edits).

Caching
-------
The full directory is cached for ``HR_DIRECTORY_TTL`` seconds (default 1800 /
30 min). HR changes slowly and roles are baked into the JWT at login, so a
half-hour freshness window is ample; per-employee ``/show`` lookups fill the
gap for someone not yet in a cached cycle. Firestore is no longer used for
employees — it remains in ``pr_lookup`` only for the portfolio/organization
list.

Env vars (preferred names per the HR integration guide, with legacy fallbacks
so the currently-deployed host keeps working without an env change):
    HR_API_BASE_URL       - HR portal base URL (default https://hr.1pwrafrica.com)
    HR_API_KEY_CC_PORTAL  - CC portal's named HR API key (preferred)
    HR_PORTAL_URL         - legacy base URL fallback
    HR_PORTAL_API_KEY     - legacy API key fallback
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger("cc-api.hr-directory")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HR_BASE_URL = (
    os.environ.get("HR_API_BASE_URL")
    or os.environ.get("HR_PORTAL_URL")
    or "https://hr.1pwrafrica.com"
).rstrip("/")

HR_API_KEY = os.environ.get("HR_API_KEY_CC_PORTAL") or os.environ.get("HR_PORTAL_API_KEY", "")

HR_TIMEOUT = float(os.environ.get("HR_TIMEOUT", "6"))
HR_DIRECTORY_TTL = float(os.environ.get("HR_DIRECTORY_TTL", "1800"))  # 30 min

_AUTH_WARNED = False


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if HR_API_KEY:
        h["X-API-Key"] = HR_API_KEY
    return h


def _get(path: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Optional[dict]:
    """GET a JSON payload from HR. Returns None on any failure/404 (best-effort;
    callers fall back to last-known-good / limited info). Logs once if the key
    is missing so we don't spam."""
    global _AUTH_WARNED
    if not HR_API_KEY and not _AUTH_WARNED:
        logger.warning(
            "HR API key not configured (set HR_API_KEY_CC_PORTAL); HR directory disabled")
        _AUTH_WARNED = True
    url = f"{HR_BASE_URL}{path}"
    try:
        resp = requests.get(
            url, headers=_headers(), params=params,
            timeout=timeout or HR_TIMEOUT, verify=False,
        )
    except requests.RequestException as exc:
        logger.warning("HR request failed: %s %s — %s", url, params or "", exc)
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code == 401:
        if not _AUTH_WARNED:
            logger.error("HR API rejected key (401) — check HR_API_KEY_CC_PORTAL")
            _AUTH_WARNED = True
        return None
    if resp.status_code != 200:
        logger.warning("HR %s returned HTTP %d", url, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("HR %s returned non-JSON body", url)
        return None


# ---------------------------------------------------------------------------
# Directory cache (full pull, TTL) + per-id show fallback
# ---------------------------------------------------------------------------

# email (lower) -> record ; employee_id (as-is from HR) -> record
_dir_by_email: dict[str, dict] = {}
_dir_by_id: dict[str, dict] = {}
_dir_loaded_at: float = 0.0
_dir_meta: Optional[list[str]] = None
_dir_meta_loaded_at: float = 0.0


def _invalidate() -> None:
    global _dir_loaded_at, _dir_meta, _dir_meta_loaded_at
    _dir_by_email.clear()
    _dir_by_id.clear()
    _dir_loaded_at = 0.0
    _dir_meta = None
    _dir_meta_loaded_at = 0.0


def _index_record(rec: dict) -> None:
    email = (rec.get("email") or "").strip().lower()
    emp_id = (rec.get("employee_id") or "").strip()
    if email:
        _dir_by_email[email] = rec
    if emp_id:
        _dir_by_id[emp_id] = rec


def _load_directory(force: bool = False) -> None:
    """Bulk-load /api/employees/directory into the cache (TTL-gated)."""
    global _dir_loaded_at
    if not force and _dir_loaded_at and (time.time() - _dir_loaded_at) < HR_DIRECTORY_TTL:
        if _dir_by_email or _dir_by_id:
            return
    data = _get("/api/employees/directory")
    if data is None:
        # Keep any existing cache as last-known-good; just don't reset the TTL
        # so we retry on the next call.
        if not (_dir_by_email or _dir_by_id):
            _dir_loaded_at = 0.0
        return
    employees = data.get("employees") or []
    # Rebuild from a fresh fetch (don't mix stale + new).
    _dir_by_email.clear()
    _dir_by_id.clear()
    for rec in employees:
        _index_record(rec)
    _dir_loaded_at = time.time()
    logger.info(
        "HR directory: cached %d employees (ttl=%.0fs)", len(employees), HR_DIRECTORY_TTL)


# ---------------------------------------------------------------------------
# Department → CC role mapping (CC-side table; only the dept *string* comes from HR)
# ---------------------------------------------------------------------------

def _map_department_to_role(department: str) -> Optional[str]:
    if not department:
        return None
    try:
        from db_auth import get_all_department_mappings_dict
        mapping = get_all_department_mappings_dict()
    except Exception as exc:
        logger.warning("department mapping load failed: %s", exc)
        return None
    return mapping.get(department.lower().strip())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_employee_by_id(employee_id: str) -> Optional[dict]:
    """Full HR record for an employee_id. Cache-first, with a /show fallback
    for someone not yet in a cached directory cycle."""
    if not employee_id:
        return None
    _load_directory()
    rec = _dir_by_id.get(employee_id.strip())
    if rec:
        return rec
    data = _get(f"/api/employees/show/{employee_id.strip()}")
    if data and isinstance(data, dict):
        _index_record(data)
        return data
    return None


def get_employee_by_email(email: str) -> Optional[dict]:
    """Full HR record for an email (cache-only; HR has no email-keyed lookup)."""
    if not email:
        return None
    _load_directory()
    return _dir_by_email.get(email.strip().lower())


def get_department_for_email(email: str) -> Optional[str]:
    rec = get_employee_by_email(email)
    return (rec or {}).get("department") or None


def get_department_for_employee_id(employee_id: str) -> Optional[str]:
    rec = get_employee_by_id(employee_id)
    return (rec or {}).get("department") or None


def get_cc_role_for_email(email: str) -> Optional[str]:
    return _map_department_to_role(get_department_for_email(email) or "")


def get_cc_role_for_employee_id(employee_id: str) -> Optional[str]:
    return _map_department_to_role(get_department_for_employee_id(employee_id) or "")


def get_all_pr_departments() -> list[dict]:
    """HR departments, shaped as the historical PRDepartment objects
    ({id,name,code,org,org_name,active}) so the admin department→role picker
    UI and the /admin/pr-departments endpoint stay unchanged."""
    global _dir_meta, _dir_meta_loaded_at
    if _dir_meta is None or (time.time() - _dir_meta_loaded_at) >= HR_DIRECTORY_TTL:
        data = _get("/api/employees/meta")
        if data and isinstance(data, dict):
            _dir_meta = list(data.get("departments") or [])
            _dir_meta_loaded_at = time.time()
            logger.info("HR meta: %d departments", len(_dir_meta))
    result = []
    for name in (_dir_meta or []):
        name = str(name).strip()
        if not name:
            continue
        result.append({
            "id": name,
            "name": name,
            "code": "",
            "org": "",
            "org_name": "",
            "active": True,
        })
    result.sort(key=lambda d: d["name"].lower())
    return result


def lookup_employee_minimal(employee_id: str) -> Optional[dict]:
    """Minimal sign-in lookup (employee_id, name, email, role) via HR /lookup.
    This is the documented sign-in endpoint; department is resolved separately
    via the directory cache. Returns None on 404."""
    return _get(f"/api/employees/lookup/{employee_id.strip()}")


def reload() -> None:
    """Invalidate caches (call after admin mapping/role edits)."""
    _invalidate()
