"""
Generic CRUD endpoints for ACCDB tables.

Provides paginated list, get-by-id, create, update, delete
with role-based permission gating.
"""

import logging
import math
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from models import (
    CCRole,
    CurrentUser,
    PaginatedResponse,
    RecordCreateRequest,
    RecordUpdateRequest,
    UserType,
)
from middleware import can_write_table, get_current_user, require_employee
from mutations import log_mutation

logger = logging.getLogger("acdb-api.crud")

router = APIRouter(prefix="/api/tables", tags=["crud"])

# Tables that customers can read their own rows from
CUSTOMER_READABLE_TABLES = {"tblcustomer", "tblaccountnumbers"}


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _get_derived_connection():
    from customer_api import get_derived_connection
    return get_derived_connection()


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    columns = [desc[0] for desc in cursor.description]
    d = {}
    for col, val in zip(columns, row):
        # Convert non-serializable types to strings
        if val is not None and not isinstance(val, (str, int, float, bool)):
            val = str(val)
        d[col] = val
    return d


def _get_primary_key(conn, table_name: str) -> Optional[str]:
    """Try to detect the primary key column for a table."""
    cursor = conn.cursor()
    try:
        # Try statistics/indexes to find the PK
        for row in cursor.statistics(table=table_name):
            if row.index_name and "PrimaryKey" in str(row.index_name):
                return row.column_name
    except Exception:
        pass

    # Fallback: common PK column patterns
    cols = [c.column_name for c in cursor.columns(table=table_name)]
    for candidate in ["ID", "Id", "id", "CUSTOMER ID", "customerid", "accountnumber"]:
        if candidate in cols:
            return candidate

    # Use first column as fallback
    return cols[0] if cols else None


# ---------------------------------------------------------------------------
# List (paginated)
# ---------------------------------------------------------------------------

@router.get("/{table_name}", response_model=PaginatedResponse)
def list_rows(
    table_name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None, description="Column to sort by"),
    order: str = Query("asc", regex="^(asc|desc)$"),
    search: Optional[str] = Query(None, description="Search across text columns"),
    filter_col: Optional[str] = Query(None, description="Column to filter"),
    filter_val: Optional[str] = Query(None, description="Value to filter by"),
    user: CurrentUser = Depends(get_current_user),
):
    """
    List rows from any table with pagination, sorting, and filtering.
    Customers can only access their own rows in customer-readable tables.
    """
    # Access control
    if user.user_type == UserType.customer:
        if table_name.lower() not in CUSTOMER_READABLE_TABLES:
            raise HTTPException(status_code=403, detail="Access denied to this table")

    with _get_connection() as conn:
        cursor = conn.cursor()

        # Verify table exists
        found = any(t.table_name == table_name for t in cursor.tables(tableType="TABLE"))
        if not found:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

        # Build WHERE clause
        where_clauses = []
        params = []

        # Customer scope: only own records
        if user.user_type == UserType.customer:
            if table_name.lower() == "tblcustomer":
                where_clauses.append("[CUSTOMER ID] = ?")
                params.append(user.user_id)
            elif table_name.lower() == "tblaccountnumbers":
                where_clauses.append("customerid = ?")
                params.append(user.user_id)

        # Column filter
        if filter_col and filter_val:
            where_clauses.append(f"[{filter_col}] = ?")
            params.append(filter_val)

        # Text search across all text columns
        if search:
            cols = [c.column_name for c in cursor.columns(table=table_name)
                    if c.type_name in ("VARCHAR", "LONGCHAR", "CHAR", "TEXT")]
            if cols:
                search_parts = [f"[{c}] LIKE ?" for c in cols[:10]]  # Limit to 10 columns
                where_clauses.append(f"({' OR '.join(search_parts)})")
                params.extend([f"%{search}%"] * len(search_parts))

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Count total
        count_sql = f"SELECT COUNT(*) FROM [{table_name}]{where_sql}"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

        # Sort
        order_sql = ""
        if sort:
            order_sql = f" ORDER BY [{sort}] {order.upper()}"

        # Paginate using TOP + offset simulation for Access SQL
        offset = (page - 1) * limit
        # Access SQL doesn't support OFFSET; use a subquery approach
        if offset == 0:
            sql = f"SELECT TOP {limit} * FROM [{table_name}]{where_sql}{order_sql}"
            cursor.execute(sql, params)
        else:
            # For Access: fetch all and slice in Python (simpler + reliable)
            sql = f"SELECT * FROM [{table_name}]{where_sql}{order_sql}"
            cursor.execute(sql, params)
            # Skip to offset
            for _ in range(offset):
                r = cursor.fetchone()
                if r is None:
                    break

        rows = []
        for _ in range(limit):
            row = cursor.fetchone()
            if row is None:
                break
            rows.append(_row_to_dict(cursor, row))

        return PaginatedResponse(
            rows=rows,
            total=total,
            page=page,
            limit=limit,
            pages=max(1, math.ceil(total / limit)),
        )


# ---------------------------------------------------------------------------
# Get single record
# ---------------------------------------------------------------------------

@router.get("/{table_name}/{record_id}")
def get_record(
    table_name: str,
    record_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Get a single record by primary key value."""
    if user.user_type == UserType.customer:
        if table_name.lower() not in CUSTOMER_READABLE_TABLES:
            raise HTTPException(status_code=403, detail="Access denied")

    with _get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key")

        cursor = conn.cursor()
        sql = f"SELECT * FROM [{table_name}] WHERE [{pk}] = ?"
        cursor.execute(sql, (record_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Record not found")

        record = _row_to_dict(cursor, row)

        # Customer scope check
        if user.user_type == UserType.customer:
            cid = record.get("CUSTOMER ID") or record.get("customerid")
            if cid and cid != user.user_id:
                raise HTTPException(status_code=403, detail="Access denied")

        return {"record": record, "primary_key": pk}


# ---------------------------------------------------------------------------
# Create record
# ---------------------------------------------------------------------------

# Country code → dialling prefix mapping.
# Used to strip country codes from phone numbers so they fit in ACCDB INTEGER columns.
# The COUNTRY field in the same record tells us which prefix to strip.
_COUNTRY_DIAL_CODES: Dict[str, str] = {
    "lesotho": "266",
    "benin": "229",
    "zambia": "260",
    "south africa": "27",
    "mozambique": "258",
    "tanzania": "255",
    "kenya": "254",
    "nigeria": "234",
    "ghana": "233",
    "senegal": "221",
    "madagascar": "261",
    "malawi": "265",
    "rwanda": "250",
    "uganda": "256",
    "drc": "243",
    "dr congo": "243",
}

# Column names that hold phone numbers (case-insensitive match)
_PHONE_COLUMNS = {"cell phone 1", "cell phone 2", "phone", "cell phone"}


def _strip_country_code(raw_phone: str, country: str) -> str:
    """
    Strip the international country code from a phone number so only the
    local subscriber number remains.  Handles formats like:
      +266 5660 1826  →  56601826
      266-56601826    →  56601826
      0056601826      →  56601826   (00 international prefix)
      56601826        →  56601826   (already local)
    """
    # Remove all non-digit characters
    digits = "".join(c for c in raw_phone if c.isdigit())
    if not digits:
        return ""

    # Look up the country dial code
    code = _COUNTRY_DIAL_CODES.get(country.lower().strip(), "")
    if not code:
        return digits

    # Strip leading 00 (international dialling prefix)
    if digits.startswith("00"):
        digits = digits[2:]

    # Strip the country code if the number starts with it
    if digits.startswith(code):
        digits = digits[len(code):]

    # Strip leading 0 (local trunk prefix) if present
    if digits.startswith("0") and len(digits) > 8:
        digits = digits[1:]

    return digits


def _coerce_values(cursor, table_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce frontend string values to match ACCDB column types so that
    pyodbc doesn't hit 'Numeric value out of range' or type errors.

    - COUNTER (AutoNumber) columns are dropped (Access auto-generates).
    - INTEGER/SMALLINT: strip non-digits, clamp to 32-bit signed range.
      For phone columns, strip country code first (inferred from COUNTRY field).
    - DOUBLE/REAL/FLOAT/CURRENCY: parse as float.
    - BIT: convert to bool.
    - DATETIME: pass through (pyodbc handles ISO strings).

    Column name matching is case-insensitive (Access column names are
    case-insensitive but cursor.columns() returns stored case).
    """
    # Build case-insensitive column type map:
    #   col_types_lower  = { "current balance": "CURRENCY", ... }
    #   col_actual_name  = { "current balance": "Current Balance", ... }
    col_types_lower: Dict[str, str] = {}
    col_actual_name: Dict[str, str] = {}
    try:
        for col in cursor.columns(table=table_name):
            lname = col.column_name.lower().strip()
            col_types_lower[lname] = (col.type_name or "").upper()
            col_actual_name[lname] = col.column_name
    except Exception:
        return data  # If introspection fails, pass through unchanged

    # Resolve country from the payload (for phone number handling)
    country = str(data.get("COUNTRY", data.get("country", "")) or "").strip()

    coerced: Dict[str, Any] = {}
    for key, val in data.items():
        lkey = key.lower().strip()
        col_type = col_types_lower.get(lkey, "VARCHAR")
        # Use the ACCDB's actual column name so the SQL matches exactly
        actual_key = col_actual_name.get(lkey, key)

        # Skip AutoNumber columns — Access generates these
        if col_type == "COUNTER":
            continue

        if val is None or (isinstance(val, str) and not val.strip()):
            coerced[actual_key] = None
            continue

        str_val = str(val).strip()

        if col_type in ("INTEGER", "SMALLINT", "SHORT"):
            is_phone = lkey in _PHONE_COLUMNS

            if is_phone and country:
                # Strip country code so local number fits in INTEGER
                digits = _strip_country_code(str_val, country)
            else:
                # Generic integer: strip non-digit chars but keep leading minus
                digits = "".join(c for c in str_val if c.isdigit() or c == "-")

            if not digits or digits == "-":
                coerced[actual_key] = None
                continue
            try:
                num = int(digits)
                # Access INTEGER is 32-bit signed: -2,147,483,648 to 2,147,483,647
                if -2_147_483_648 <= num <= 2_147_483_647:
                    coerced[actual_key] = num
                else:
                    logger.warning(
                        "Skipping column [%s]: value %s overflows INTEGER even after "
                        "country-code stripping (country=%s)",
                        actual_key, num, country or "unknown",
                    )
                    continue
            except ValueError:
                coerced[actual_key] = None

        elif col_type in ("DOUBLE", "REAL", "FLOAT", "NUMERIC", "DECIMAL", "CURRENCY"):
            try:
                coerced[actual_key] = float(str_val)
            except ValueError:
                coerced[actual_key] = None

        elif col_type == "BIT":
            coerced[actual_key] = str_val.lower() in ("1", "true", "yes")

        else:
            # VARCHAR, LONGCHAR, DATETIME, etc. — pass as string
            coerced[actual_key] = str_val

    return coerced


@router.post("/{table_name}", status_code=201)
def create_record(
    table_name: str,
    req: RecordCreateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Create a new record. Requires write permission for the table."""
    if not can_write_table(user, table_name):
        raise HTTPException(status_code=403, detail="Write access denied for this table")

    if not req.data:
        raise HTTPException(status_code=400, detail="No data provided")

    with _get_connection() as conn:
        cursor = conn.cursor()

        # Coerce values to match ACCDB column types
        coerced = _coerce_values(cursor, table_name, req.data)
        if not coerced:
            raise HTTPException(status_code=400, detail="No valid fields after type coercion")

        columns = list(coerced.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_list = ", ".join([f"[{c}]" for c in columns])
        values = [coerced[c] for c in columns]

        sql = f"INSERT INTO [{table_name}] ({col_list}) VALUES ({placeholders})"
        try:
            cursor.execute(sql, values)
            conn.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Insert failed: {e}")

        # Determine record ID for the log (use PK value, case-insensitive)
        pk = _get_primary_key(conn, table_name)
        rid = "unknown"
        if pk:
            pk_lower = pk.lower()
            for k, v in req.data.items():
                if k.lower() == pk_lower:
                    rid = str(v)
                    break

        log_mutation(user, "create", table_name, rid, new_values=coerced)

        return {"message": "Record created", "table": table_name}


# ---------------------------------------------------------------------------
# Update record
# ---------------------------------------------------------------------------

@router.put("/{table_name}/{record_id}")
def update_record(
    table_name: str,
    record_id: str,
    req: RecordUpdateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Update a record by primary key. Requires write permission."""
    if not can_write_table(user, table_name):
        raise HTTPException(status_code=403, detail="Write access denied for this table")

    if not req.data:
        raise HTTPException(status_code=400, detail="No data provided")

    with _get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key")

        # Capture old values before the update
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM [{table_name}] WHERE [{pk}] = ?", (record_id,))
        old_row = cursor.fetchone()
        old_values = _row_to_dict(cursor, old_row) if old_row else None

        # Coerce values to match ACCDB column types
        coerced = _coerce_values(cursor, table_name, req.data)
        if not coerced:
            raise HTTPException(status_code=400, detail="No valid fields after type coercion")

        set_parts = [f"[{col}] = ?" for col in coerced.keys()]
        values = list(coerced.values()) + [record_id]

        sql = f"UPDATE [{table_name}] SET {', '.join(set_parts)} WHERE [{pk}] = ?"
        try:
            cursor.execute(sql, values)
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Record not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Update failed: {e}")

        # Build new_values by merging old with actually-written fields
        new_values = dict(old_values) if old_values else {}
        new_values.update(coerced)
        log_mutation(user, "update", table_name, record_id, old_values=old_values, new_values=new_values)

        return {"message": "Record updated", "table": table_name, "id": record_id}


# ---------------------------------------------------------------------------
# Delete record
# ---------------------------------------------------------------------------

@router.delete("/{table_name}/{record_id}")
def delete_record(
    table_name: str,
    record_id: str,
    user: CurrentUser = Depends(require_employee),
):
    """Delete a record by primary key. Requires superadmin or onm_team role."""
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Delete requires superadmin or onm_team role")

    with _get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key")

        # Capture old values before deletion
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM [{table_name}] WHERE [{pk}] = ?", (record_id,))
        old_row = cursor.fetchone()
        old_values = _row_to_dict(cursor, old_row) if old_row else None

        sql = f"DELETE FROM [{table_name}] WHERE [{pk}] = ?"
        try:
            cursor.execute(sql, (record_id,))
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Record not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Delete failed: {e}")

        log_mutation(user, "delete", table_name, record_id, old_values=old_values)

        return {"message": "Record deleted", "table": table_name, "id": record_id}


# ---------------------------------------------------------------------------
# Customer self-service
# ---------------------------------------------------------------------------

customer_router = APIRouter(prefix="/api/my", tags=["customer-self-service"])


@customer_router.get("/profile")
def my_profile(user: CurrentUser = Depends(get_current_user)):
    """Customer: get own profile.

    user_id is the account number (e.g. 0045MAK). We resolve to a tblcustomer
    record via Copy Of tblmeter when possible, and always include the account
    number and recent transaction info.
    """
    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")

    from customer_api import get_connection, _row_to_dict, _normalize_customer

    acct = user.user_id  # e.g. "0045MAK"

    with get_connection() as conn:
        cursor = conn.cursor()

        cust: dict = {
            "account_number": acct,
            "customer_id": None,
            "first_name": "",
            "last_name": "",
            "account_numbers": [acct],
        }

        # Try to resolve account -> customer via Copy Of tblmeter
        try:
            cursor.execute(
                "SELECT [customer id] FROM [Copy Of tblmeter] WHERE [accountnumber] = ?",
                (acct,),
            )
            meter_row = cursor.fetchone()
            if meter_row and meter_row[0]:
                cust_id = str(meter_row[0])
                cursor.execute("SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?", (cust_id,))
                cust_row = cursor.fetchone()
                if cust_row:
                    cust = _normalize_customer(_row_to_dict(cursor, cust_row))
                    cust["account_number"] = acct
                    cust["account_numbers"] = [acct]
        except Exception:
            pass

        # Also try tblaccountnumbers for any additional accounts
        if cust.get("customer_id"):
            try:
                cursor.execute(
                    "SELECT accountnumber FROM tblaccountnumbers WHERE customerid = ?",
                    (cust["customer_id"],),
                )
                extra = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]
                if extra:
                    all_accts = list(set([acct] + extra))
                    cust["account_numbers"] = all_accts
            except Exception:
                pass

        return {"customer": cust}


@customer_router.get("/dashboard")
def my_dashboard(user: CurrentUser = Depends(get_current_user)):
    """
    Customer dashboard: meter balance, consumption, payments, and charts.

    Returns:
      - balance_kwh: estimated kWh remaining
      - last_payment: {amount, date, kwh_purchased}
      - avg_kwh_per_day: rolling 30-day average daily consumption
      - estimated_recharge_seconds: seconds until balance reaches 0
      - total_kwh_all_time: cumulative consumption
      - total_lsl_all_time: cumulative payments
      - daily_7d: [{date, kwh}] last 7 days
      - daily_30d: [{date, kwh}] last 30 days
      - monthly_12m: [{month, kwh}] last 12 months
    """
    import math
    from datetime import datetime, timedelta
    from collections import defaultdict

    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")

    from customer_api import get_connection

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. The user_id IS the account number (e.g. "0045MAK")
        accounts = [user.user_id]

        if not accounts or not accounts[0]:
            return {
                "balance_kwh": 0,
                "last_payment": None,
                "avg_kwh_per_day": 0,
                "estimated_recharge_seconds": 0,
                "total_kwh_all_time": 0,
                "total_lsl_all_time": 0,
                "daily_7d": [],
                "daily_30d": [],
                "monthly_12m": [],
            }

        placeholders = ",".join("?" for _ in accounts)

        # 2. Query account history for this customer's accounts
        #    Columns: accountnumber, kwh value, transaction amount, date columns
        history_rows = []
        latest_balance = None
        for table in ["tblaccounthistory1", "tblaccounthistoryOriginal"]:
            try:
                # Get column names first to find the date column
                cursor.execute(f"SELECT TOP 1 * FROM [{table}]")
                cols = [desc[0].lower().strip() for desc in cursor.description]

                # Find the date column (varies by table)
                date_col = None
                for candidate in ["date", "transactiondate", "transaction date", "datetime", "timestamp"]:
                    if candidate in cols:
                        date_col = candidate
                        break
                # Fallback: any column with 'date' in the name
                if not date_col:
                    for c in cols:
                        if "date" in c:
                            date_col = c
                            break

                if not date_col:
                    continue

                # Also grab [current balance] if it exists
                has_balance = "current balance" in cols
                balance_col = ", [current balance]" if has_balance else ""

                cursor.execute(
                    f"SELECT [accountnumber], [kwh value], [transaction amount], [{date_col}]{balance_col} "
                    f"FROM [{table}] WHERE [accountnumber] IN ({placeholders}) "
                    f"ORDER BY [{date_col}] DESC",
                    accounts,
                )
                history_rows = []
                latest_balance = None
                for r in cursor.fetchall():
                    kwh = float(r[1] or 0)
                    lsl = float(r[2] or 0)
                    dt = r[3]
                    # Capture the most recent balance value
                    if has_balance and latest_balance is None and r[4] is not None:
                        try:
                            latest_balance = float(r[4])
                        except (ValueError, TypeError):
                            pass
                    if dt is not None:
                        if isinstance(dt, str):
                            # Try common date formats
                            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                                try:
                                    dt = datetime.strptime(dt.strip(), fmt)
                                    break
                                except ValueError:
                                    continue
                            else:
                                dt = None
                    history_rows.append({"kwh": kwh, "lsl": lsl, "date": dt})

                if history_rows:
                    break  # Got data, don't try fallback table
            except Exception as e:
                logger.warning("Dashboard: failed to read %s: %s", table, e)
                continue

        # 2b. Supplement with tblmonthlytransactions for months beyond history
        latest_hist_dt: Optional[datetime] = None
        for r in history_rows:
            if r["date"] and isinstance(r["date"], datetime):
                if latest_hist_dt is None or r["date"] > latest_hist_dt:
                    latest_hist_dt = r["date"]

        try:
            with _get_derived_connection() as dconn:
                dcursor = dconn.cursor()
                existing_tbl = {
                    t.table_name.lower()
                    for t in dcursor.tables(tableType="TABLE")
                }
                if "tblmonthlytransactions" in existing_tbl:
                    acct_key = accounts[0]
                    dcursor.execute(
                        "SELECT [yearmonth], [amount_lsl], [kwh_vended] "
                        "FROM [tblmonthlytransactions] "
                        "WHERE [accountnumber] = ? "
                        "ORDER BY [yearmonth] DESC",
                        (acct_key,),
                    )
                    for r2 in dcursor.fetchall():
                        ym = str(r2[0] or "").strip()
                        if not ym:
                            continue
                        try:
                            y2, m2 = int(ym[:4]), int(ym[5:7])
                            row_dt2 = datetime(y2, m2, 15)
                        except (ValueError, IndexError):
                            continue
                        if latest_hist_dt and row_dt2 <= latest_hist_dt:
                            continue
                        lsl2 = float(r2[1] or 0)
                        kwh2 = float(r2[2] or 0)
                        history_rows.append({"kwh": kwh2, "lsl": lsl2, "date": row_dt2})
        except Exception as e:
            logger.warning("Dashboard: failed to supplement from tblmonthlytransactions: %s", e)

        # 3. Compute aggregates
        now = datetime.utcnow()
        total_kwh = sum(r["kwh"] for r in history_rows)
        total_lsl = sum(r["lsl"] for r in history_rows)

        # Last payment: most recent row with positive transaction amount
        last_payment = None
        for r in history_rows:
            if r["lsl"] > 0 and r["date"]:
                last_payment = {
                    "amount": round(r["lsl"], 2),
                    "date": r["date"].strftime("%Y-%m-%d") if r["date"] else None,
                    "kwh_purchased": round(r["kwh"], 2),
                }
                break

        # Daily consumption for last 30 days
        daily = defaultdict(float)
        cutoff_30 = now - timedelta(days=30)
        for r in history_rows:
            if r["date"] and r["date"] >= cutoff_30 and r["kwh"] > 0:
                day_key = r["date"].strftime("%Y-%m-%d")
                daily[day_key] += r["kwh"]

        # Average kWh/day over last 30 days
        days_with_data = len(daily)
        avg_kwh_per_day = sum(daily.values()) / max(days_with_data, 1)

        # Balance: prefer the actual "current balance" field from the most recent
        # transaction record. Fall back to estimation if not available.
        if latest_balance is not None:
            balance_kwh = max(0, latest_balance)
        else:
            total_purchased_kwh = sum(r["kwh"] for r in history_rows if r["lsl"] > 0)
            total_consumed_kwh = sum(r["kwh"] for r in history_rows if r["lsl"] <= 0)
            if total_consumed_kwh == 0:
                balance_kwh = max(0, history_rows[0]["kwh"] if history_rows else 0)
            else:
                balance_kwh = max(0, total_purchased_kwh - abs(total_consumed_kwh))

        # Estimated time to recharge (seconds)
        if avg_kwh_per_day > 0 and balance_kwh > 0:
            days_remaining = balance_kwh / avg_kwh_per_day
            estimated_seconds = int(days_remaining * 86400)
        else:
            estimated_seconds = 0

        # 4. Build chart data
        # Last 7 days
        daily_7d = []
        for i in range(6, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_7d.append({"date": d, "kwh": round(daily.get(d, 0), 2)})

        # Last 30 days
        daily_30d = []
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_30d.append({"date": d, "kwh": round(daily.get(d, 0), 2)})

        # Monthly for last 12 months
        monthly = defaultdict(float)
        cutoff_12m = now - timedelta(days=365)
        for r in history_rows:
            if r["date"] and r["date"] >= cutoff_12m and r["kwh"] > 0:
                mo_key = r["date"].strftime("%Y-%m")
                monthly[mo_key] += r["kwh"]

        monthly_12m = []
        for i in range(11, -1, -1):
            d = now - timedelta(days=i * 30)
            mo = d.strftime("%Y-%m")
            monthly_12m.append({"month": mo, "kwh": round(monthly.get(mo, 0), 1)})

        return {
            "balance_kwh": round(balance_kwh, 2),
            "last_payment": last_payment,
            "avg_kwh_per_day": round(avg_kwh_per_day, 2),
            "estimated_recharge_seconds": estimated_seconds,
            "total_kwh_all_time": round(total_kwh, 1),
            "total_lsl_all_time": round(total_lsl, 2),
            "daily_7d": daily_7d,
            "daily_30d": daily_30d,
            "monthly_12m": monthly_12m,
        }


# ---------------------------------------------------------------------------
# Employee: customer data lookup
# ---------------------------------------------------------------------------

customer_data_router = APIRouter(prefix="/api/customer-data", tags=["customer-data"])


@customer_data_router.get("/{account_number}")
def employee_customer_data(
    account_number: str,
    user: CurrentUser = Depends(require_employee),
):
    """
    Employee-facing endpoint: returns dashboard stats, transaction history,
    and customer profile for any account number (e.g. 0045MAK).
    """
    import math
    from datetime import datetime, timedelta
    from collections import defaultdict
    from customer_api import get_connection, _row_to_dict, _normalize_customer

    acct = account_number.strip().upper()

    with get_connection() as conn:
        cursor = conn.cursor()

        # If a purely numeric customer ID was passed, resolve to account number
        if acct.isdigit():
            from customer_api import _resolve_accounts_for_customer
            resolved = _resolve_accounts_for_customer(cursor, acct)
            if resolved:
                acct = resolved[0].upper()

        # --- Resolve customer profile ---
        profile: dict = {
            "account_number": acct,
            "customer_id": None,
            "first_name": "",
            "last_name": "",
        }
        meter_info: dict = {}

        # Try meter tables first
        for meter_table in ["tblmeter", "Copy Of tblmeter"]:
            try:
                cursor.execute(
                    f"SELECT * FROM [{meter_table}] WHERE [accountnumber] = ?",
                    (acct,),
                )
                mrow = cursor.fetchone()
                if mrow:
                    meter_info = _row_to_dict(cursor, mrow)
                    cust_id = meter_info.get("customer id")
                    if cust_id:
                        cursor.execute(
                            "SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?",
                            (str(cust_id),),
                        )
                        crow = cursor.fetchone()
                        if crow:
                            profile = _normalize_customer(_row_to_dict(cursor, crow))
                            profile["account_number"] = acct
                    break
            except Exception:
                continue

        # If meter tables didn't find the customer, try reverse-deriving
        # from PLOT NUMBER:  account 0045MAK -> plot like 'MAK 0045%'
        if not profile.get("customer_id"):
            from customer_api import _derive_account_from_plot
            # Extract community suffix and numeric part from the account
            import re as _re
            m = _re.match(r"^(\d{3,4})([A-Za-z]{2,4})$", acct)
            if m:
                num_part, comm = m.group(1), m.group(2).upper()
                plot_pattern = f"{comm} {num_part}%"
                try:
                    cursor.execute(
                        "SELECT * FROM tblcustomer WHERE [PLOT NUMBER] LIKE ? AND [Concession name] = ?",
                        (plot_pattern, comm),
                    )
                    crow = cursor.fetchone()
                    if crow:
                        profile = _normalize_customer(_row_to_dict(cursor, crow))
                        profile["account_number"] = acct
                except Exception:
                    pass

        # If still no meter info, try to get meter ID from history
        if not meter_info:
            for htable in ["tblaccounthistory1", "tblaccounthistoryOriginal"]:
                try:
                    cursor.execute(
                        f"SELECT TOP 1 [meterid] FROM [{htable}] "
                        f"WHERE [accountnumber] = ? AND [meterid] IS NOT NULL AND [meterid] <> ''",
                        (acct,),
                    )
                    hrow = cursor.fetchone()
                    if hrow and hrow[0]:
                        meter_info = {"meterid": str(hrow[0]).strip()}
                        # Try to find the community from the account suffix
                        if m:
                            meter_info["community"] = m.group(2).upper()
                        break
                except Exception:
                    continue

        # --- Transaction history (paginated, most recent first) ---
        transactions = []
        for table in ["tblaccounthistory1", "tblaccounthistoryOriginal"]:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM [{table}]")
                cols = [d[0].lower().strip() for d in cursor.description]
                date_col = None
                for cand in ["transaction date", "date", "transactiondate", "datetime", "timestamp"]:
                    if cand in cols:
                        date_col = cand
                        break
                if not date_col:
                    for c in cols:
                        if "date" in c:
                            date_col = c
                            break
                if not date_col:
                    continue

                has_balance = "current balance" in cols

                balance_sel = ", [current balance]" if has_balance else ""
                cursor.execute(
                    f"SELECT [ID], [accountnumber], [meterid], [{date_col}], "
                    f"[transaction amount], [rate used], [kwh value], [payment]{balance_sel} "
                    f"FROM [{table}] WHERE [accountnumber] = ? "
                    f"ORDER BY [{date_col}] DESC",
                    (acct,),
                )
                for r in cursor.fetchall():
                    dt_raw = r[3]
                    dt_str = None
                    dt_parsed = None
                    if dt_raw is not None:
                        if isinstance(dt_raw, str):
                            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
                                try:
                                    dt_parsed = datetime.strptime(dt_raw.strip(), fmt)
                                    dt_str = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
                                    break
                                except ValueError:
                                    continue
                        else:
                            dt_parsed = dt_raw
                            dt_str = dt_raw.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt_raw, "strftime") else str(dt_raw)

                    txn = {
                        "id": r[0],
                        "account": r[1],
                        "meter": r[2],
                        "date": dt_str,
                        "amount_lsl": round(float(r[4] or 0), 2),
                        "rate": round(float(r[5] or 0), 2),
                        "kwh": round(float(r[6] or 0), 2),
                        "is_payment": bool(r[7]),
                    }
                    if has_balance:
                        try:
                            txn["balance"] = round(float(r[8] or 0), 2)
                        except (ValueError, TypeError):
                            txn["balance"] = None
                    transactions.append(txn)

                if transactions:
                    break
            except Exception as e:
                logger.warning("customer-data: failed to read %s: %s", table, e)
                continue

        # --- Supplement with tblmonthlytransactions for months beyond history ---
        # The history tables may be stale (cloned ACCDB).  tblmonthlytransactions
        # has SparkMeter portfolio data that may cover more recent months.
        latest_hist_date: Optional[datetime] = None
        for t in transactions:
            if t["date"]:
                try:
                    dt_p = datetime.strptime(t["date"][:19], "%Y-%m-%d %H:%M:%S")
                    if latest_hist_date is None or dt_p > latest_hist_date:
                        latest_hist_date = dt_p
                except ValueError:
                    pass

        try:
            with _get_derived_connection() as dconn:
                dcursor = dconn.cursor()
                existing_tables = {
                    tbl.table_name.lower()
                    for tbl in dcursor.tables(tableType="TABLE")
                }
                if "tblmonthlytransactions" in existing_tables:
                    dcursor.execute(
                        "SELECT [yearmonth], [amount_lsl], [kwh_vended], "
                        "[n_transactions], [meterid], [community] "
                        "FROM [tblmonthlytransactions] "
                        "WHERE [accountnumber] = ? "
                        "ORDER BY [yearmonth] DESC",
                        (acct,),
                    )
                    for r in dcursor.fetchall():
                        ym = str(r[0] or "").strip()
                        if not ym:
                            continue
                        try:
                            y, m = int(ym[:4]), int(ym[5:7])
                            row_dt = datetime(y, m, 15)  # mid-month placeholder
                        except (ValueError, IndexError):
                            continue

                        # Only add if this month is AFTER the latest history record
                        if latest_hist_date and row_dt <= latest_hist_date:
                            continue

                        lsl = round(float(r[1] or 0), 2)
                        kwh = round(float(r[2] or 0), 2)
                        n_txn = int(r[3] or 0)
                        mid = str(r[4] or "").strip()

                        transactions.append({
                            "id": None,
                            "account": acct,
                            "meter": mid,
                            "date": row_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            "amount_lsl": lsl,
                            "rate": 0,
                            "kwh": kwh,
                            "is_payment": lsl > 0,
                            "source": "sparkmeter_monthly",
                            "n_transactions": n_txn,
                            "yearmonth": ym,
                        })
        except Exception as e:
            logger.warning("customer-data: failed to supplement from tblmonthlytransactions: %s", e)

        # Re-sort by date descending after supplementing
        def _txn_sort_key(t):
            try:
                return datetime.strptime(t["date"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError, AttributeError):
                return datetime.min
        transactions.sort(key=_txn_sort_key, reverse=True)

        # --- Compute dashboard aggregates from transactions ---
        now = datetime.utcnow()
        total_kwh = sum(t["kwh"] for t in transactions)
        total_lsl = sum(t["amount_lsl"] for t in transactions)

        last_payment = None
        for t in transactions:
            if t["amount_lsl"] > 0 and t["date"]:
                last_payment = {
                    "amount": t["amount_lsl"],
                    "date": t["date"][:10],
                    "kwh_purchased": t["kwh"],
                }
                break

        daily = defaultdict(float)
        cutoff_30 = now - timedelta(days=30)
        for t in transactions:
            if t["date"]:
                try:
                    dt = datetime.strptime(t["date"][:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if dt >= cutoff_30 and t["kwh"] > 0:
                    daily[dt.strftime("%Y-%m-%d")] += t["kwh"]

        days_with_data = len(daily)
        avg_kwh_per_day = sum(daily.values()) / max(days_with_data, 1)

        # Balance
        latest_balance = None
        for t in transactions:
            if t.get("balance") is not None:
                latest_balance = t["balance"]
                break
        if latest_balance is not None:
            balance_kwh = max(0, latest_balance)
        else:
            purchased = sum(t["kwh"] for t in transactions if t["amount_lsl"] > 0)
            consumed = sum(t["kwh"] for t in transactions if t["amount_lsl"] <= 0)
            balance_kwh = max(0, purchased - abs(consumed)) if consumed else max(0, transactions[0]["kwh"] if transactions else 0)

        if avg_kwh_per_day > 0 and balance_kwh > 0:
            est_seconds = int((balance_kwh / avg_kwh_per_day) * 86400)
        else:
            est_seconds = 0

        # Charts
        daily_7d = [{"date": (now - timedelta(days=6 - i)).strftime("%Y-%m-%d"), "kwh": round(daily.get((now - timedelta(days=6 - i)).strftime("%Y-%m-%d"), 0), 2)} for i in range(7)]
        daily_30d = [{"date": (now - timedelta(days=29 - i)).strftime("%Y-%m-%d"), "kwh": round(daily.get((now - timedelta(days=29 - i)).strftime("%Y-%m-%d"), 0), 2)} for i in range(30)]

        monthly = defaultdict(float)
        cutoff_12m = now - timedelta(days=365)
        for t in transactions:
            if t["date"]:
                try:
                    dt = datetime.strptime(t["date"][:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if dt >= cutoff_12m and t["kwh"] > 0:
                    monthly[dt.strftime("%Y-%m")] += t["kwh"]
        monthly_12m = [{"month": (now - timedelta(days=(11 - i) * 30)).strftime("%Y-%m"), "kwh": round(monthly.get((now - timedelta(days=(11 - i) * 30)).strftime("%Y-%m"), 0), 1)} for i in range(12)]

        # Resolve effective tariff for this customer
        tariff_info = None
        try:
            from tariff import resolve_rate
            cust_id = profile.get("customer_id") or ""
            conc = profile.get("concession") or meter_info.get("community") or ""
            tariff_info = resolve_rate(cursor, customer_id=cust_id, concession=conc)
        except Exception as e:
            logger.warning("Failed to resolve tariff: %s", e)

        return {
            "account_number": acct,
            "profile": profile,
            "meter": {
                "meterid": meter_info.get("meterid"),
                "community": meter_info.get("community"),
                "customer_type": meter_info.get("customer type"),
                "village": meter_info.get("Village name"),
                "status": meter_info.get("current status"),
                "connect_date": str(meter_info.get("customer connect date") or ""),
            } if meter_info else None,
            "tariff": tariff_info,
            "dashboard": {
                "balance_kwh": round(balance_kwh, 2),
                "last_payment": last_payment,
                "avg_kwh_per_day": round(avg_kwh_per_day, 2),
                "estimated_recharge_seconds": est_seconds,
                "total_kwh_all_time": round(total_kwh, 1),
                "total_lsl_all_time": round(total_lsl, 2),
                "daily_7d": daily_7d,
                "daily_30d": daily_30d,
                "monthly_12m": monthly_12m,
            },
            "transactions": transactions,
            "transaction_count": len(transactions),
        }
