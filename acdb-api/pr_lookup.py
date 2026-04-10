"""
PR System (Firestore) integration for CC portal.

1. Department lookup for role auto-mapping (email → department → CC role).
2. Portfolio / organization list sourced from ``referenceData_organizations``.

Firebase Admin SDK authenticates via the service-account JSON referenced by
``FIREBASE_SA_PATH`` (default: ``firebase-service-account.json`` next to this file).
"""

import logging
import os
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter

logger = logging.getLogger("acdb-api.pr-lookup")

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])

# ---------------------------------------------------------------------------
# Firebase initialisation (lazy)
# ---------------------------------------------------------------------------

_firebase_initialised = False
_firestore_db = None

FIREBASE_SA_PATH = os.environ.get(
    "FIREBASE_SA_PATH",
    os.path.join(os.path.dirname(__file__), "firebase-service-account.json"),
)


def _ensure_firebase():
    """Initialise Firebase Admin SDK once (lazy)."""
    global _firebase_initialised, _firestore_db

    if _firebase_initialised:
        return _firestore_db is not None

    _firebase_initialised = True

    if not os.path.isfile(FIREBASE_SA_PATH):
        logger.warning(
            "Firebase service account not found at %s — PR department lookup disabled",
            FIREBASE_SA_PATH,
        )
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_SA_PATH)
            firebase_admin.initialize_app(cred)

        _firestore_db = firestore.client()
        logger.info("Connected to PR Firestore for department lookup")
        return True
    except Exception as e:
        logger.error("Failed to initialise Firebase: %s", e)
        return False


# ---------------------------------------------------------------------------
# Department → CC Role mapping (DB-backed via cc_department_role_mappings)
# ---------------------------------------------------------------------------

_dept_role_map: dict[str, str] = {}  # populated from SQLite


def _reload_dept_role_map():
    """Refresh the in-memory department→role dict from SQLite."""
    global _dept_role_map
    try:
        from db_auth import get_all_department_mappings_dict
        _dept_role_map = get_all_department_mappings_dict()
        logger.debug("Loaded %d department→role mappings from DB", len(_dept_role_map))
    except Exception as e:
        logger.error("Failed to load department mappings from DB: %s", e)


def reload_department_mappings():
    """Public helper — call after admin writes a mapping change."""
    _reload_dept_role_map()
    _invalidate_user_cache()


def _map_department_to_role(department: str) -> Optional[str]:
    """Map a PR department string to a CCRole value, or None if no mapping."""
    if not department:
        return None
    if not _dept_role_map:
        _reload_dept_role_map()
    return _dept_role_map.get(department.lower().strip())


# ---------------------------------------------------------------------------
# Firestore referenceData_departments resolution cache
# ---------------------------------------------------------------------------

_ref_departments: dict[str, dict] = {}  # doc_id → {name, code, org}
_ref_depts_loaded = False


def _load_reference_departments():
    """Bulk-load referenceData_departments for ID→name resolution."""
    global _ref_depts_loaded, _ref_departments

    if _ref_depts_loaded:
        return

    _ref_depts_loaded = True

    if not _ensure_firebase() or _firestore_db is None:
        return

    try:
        count = 0
        for doc in _firestore_db.collection("referenceData_departments").stream():
            d = doc.to_dict()
            org_data = d.get("organization")
            org_id = ""
            if isinstance(org_data, dict):
                org_id = org_data.get("id", "")
            _ref_departments[doc.id] = {
                "name": d.get("name", ""),
                "code": d.get("code", ""),
                "org": org_id,
                "org_name": org_data.get("name", "") if isinstance(org_data, dict) else "",
                "active": d.get("active", True),
            }
            count += 1
        logger.info("Loaded %d referenceData_departments from Firestore", count)
    except Exception as e:
        logger.error("Failed to load referenceData_departments: %s", e)


def _resolve_department(raw_department: str) -> list[str]:
    """Return candidate strings to try against the mapping table.

    For readable departments (e.g. "O&M"), returns [raw].
    For Firestore doc IDs, resolves via referenceData_departments and
    returns [name, code] (lowercased) so any of them can match.
    """
    if not raw_department:
        return []

    _load_reference_departments()

    ref = _ref_departments.get(raw_department)
    if ref:
        candidates = [raw_department.lower()]
        if ref["name"]:
            candidates.append(ref["name"].lower().strip())
        if ref["code"]:
            candidates.append(ref["code"].lower().strip())
        return candidates

    return [raw_department.lower().strip()]


def get_all_pr_departments() -> list[dict]:
    """Return every referenceData_department for the admin UI."""
    _load_reference_departments()
    result = []
    for doc_id, info in _ref_departments.items():
        result.append({
            "id": doc_id,
            "name": info["name"],
            "code": info["code"],
            "org": info["org"],
            "org_name": info["org_name"],
            "active": info["active"],
        })
    result.sort(key=lambda d: (d["org"], d["name"]))
    return result


# ---------------------------------------------------------------------------
# Preloaded email → role cache (all Firestore users, loaded once)
# ---------------------------------------------------------------------------

_email_role_cache: dict[str, Optional[str]] = {}  # lowered email → role
_cache_loaded = False


def _invalidate_user_cache():
    """Force re-evaluation of user roles on next lookup."""
    global _cache_loaded
    _cache_loaded = False
    _email_role_cache.clear()


def _load_all_firestore_users():
    """Bulk-load all PR Firestore users into _email_role_cache."""
    global _cache_loaded

    if _cache_loaded:
        return

    _cache_loaded = True

    if not _ensure_firebase() or _firestore_db is None:
        return

    if not _dept_role_map:
        _reload_dept_role_map()

    try:
        count = 0
        for doc in _firestore_db.collection("users").stream():
            data = doc.to_dict()
            email = (data.get("email") or "").strip().lower()
            raw_dept = str(data.get("department", "")).strip()
            if email:
                role = None
                for candidate in _resolve_department(raw_dept):
                    role = _map_department_to_role(candidate)
                    if role:
                        break
                _email_role_cache[email] = role
                count += 1

        logger.info(
            "PR lookup: preloaded %d users from Firestore (%d with mapped roles)",
            count,
            sum(1 for v in _email_role_cache.values() if v is not None),
        )
    except Exception as e:
        logger.error("Failed to preload PR Firestore users: %s", e)


# ---------------------------------------------------------------------------
# SQLite fallback: employee_id → email mapping
# ---------------------------------------------------------------------------

_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "cc_auth.db")


def _ensure_email_table():
    """Create cc_employee_emails table if it doesn't exist."""
    try:
        conn = sqlite3.connect(_SQLITE_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cc_employee_emails (
                employee_id TEXT PRIMARY KEY,
                email TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to create cc_employee_emails table: %s", e)


def get_employee_email(employee_id: str) -> Optional[str]:
    """Look up cached email for an employee_id from SQLite."""
    try:
        _ensure_email_table()
        conn = sqlite3.connect(_SQLITE_PATH)
        row = conn.execute(
            "SELECT email FROM cc_employee_emails WHERE employee_id = ?",
            (str(employee_id),),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error("SQLite email lookup failed for %s: %s", employee_id, e)
        return None


def set_employee_email(employee_id: str, email: str):
    """Store an employee_id → email mapping in SQLite."""
    try:
        _ensure_email_table()
        conn = sqlite3.connect(_SQLITE_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO cc_employee_emails (employee_id, email) VALUES (?, ?)",
            (str(employee_id), email.lower().strip()),
        )
        conn.commit()
        conn.close()
        logger.info("Stored email mapping: %s → %s", employee_id, email)
    except Exception as e:
        logger.error("SQLite email store failed for %s: %s", employee_id, e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cc_role_for_email(email: str) -> Optional[str]:
    """
    Look up the CC portal role for a given email address.

    Uses the preloaded Firestore cache (case-insensitive).
    Returns the mapped role string or None.
    """
    if not email:
        return None

    _load_all_firestore_users()

    email_lower = email.lower().strip()
    if email_lower in _email_role_cache:
        return _email_role_cache[email_lower]

    # Not found in Firestore
    logger.debug("No PR user found for email %s", email_lower)
    return None


def get_cc_role_for_employee_id(employee_id: str) -> Optional[str]:
    """
    Look up the CC portal role for an employee_id.

    1. Checks the SQLite cc_employee_emails table for a stored email mapping
    2. Uses that email to look up the department in the Firestore cache
    3. Returns the mapped role string or None
    """
    email = get_employee_email(employee_id)
    if not email:
        return None

    return get_cc_role_for_email(email)


# ---------------------------------------------------------------------------
# Portfolio / organization endpoint
# ---------------------------------------------------------------------------

_portfolio_cache: list[dict] | None = None


def _normalize_site_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip().upper() for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        return [str(key).strip().upper() for key in value.keys() if str(key).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().upper() for item in value if str(item).strip()]
    return []


@router.get("")
def list_portfolios():
    """Return active organizations from the PR system's Firestore."""
    global _portfolio_cache

    if _portfolio_cache is not None:
        return _portfolio_cache

    if not _ensure_firebase() or _firestore_db is None:
        return []

    try:
        docs = (
            _firestore_db.collection("referenceData_organizations")
            .where("active", "==", True)
            .stream()
        )
        result = []
        for doc in docs:
            d = doc.to_dict()
            result.append({
                "id": doc.id,
                "name": d.get("name", doc.id),
                "code": d.get("code"),
                "country": d.get("country"),
                "baseCurrency": d.get("baseCurrency", "USD"),
                "allowedCurrencies": d.get("allowedCurrencies", []),
                "siteIds": _normalize_site_ids(
                    d.get("siteIds") or d.get("site_ids") or d.get("sites")
                ),
            })

        _portfolio_cache = result
        logger.info("Loaded %d portfolios from PR Firestore", len(result))
        return result
    except Exception as e:
        logger.error("Failed to fetch portfolios: %s", e)
        return []
