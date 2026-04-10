"""
SQLite connection manager for the CC portal auth database.

Manages:
  - cc_customer_passwords: customer self-service passwords (bcrypt)
  - cc_employee_roles: superadmin-assigned CC roles for employees
  - cc_department_role_mappings: PR department → CC role auto-mapping
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger("acdb-api.auth-db")

AUTH_DB_PATH = os.environ.get("CC_AUTH_DB", os.path.join(os.path.dirname(__file__), "cc_auth.db"))


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_auth_db():
    """Context manager for the auth SQLite database."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_auth_db():
    """Create auth tables if they don't exist."""
    with get_auth_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cc_customer_passwords (
                customer_id   TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS cc_employee_roles (
                employee_id  TEXT PRIMARY KEY,
                cc_role      TEXT NOT NULL DEFAULT 'generic',
                assigned_by  TEXT NOT NULL DEFAULT '',
                assigned_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS cc_customer_metadata (
                customer_id    TEXT PRIMARY KEY,
                customer_type  TEXT,
                meter_serial   TEXT,
                gps_x          REAL,
                gps_y          REAL,
                ugp_survey_id  TEXT,
                ugp_project_id TEXT,
                synced_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS cc_site_projects (
                site_code   TEXT PRIMARY KEY,
                project_id  TEXT NOT NULL,
                site_name   TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS cc_mutations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
                user_type   TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                user_name   TEXT NOT NULL DEFAULT '',
                action      TEXT NOT NULL,
                table_name  TEXT NOT NULL,
                record_id   TEXT NOT NULL,
                old_values  TEXT,
                new_values  TEXT,
                reverted    INTEGER NOT NULL DEFAULT 0,
                reverted_by TEXT,
                reverted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS cc_tariff_overrides (
                scope           TEXT NOT NULL,
                scope_key       TEXT NOT NULL,
                rate_lsl        REAL NOT NULL,
                effective_from  TEXT NOT NULL DEFAULT (datetime('now')),
                set_by          TEXT NOT NULL,
                set_by_name     TEXT NOT NULL DEFAULT '',
                set_at          TEXT NOT NULL DEFAULT (datetime('now')),
                notes           TEXT DEFAULT '',
                PRIMARY KEY (scope, scope_key)
            );

            CREATE TABLE IF NOT EXISTS cc_tariff_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
                scope           TEXT NOT NULL,
                scope_key       TEXT NOT NULL,
                rate_lsl        REAL NOT NULL,
                previous_rate   REAL,
                effective_from  TEXT NOT NULL,
                set_by          TEXT NOT NULL,
                set_by_name     TEXT NOT NULL DEFAULT '',
                notes           TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS cc_department_role_mappings (
                department_key TEXT PRIMARY KEY,
                cc_role        TEXT NOT NULL,
                label          TEXT NOT NULL DEFAULT '',
                added_by       TEXT NOT NULL DEFAULT 'system',
                added_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Seed default superadmin if not already present
        existing = conn.execute(
            "SELECT 1 FROM cc_employee_roles WHERE employee_id = '00'"
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO cc_employee_roles (employee_id, cc_role, assigned_by, assigned_at)
                   VALUES ('00', 'superadmin', 'system', datetime('now'))"""
            )
            logger.info("Seeded superadmin role for employee 00 (Matt Orosz)")

        _seed_department_mappings(conn)

    logger.info("Auth database initialized at %s", AUTH_DB_PATH)


_DEFAULT_DEPT_MAPPINGS: list[tuple[str, str, str]] = [
    # key, cc_role, label — LS English
    ("o&m",                            "onm_team",      "O&M"),
    ("o_m",                            "onm_team",      "O&M (alt)"),
    ("finance",                        "finance_team",  "Finance"),
    ("cfo",                            "finance_team",  "CFO"),
    # BN / MGB French
    ("exploitation et maintenance",    "onm_team",      "Exploitation et Maintenance"),
    ("em",                             "onm_team",      "EM (code)"),
    ("fin",                            "finance_team",  "FIN (code)"),
    ("service client",                 "onm_team",      "Service client"),
    ("sc",                             "onm_team",      "SC (code)"),
]


def _seed_department_mappings(conn: sqlite3.Connection):
    """Insert default department→role mappings if the table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM cc_department_role_mappings").fetchone()[0]
    if count > 0:
        return
    for key, role, label in _DEFAULT_DEPT_MAPPINGS:
        conn.execute(
            """INSERT OR IGNORE INTO cc_department_role_mappings
               (department_key, cc_role, label, added_by)
               VALUES (?, ?, ?, 'system')""",
            (key.lower(), role, label),
        )
    logger.info("Seeded %d default department→role mappings", len(_DEFAULT_DEPT_MAPPINGS))


# ---------------------------------------------------------------------------
# Customer password operations
# ---------------------------------------------------------------------------

def get_customer_password_hash(customer_id: str) -> str | None:
    """Return bcrypt hash for a customer, or None if not registered."""
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT password_hash FROM cc_customer_passwords WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        return row["password_hash"] if row else None


def set_customer_password(customer_id: str, password_hash: str):
    """Insert or update a customer's password hash."""
    now = datetime.utcnow().isoformat()
    with get_auth_db() as conn:
        conn.execute(
            """INSERT INTO cc_customer_passwords (customer_id, password_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(customer_id) DO UPDATE SET password_hash = ?, updated_at = ?""",
            (customer_id, password_hash, now, now, password_hash, now),
        )


def customer_is_registered(customer_id: str) -> bool:
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM cc_customer_passwords WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# Employee role operations
# ---------------------------------------------------------------------------

def get_employee_role(employee_id: str) -> str | None:
    """Return the CC role for an employee, or None (defaults to 'generic')."""
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT cc_role FROM cc_employee_roles WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        return row["cc_role"] if row else None


def set_employee_role(employee_id: str, cc_role: str, assigned_by: str):
    now = datetime.utcnow().isoformat()
    with get_auth_db() as conn:
        conn.execute(
            """INSERT INTO cc_employee_roles (employee_id, cc_role, assigned_by, assigned_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(employee_id) DO UPDATE SET cc_role = ?, assigned_by = ?, assigned_at = ?""",
            (employee_id, cc_role, assigned_by, now, cc_role, assigned_by, now),
        )


def delete_employee_role(employee_id: str) -> bool:
    with get_auth_db() as conn:
        cursor = conn.execute(
            "DELETE FROM cc_employee_roles WHERE employee_id = ?",
            (employee_id,),
        )
        return cursor.rowcount > 0


def list_employee_roles() -> list[dict]:
    with get_auth_db() as conn:
        rows = conn.execute(
            "SELECT employee_id, cc_role, assigned_by, assigned_at FROM cc_employee_roles ORDER BY employee_id"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Department → role mapping operations
# ---------------------------------------------------------------------------

def list_department_mappings() -> list[dict]:
    with get_auth_db() as conn:
        rows = conn.execute(
            "SELECT department_key, cc_role, label, added_by, added_at "
            "FROM cc_department_role_mappings ORDER BY cc_role, department_key"
        ).fetchall()
        return [dict(r) for r in rows]


def get_department_mapping(department_key: str) -> str | None:
    """Return the CC role for a department key, or None."""
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT cc_role FROM cc_department_role_mappings WHERE department_key = ?",
            (department_key.lower().strip(),),
        ).fetchone()
        return row["cc_role"] if row else None


def get_all_department_mappings_dict() -> dict[str, str]:
    """Return {department_key: cc_role} for all mappings (for in-memory cache)."""
    with get_auth_db() as conn:
        rows = conn.execute(
            "SELECT department_key, cc_role FROM cc_department_role_mappings"
        ).fetchall()
        return {r["department_key"]: r["cc_role"] for r in rows}


def set_department_mapping(department_key: str, cc_role: str, label: str, added_by: str):
    now = datetime.utcnow().isoformat()
    with get_auth_db() as conn:
        conn.execute(
            """INSERT INTO cc_department_role_mappings
               (department_key, cc_role, label, added_by, added_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(department_key) DO UPDATE SET
               cc_role = ?, label = ?, added_by = ?, added_at = ?""",
            (department_key.lower().strip(), cc_role, label, added_by, now,
             cc_role, label, added_by, now),
        )


def delete_department_mapping(department_key: str) -> bool:
    with get_auth_db() as conn:
        cursor = conn.execute(
            "DELETE FROM cc_department_role_mappings WHERE department_key = ?",
            (department_key.lower().strip(),),
        )
        return cursor.rowcount > 0
