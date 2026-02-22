"""
1PWR Customer Care Portal API
==============================
FastAPI service providing:
  - Customer data CRUD from 1PDB (PostgreSQL)
  - Schema introspection and data export (CSV/XLSX)
  - Dual auth: customer self-service + employee date-based login
  - Role-based access control (superadmin, onm_team, finance_team, generic)
  - Employee name lookup via HR portal API

Runs on the Linux EC2 at 0.0.0.0:8100.

Environment variables:
  DATABASE_URL        - PostgreSQL connection string (required)
  ACDB_PORT           - Port to bind         (default: 8100)
  CC_JWT_SECRET       - JWT signing secret   (default: dev secret)
  CC_JWT_EXPIRY_HOURS - Token lifetime       (default: 8)
  CC_AUTH_DB          - SQLite auth DB path  (default: ./cc_auth.db)
  HR_PORTAL_URL       - HR portal base URL   (default: http://13.246.55.153)
  HR_PORTAL_API_KEY   - API key for HR lookup (default: empty)
"""

import os
import re
import sys
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.pool
import psycopg2.extras
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cc-api")

PORT = int(os.environ.get("ACDB_PORT", "8100"))

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Lazy-initialize the connection pool."""
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
        )
    return _pool


@contextmanager
def get_connection():
    """Context manager for PostgreSQL connections from the pool."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


# Keep get_derived_connection as an alias for backward compatibility.
# With PostgreSQL, everything is in one database — no separate derived DB.
get_derived_connection = get_connection


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    """Convert a psycopg2 row to a dict with column names."""
    if row is None:
        return {}
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def _normalize_phone(phone: str) -> str:
    """Strip common prefixes and non-digit chars for matching."""
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("266") and len(digits) > 9:
        digits = digits[3:]
    digits = digits.lstrip("0")
    return digits


def _normalize_customer(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a PostgreSQL customer row into a clean API response."""
    def _s(key):
        v = row_dict.get(key)
        return str(v).strip() if v is not None else ""

    return {
        "customer_id": _s("customer_id_legacy"),
        "first_name": _s("first_name"),
        "middle_name": _s("middle_name"),
        "last_name": _s("last_name"),
        "phone": _s("phone"),
        "cell_phone_1": _s("cell_phone_1"),
        "cell_phone_2": _s("cell_phone_2"),
        "email": _s("email"),
        "plot_number": _s("plot_number"),
        "street_address": _s("street_address"),
        "city": _s("city"),
        "district": _s("district"),
        "concession": _s("community"),
        "date_connected": _s("date_service_connected"),
        "date_terminated": _s("date_service_terminated"),
    }


def _derive_account_from_plot(plot_number: str, concession: str) -> Optional[str]:
    """Derive account number from the PLOT NUMBER field.

    PLOT NUMBER format: ``XXX NNNN[A-Z] TT``  e.g.  ``MAK 0045 HH``
    Account number format: ``NNNNXXX``          e.g.  ``0045MAK``
    """
    if not plot_number or not concession:
        return None
    plot = str(plot_number).strip()
    conc = str(concession).strip().upper()
    if not plot or plot.lower() == "none":
        return None
    m = re.match(r"^[A-Za-z]{2,4}\s+(\d{3,4})[A-Za-z]?\s+\S+", plot)
    if m:
        num = m.group(1).zfill(4)
        return f"{num}{conc}"
    return None


def _resolve_accounts_for_customer(cursor, customer_id_legacy: str) -> List[str]:
    """Resolve all known account numbers for a customer.

    Checks:
      1. accounts table (primary)
      2. meters table (account_number field)
      3. Derivation from plot_number in customers
    """
    accounts: set = set()
    cid = str(customer_id_legacy).strip()

    # 1. accounts table
    try:
        cursor.execute(
            "SELECT a.account_number FROM accounts a "
            "JOIN customers c ON a.customer_id = c.id "
            "WHERE c.customer_id_legacy = %s",
            (int(cid),),
        )
        for r in cursor.fetchall():
            if r[0]:
                accounts.add(str(r[0]).strip())
    except Exception:
        pass

    # 2. meters table
    try:
        cursor.execute(
            "SELECT account_number FROM meters "
            "WHERE customer_id_legacy = %s AND account_number IS NOT NULL",
            (int(cid),),
        )
        for r in cursor.fetchall():
            if r[0]:
                accounts.add(str(r[0]).strip())
    except Exception:
        pass

    # 3. Derive from plot_number
    if not accounts:
        try:
            cursor.execute(
                "SELECT plot_number, community FROM customers "
                "WHERE customer_id_legacy = %s",
                (int(cid),),
            )
            row = cursor.fetchone()
            if row:
                derived = _derive_account_from_plot(
                    str(row[0] or ""), str(row[1] or "")
                )
                if derived:
                    accounts.add(derived)
        except Exception:
            pass

    return sorted(accounts)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="1PWR Customer Care Portal API",
    description="Customer data management, schema introspection, export, and role-based access.",
    version="3.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Mount sub-routers (auth, schema, CRUD, export, admin)
# ---------------------------------------------------------------------------
from auth import router as auth_router
from schema import router as schema_router
from crud import router as crud_router, customer_router, customer_data_router, _ensure_soft_delete_table
from exports import router as export_router
from admin import router as admin_router
from stats import router as stats_router
from mutations import router as mutations_router
from om_report import router as om_report_router
from sync_ugridplan import router as sync_router
from commission import router as commission_router
from tariff import router as tariff_router
from registration import router as registration_router
from payments import router as payments_router
from ingest import router as ingest_router
from meter_lifecycle import router as meter_lifecycle_router, ensure_meter_assignments_table

from db_auth import init_auth_db
init_auth_db()

app.include_router(auth_router)
app.include_router(schema_router)
app.include_router(crud_router)
_ensure_soft_delete_table()
app.include_router(customer_router)
app.include_router(export_router)
app.include_router(admin_router)
app.include_router(stats_router)
app.include_router(mutations_router)
app.include_router(om_report_router)
app.include_router(sync_router)
app.include_router(commission_router)
app.include_router(tariff_router)
app.include_router(customer_data_router)
app.include_router(registration_router)
app.include_router(payments_router)
app.include_router(ingest_router)
app.include_router(meter_lifecycle_router)
ensure_meter_assignments_table()


# ---- Country config ----

@app.get("/api/config")
def country_config_endpoint():
    """Return country-specific metadata for the frontend."""
    from country_config import COUNTRY
    return {
        "country_code": COUNTRY.code,
        "country_name": COUNTRY.name,
        "currency": COUNTRY.currency,
        "currency_symbol": COUNTRY.currency_symbol,
        "dial_code": COUNTRY.dial_code,
        "sites": COUNTRY.site_abbrev,
    }


# ---- Health ----

@app.get("/health")
@app.get("/api/health")
def health():
    """Health check including DB connectivity."""
    status = {"status": "ok", "database": "postgresql", "timestamp": datetime.now().isoformat()}

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM customers")
            count = cursor.fetchone()[0]
            status["customer_count"] = count

            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            status["tables"] = [r[0] for r in cursor.fetchall()]
    except Exception as e:
        status["status"] = "db_error"
        status["error"] = str(e)

    return status


# ---- Lookup by phone ----

@app.get("/customers/by-phone/{phone}")
def customer_by_phone(phone: str):
    """Look up a customer by phone number."""
    normalized = _normalize_phone(phone)
    if len(normalized) < 5:
        raise HTTPException(status_code=400, detail="Phone number too short")

    like_pattern = f"%{normalized[-8:]}"

    sql = """
        SELECT * FROM customers
        WHERE phone LIKE %s
           OR cell_phone_1 LIKE %s
           OR cell_phone_2 LIKE %s
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (like_pattern, like_pattern, like_pattern))
            rows = cursor.fetchall()

            if not rows:
                raise HTTPException(status_code=404, detail="No customer found for this phone number")

            customers = [_normalize_customer(_row_to_dict(cursor, row)) for row in rows]

            for cust in customers:
                cid = cust["customer_id"]
                if cid:
                    cust["account_numbers"] = _resolve_accounts_for_customer(cursor, cid)
                else:
                    cust["account_numbers"] = []

            return {"customers": customers, "count": len(customers)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Phone lookup failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---- Lookup by customer ID ----

@app.get("/customers/by-id/{customer_id}")
def customer_by_id(customer_id: str):
    """Look up a customer by their legacy CUSTOMER ID."""
    sql = "SELECT * FROM customers WHERE customer_id_legacy = %s"

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (int(customer_id),))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail=f"No customer with ID {customer_id}")

            cust = _normalize_customer(_row_to_dict(cursor, row))
            cust["account_numbers"] = _resolve_accounts_for_customer(cursor, customer_id)

            return {"customer": cust}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("ID lookup failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---- Lookup by account number ----

@app.get("/customers/by-account/{account_number}")
def customer_by_account(account_number: str):
    """Look up a customer by their account number."""
    acct = account_number.strip()

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cust_id = None

            # 1. accounts table
            try:
                cursor.execute(
                    "SELECT customer_id FROM accounts WHERE account_number = %s",
                    (acct,),
                )
                r = cursor.fetchone()
                if r and r[0]:
                    cust_id = r[0]  # This is the PG id
            except Exception:
                pass

            # 2. meters table
            if not cust_id:
                try:
                    cursor.execute(
                        "SELECT c.id FROM meters m "
                        "JOIN customers c ON m.customer_id_legacy = c.customer_id_legacy "
                        "WHERE m.account_number = %s",
                        (acct,),
                    )
                    r = cursor.fetchone()
                    if r and r[0]:
                        cust_id = r[0]
                except Exception:
                    pass

            if not cust_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"No customer with account {account_number}",
                )

            cursor.execute("SELECT * FROM customers WHERE id = %s", (cust_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Customer ID {cust_id} (from account {acct}) not found",
                )

            cust = _normalize_customer(_row_to_dict(cursor, row))
            cust["account_numbers"] = _resolve_accounts_for_customer(
                cursor, cust["customer_id"]
            )

            return {"customer": cust}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Account lookup failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---- General search ----

@app.get("/customers/search")
def customer_search(
    q: str = Query(..., min_length=2, description="Search query (name, village, plot number)"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search customers by name, village/concession, or plot number."""
    pattern = f"%{q}%"

    sql = """
        SELECT * FROM customers
        WHERE first_name ILIKE %s
           OR last_name ILIKE %s
           OR middle_name ILIKE %s
           OR community ILIKE %s
           OR plot_number ILIKE %s
           OR city ILIKE %s
           OR district ILIKE %s
           OR customer_id_legacy::text LIKE %s
        LIMIT %s
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (
                pattern, pattern, pattern, pattern, pattern,
                pattern, pattern, pattern, limit,
            ))
            rows = cursor.fetchall()
            customers = [_normalize_customer(_row_to_dict(cursor, row)) for row in rows]
            return {"customers": customers, "count": len(customers), "query": q}

    except Exception as e:
        logger.error("Search failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---- Sites/concessions list ----

@app.get("/sites")
@app.get("/api/sites")
def list_sites():
    """List all distinct concession names (sites) in the customer database."""
    sql = """
        SELECT community, COUNT(*) AS customer_count
        FROM customers
        WHERE community IS NOT NULL AND community <> ''
        GROUP BY community
        ORDER BY community
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()

            sites = [
                {"concession": row[0], "customer_count": row[1]}
                for row in rows if row[0]
            ]

            return {"sites": sites, "total_sites": len(sites)}

    except Exception as e:
        logger.error("Sites list failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from db_auth import init_auth_db
    init_auth_db()

    logger.info("=" * 60)
    logger.info("1PWR Customer Care Portal API v3.0 (PostgreSQL)")
    logger.info("Database: %s", DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL)
    logger.info("Port: %d", PORT)
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
