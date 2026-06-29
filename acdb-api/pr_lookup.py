"""
Employee department affiliation + portfolio/organization lookups for CC.

Employee + department source (HR):
    Employee records and their department associations are now sourced from
    the HR portal (``hr.1pwrafrica.com``) via :mod:`hr_directory`, which is the
    authoritative system of record. The department string from HR is mapped to
    a CC role using the CC-side ``cc_department_role_mappings`` table
    (managed in Admin → Roles). Previously this came from the PR system's
    Firestore; HR replaced it as canonical on 2026-06-29.

    The public helpers below (``get_cc_role_for_*``, ``get_department_for_*``,
    ``get_all_pr_departments``, ``reload_department_mappings``) are preserved as
    thin delegates so callers (auth.py, admin.py) need no changes.

Portfolio / organization list (Firestore):
    The portfolio/organization reference data still comes from the PR system's
    Firestore ``referenceData_organizations`` collection — that is reference
    data about sites/orgs, not employees, and is unaffected by the HR move.

Firebase Admin SDK authenticates via the service-account JSON referenced by
``FIREBASE_SA_PATH`` (default: ``firebase-service-account.json`` next to this file).
"""

import logging
import os
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter

import hr_directory

logger = logging.getLogger("acdb-api.pr-lookup")

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])

# ---------------------------------------------------------------------------
# Firebase initialisation (lazy) — used only for portfolio/org reference data
# ---------------------------------------------------------------------------

_firebase_initialised = False
_firestore_db = None

FIREBASE_SA_PATH = os.environ.get(
    "FIREBASE_SA_PATH",
    os.path.join(os.path.dirname(__file__), "firebase-service-account.json"),
)


def _ensure_firebase():
    """Initialise Firebase Admin SDK once (lazy). Only needed for the
    portfolio/organization list; employee lookups go through HR now."""
    global _firebase_initialised, _firestore_db

    if _firebase_initialised:
        return _firestore_db is not None

    _firebase_initialised = True

    if not os.path.isfile(FIREBASE_SA_PATH):
        logger.warning(
            "Firebase service account not found at %s — portfolio list disabled",
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
        logger.info("Connected to PR Firestore for portfolio/organization list")
        return True
    except Exception as e:
        logger.error("Failed to initialise Firebase: %s", e)
        return False


# ---------------------------------------------------------------------------
# Department → CC role mapping (delegates to HR directory + CC mapping table)
# ---------------------------------------------------------------------------

def reload_department_mappings():
    """Invalidate HR directory caches after an admin mapping/role edit.
    Kept under the historical name so admin.py need not change."""
    hr_directory.reload()


# ---------------------------------------------------------------------------
# Public employee/department API (delegates to hr_directory)
# ---------------------------------------------------------------------------

def get_cc_role_for_email(email: str) -> Optional[str]:
    return hr_directory.get_cc_role_for_email(email)


def get_cc_role_for_employee_id(employee_id: str) -> Optional[str]:
    return hr_directory.get_cc_role_for_employee_id(employee_id)


def get_department_for_email(email: str) -> Optional[str]:
    return hr_directory.get_department_for_email(email)


def get_department_for_employee_id(employee_id: str) -> Optional[str]:
    return hr_directory.get_department_for_employee_id(employee_id)


def get_all_pr_departments() -> list[dict]:
    """HR departments, shaped for the admin department→role picker UI.
    Name kept for backward compatibility with admin.py / the frontend."""
    return hr_directory.get_all_pr_departments()


# ---------------------------------------------------------------------------
# SQLite fallback: employee_id → email mapping (legacy cache)
# ---------------------------------------------------------------------------
# No longer required for role resolution (HR is keyed by employee_id directly),
# but still populated at login for diagnostic continuity. Harmless to keep.

_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "cc_auth.db")


def _ensure_email_table():
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
