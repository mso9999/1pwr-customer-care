"""
PR System (Firestore) department lookup for CC portal role auto-mapping.

Loads ALL users from the PR Firestore `users` collection once on first call,
building an in-memory map of email → department → CC role.

When employee_id cannot be resolved to an email by the HR portal, a local
SQLite table (cc_employee_emails) provides a fallback mapping.

Roles:
  - onm_team:     O&M, Reticulation, Production, Engineering, EHS, PUECO,
                  Asset Management, Fleet, Facilities
  - finance_team: Finance, CFO
  - superadmin:   manual assignment only (not auto-mapped)
  - generic:      everything else or lookup failure
"""

import os
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger("acdb-api.pr-lookup")

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
# Department → CC Role mapping
# ---------------------------------------------------------------------------

_DEPT_TO_ROLE: dict[str, str] = {}

_ONM_DEPARTMENTS = [
    "o&m", "o_m", "o_m_smp",
    "reticulation", "reticulation_smp",
    "production", "production_smp",
    "engineering", "mechanical_engineering",
    "ehs",
    "pueco",
    "asset_management", "asset management",
    "fleet",
    "facilities",
]

_FINANCE_DEPARTMENTS = [
    "finance",
    "cfo",
]

for _d in _ONM_DEPARTMENTS:
    _DEPT_TO_ROLE[_d.lower()] = "onm_team"

for _d in _FINANCE_DEPARTMENTS:
    _DEPT_TO_ROLE[_d.lower()] = "finance_team"


def _map_department_to_role(department: str) -> Optional[str]:
    """Map a PR department string to a CCRole value, or None if no mapping."""
    if not department:
        return None
    return _DEPT_TO_ROLE.get(department.lower().strip())


# ---------------------------------------------------------------------------
# Preloaded email → role cache (all Firestore users, loaded once)
# ---------------------------------------------------------------------------

_email_role_cache: dict[str, Optional[str]] = {}  # lowered email → role
_cache_loaded = False


def _load_all_firestore_users():
    """Bulk-load all PR Firestore users into _email_role_cache."""
    global _cache_loaded

    if _cache_loaded:
        return

    _cache_loaded = True  # mark even on failure so we don't retry every call

    if not _ensure_firebase() or _firestore_db is None:
        return

    try:
        count = 0
        for doc in _firestore_db.collection("users").stream():
            data = doc.to_dict()
            email = (data.get("email") or "").strip().lower()
            department = str(data.get("department", "")).strip()
            if email:
                role = _map_department_to_role(department)
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
