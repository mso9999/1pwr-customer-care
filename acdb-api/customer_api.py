"""
1PWR Customer Care Portal API
==============================
FastAPI service providing:
  - Customer data CRUD from the 1PWR Access Database (ACCDB)
  - Schema introspection and data export (CSV/XLSX)
  - Dual auth: customer self-service + employee date-based login
  - Role-based access control (superadmin, onm_team, finance_team, generic)
  - Employee name lookup via HR portal API

Runs on the ACDB Windows EC2 at 0.0.0.0:8100.

Environment variables:
  ACDB_PATH          - Path to .accdb file  (default: auto-detect)
  DERIVED_DB_PATH    - Path to derived data .accdb (default: derived_data.accdb beside ACDB)
  ACDB_PORT          - Port to bind         (default: 8100)
  CC_JWT_SECRET      - JWT signing secret   (default: dev secret)
  CC_JWT_EXPIRY_HOURS - Token lifetime      (default: 8)
  CC_AUTH_DB         - SQLite auth DB path  (default: ./cc_auth.db)
  HR_PORTAL_URL      - HR portal base URL   (default: http://13.246.55.153)
  HR_PORTAL_API_KEY  - API key for HR lookup (default: empty)
"""

import os
import re
import sys
import glob
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import pyodbc
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
logger = logging.getLogger("acdb-api")

PORT = int(os.environ.get("ACDB_PORT", "8100"))

# Auto-detect the .accdb file
DEFAULT_SEARCH_PATHS = [
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\tuacc.accdb",
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\*.accdb",
    r"C:\Users\Administrator\Desktop\*.accdb",
    r"C:\Users\Administrator\Documents\*.accdb",
    r".tmp\tuacc.accdb",
]


def _find_accdb() -> str:
    """Find the .accdb file, preferring ACDB_PATH env var."""
    env_path = os.environ.get("ACDB_PATH", "")
    if env_path and os.path.isfile(env_path):
        return env_path

    for pattern in DEFAULT_SEARCH_PATHS:
        if "*" in pattern:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        elif os.path.isfile(pattern):
            return pattern

    logger.warning("No .accdb file found — set ACDB_PATH environment variable")
    return ""


DB_PATH = _find_accdb()
DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"

# Derived data DB (tblmonthlyconsumption, tblmonthlytransactions, tblhourlyconsumption).
# Kept separate from the main ACCDB because the 2 GB main file has no room for
# new tables and suffers corruption when written to concurrently.
DERIVED_DB_PATH = os.environ.get("DERIVED_DB_PATH", "")
if not DERIVED_DB_PATH and DB_PATH:
    DERIVED_DB_PATH = os.path.join(os.path.dirname(DB_PATH), "derived_data.accdb")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _connection_string() -> str:
    if not DB_PATH:
        raise RuntimeError("ACDB_PATH not configured and no .accdb file found")
    return f"Driver={DRIVER};DBQ={DB_PATH}"


def _derived_connection_string() -> str:
    if not DERIVED_DB_PATH:
        raise RuntimeError("DERIVED_DB_PATH not configured")
    return f"Driver={DRIVER};DBQ={DERIVED_DB_PATH}"


@contextmanager
def get_connection():
    """Context manager for main ACDB connections (source data, read-only)."""
    conn = pyodbc.connect(_connection_string(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_derived_connection():
    """Context manager for derived data DB (monthly consumption, transactions, hourly)."""
    if not DERIVED_DB_PATH or not os.path.isfile(DERIVED_DB_PATH):
        raise RuntimeError(
            f"Derived DB not found at {DERIVED_DB_PATH}. "
            "Run import_meter_readings.py to create it."
        )
    conn = pyodbc.connect(_derived_connection_string(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    """Convert a pyodbc Row to a dict with column names."""
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def _normalize_phone(phone: str) -> str:
    """Strip common prefixes and non-digit chars for matching."""
    digits = "".join(c for c in phone if c.isdigit())
    # Remove country code prefix (266 for Lesotho)
    if digits.startswith("266") and len(digits) > 9:
        digits = digits[3:]
    # Remove leading zeros
    digits = digits.lstrip("0")
    return digits


def _normalize_customer(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an ACDB customer row into a clean API response."""
    return {
        "customer_id": str(row_dict.get("CUSTOMER ID", "")).strip(),
        "first_name": str(row_dict.get("FIRST NAME", "")).strip(),
        "middle_name": str(row_dict.get("MIDDLE NAME", "")).strip(),
        "last_name": str(row_dict.get("LAST NAME", "")).strip(),
        "phone": str(row_dict.get("PHONE", "")).strip(),
        "cell_phone_1": str(row_dict.get("CELL PHONE 1", "")).strip(),
        "cell_phone_2": str(row_dict.get("CELL PHONE 2", "")).strip(),
        "email": str(row_dict.get("EMAIL", "")).strip(),
        "plot_number": str(row_dict.get("PLOT NUMBER", "")).strip(),
        "street_address": str(row_dict.get("STREET ADDRESS", "")).strip(),
        "city": str(row_dict.get("CITY", "")).strip(),
        "district": str(row_dict.get("DISTRICT", "")).strip(),
        "concession": str(row_dict.get("Concession name", "")).strip(),
        "date_connected": str(row_dict.get("DATE SERVICE CONNECTED", "")).strip(),
        "date_terminated": str(row_dict.get("DATE SERVICE TERMINATED", "")).strip(),
    }


def _derive_account_from_plot(plot_number: str, concession: str) -> Optional[str]:
    """Derive account number from the PLOT NUMBER field in tblcustomer.

    PLOT NUMBER format: ``XXX NNNN[A-Z] TT``  e.g.  ``MAK 0045 HH``
    Account number format: ``NNNNXXX``          e.g.  ``0045MAK``

    For sub-plots with a letter suffix (``KET 0914B HH``), the letter
    is dropped and the numeric root is used (``0914KET``).
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


def _resolve_accounts_for_customer(cursor, customer_id: str) -> List[str]:
    """Resolve all known account numbers for a customer.

    Checks (in order):
      1. ``tblaccountnumbers``
      2. ``tblmeter``
      3. ``Copy Of tblmeter``
      4. Derivation from ``PLOT NUMBER`` in ``tblcustomer``
    """
    accounts: set = set()
    cid = str(customer_id).strip()

    # 1. tblaccountnumbers
    try:
        cursor.execute(
            "SELECT accountnumber FROM tblaccountnumbers WHERE customerid = ?",
            (cid,),
        )
        for r in cursor.fetchall():
            if r[0]:
                accounts.add(str(r[0]).strip())
    except Exception:
        pass

    # 2. tblmeter / Copy Of tblmeter
    for table in ["tblmeter", "Copy Of tblmeter"]:
        try:
            cursor.execute(
                f"SELECT [accountnumber] FROM [{table}] "
                f"WHERE [customer id] = ? AND [accountnumber] IS NOT NULL",
                (int(cid),),
            )
            for r in cursor.fetchall():
                if r[0]:
                    accounts.add(str(r[0]).strip())
        except Exception:
            continue

    # 3. Derive from PLOT NUMBER
    if not accounts:
        try:
            cursor.execute(
                "SELECT [PLOT NUMBER], [Concession name] FROM tblcustomer "
                "WHERE [CUSTOMER ID] = ?",
                (cid,),
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
    description="Customer data management, schema introspection, export, and role-based access for 1PWR Lesotho.",
    version="2.0.0",
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
from crud import router as crud_router, customer_router, customer_data_router
from exports import router as export_router
from admin import router as admin_router
from stats import router as stats_router
from mutations import router as mutations_router
from om_report import router as om_report_router
from sync_ugridplan import router as sync_router
from commission import router as commission_router
from tariff import router as tariff_router

app.include_router(auth_router)
app.include_router(schema_router)
app.include_router(crud_router)
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


# ---- Health ----

@app.get("/health")
@app.get("/api/health")
def health():
    """Health check including DB connectivity."""
    status = {"status": "ok", "db_path": DB_PATH, "timestamp": datetime.now().isoformat()}
    if not DB_PATH:
        status["status"] = "no_db"
        status["error"] = "No .accdb file configured"
        return status

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tblcustomer")
            count = cursor.fetchone()[0]
            status["customer_count"] = count
    except Exception as e:
        status["status"] = "db_error"
        status["error"] = str(e)

    # Derived DB health
    status["derived_db_path"] = DERIVED_DB_PATH
    if DERIVED_DB_PATH and os.path.isfile(DERIVED_DB_PATH):
        try:
            with get_derived_connection() as dconn:
                dc = dconn.cursor()
                existing = {t.table_name.lower() for t in dc.tables(tableType="TABLE")}
                status["derived_tables"] = sorted(existing)
        except Exception as e:
            status["derived_db_error"] = str(e)
    else:
        status["derived_db_status"] = "not_found"

    return status


# ---- Lookup by phone ----

@app.get("/customers/by-phone/{phone}")
def customer_by_phone(phone: str):
    """
    Look up a customer by phone number.

    Searches PHONE, CELL PHONE 1, and CELL PHONE 2 fields.
    The phone number is normalized (country code and leading zeros stripped)
    for flexible matching.
    """
    normalized = _normalize_phone(phone)
    if len(normalized) < 5:
        raise HTTPException(status_code=400, detail="Phone number too short")

    # Build patterns for LIKE matching (handles various storage formats)
    like_pattern = f"%{normalized[-8:]}"  # Match last 8 digits

    sql = """
        SELECT * FROM tblcustomer
        WHERE [PHONE] LIKE ?
           OR [CELL PHONE 1] LIKE ?
           OR [CELL PHONE 2] LIKE ?
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (like_pattern, like_pattern, like_pattern))
            rows = cursor.fetchall()

            if not rows:
                raise HTTPException(status_code=404, detail="No customer found for this phone number")

            customers = [_normalize_customer(_row_to_dict(cursor, row)) for row in rows]

            # Resolve account numbers from all sources
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
    """Look up a customer by their CUSTOMER ID.

    Resolves account numbers from tblaccountnumbers, tblmeter,
    Copy Of tblmeter, and PLOT NUMBER derivation.
    """
    sql = "SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?"

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (customer_id,))
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
    """Look up a customer by their account number.

    Checks tblaccountnumbers, tblmeter, and Copy Of tblmeter for the
    account → customer mapping.
    """
    acct = account_number.strip()

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cust_id = None

            # 1. tblaccountnumbers
            try:
                cursor.execute(
                    "SELECT customerid FROM tblaccountnumbers WHERE accountnumber = ?",
                    (acct,),
                )
                r = cursor.fetchone()
                if r and r[0]:
                    cust_id = str(r[0]).strip()
            except Exception:
                pass

            # 2. tblmeter / Copy Of tblmeter
            if not cust_id:
                for table in ["tblmeter", "Copy Of tblmeter"]:
                    try:
                        cursor.execute(
                            f"SELECT [customer id] FROM [{table}] WHERE [accountnumber] = ?",
                            (acct,),
                        )
                        r = cursor.fetchone()
                        if r and r[0]:
                            cust_id = str(r[0]).strip()
                            break
                    except Exception:
                        continue

            if not cust_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"No customer with account {account_number}",
                )

            cursor.execute(
                "SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?",
                (cust_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Customer ID {cust_id} (from account {acct}) not in tblcustomer",
                )

            cust = _normalize_customer(_row_to_dict(cursor, row))
            cust["account_numbers"] = _resolve_accounts_for_customer(cursor, cust_id)

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
    """
    Search customers by name, village/concession, or plot number.
    """
    pattern = f"%{q}%"

    sql = """
        SELECT TOP ? * FROM tblcustomer
        WHERE [FIRST NAME] LIKE ?
           OR [LAST NAME] LIKE ?
           OR [MIDDLE NAME] LIKE ?
           OR [Concession name] LIKE ?
           OR [PLOT NUMBER] LIKE ?
           OR [CITY] LIKE ?
           OR [DISTRICT] LIKE ?
           OR [CUSTOMER ID] LIKE ?
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (limit, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern))
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
        SELECT DISTINCT [Concession name] FROM tblcustomer
        WHERE [Concession name] IS NOT NULL AND [Concession name] <> ''
        ORDER BY [Concession name]
    """

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()

            sites = []
            for row in rows:
                name = str(row[0]).strip()
                if name:
                    # Count customers per site
                    cursor.execute(
                        "SELECT COUNT(*) FROM tblcustomer WHERE [Concession name] = ?",
                        (name,),
                    )
                    count = cursor.fetchone()[0]
                    sites.append({"concession": name, "customer_count": count})

            return {"sites": sites, "total_sites": len(sites)}

    except Exception as e:
        logger.error("Sites list failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Initialize auth database
    from db_auth import init_auth_db
    init_auth_db()

    logger.info("=" * 60)
    logger.info("1PWR Customer Care Portal API v2.0")
    logger.info("DB Path: %s", DB_PATH or "(not found)")
    logger.info("Derived DB: %s", DERIVED_DB_PATH or "(not configured)")
    logger.info("Port: %d", PORT)
    logger.info("=" * 60)

    if not DB_PATH:
        logger.error(
            "No .accdb file found! Set ACDB_PATH environment variable.\n"
            "Searched: %s",
            "\n  ".join(DEFAULT_SEARCH_PATHS),
        )
        logger.info("Starting anyway (health endpoint will report no_db)...")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
