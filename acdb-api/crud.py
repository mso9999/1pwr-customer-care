"""
Generic CRUD endpoints for PostgreSQL tables.

Provides paginated list, get-by-id, create, update, delete
with role-based permission gating.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

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
from sparkmeter_credit import credit_sparkmeter

logger = logging.getLogger("acdb-api.crud")

router = APIRouter(prefix="/api/tables", tags=["crud"])

# Tables that customers can read their own rows from
CUSTOMER_READABLE_TABLES = {"customers", "accounts"}

SOFT_DELETE_TABLES = {"customers"}
COLD_STORAGE_DAYS = 30


def _get_connection():
    from customer_api import get_connection
    return get_connection()


def _ensure_soft_delete_columns():
    """Add deleted_at / deleted_by columns to soft-delete tables if missing."""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            for tbl in SOFT_DELETE_TABLES:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "AND column_name = 'deleted_at'",
                    (tbl,),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        f"ALTER TABLE {tbl} "
                        "ADD COLUMN deleted_at TIMESTAMPTZ DEFAULT NULL, "
                        "ADD COLUMN deleted_by TEXT DEFAULT NULL"
                    )
                    conn.commit()
                    logger.info("Added deleted_at/deleted_by to %s", tbl)
    except Exception as e:
        logger.warning("soft-delete column init: %s", e)


def _has_deleted_at(conn, table_name: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "AND column_name = 'deleted_at'",
        (table_name,),
    )
    return cursor.fetchone() is not None


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
    """Detect the primary key column for a table using pg_index."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary",
            (table_name,)
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    except Exception:
        conn.rollback()

    # Fallback: common PK column patterns
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table_name,)
    )
    cols = [r[0] for r in cursor.fetchall()]
    for candidate in ["id", "customer_id_legacy", "account_number"]:
        if candidate in cols:
            return candidate

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
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

        # Build WHERE clause
        where_clauses: list[str] = []
        params: list = []

        # Exclude soft-deleted rows from normal listing
        if table_name.lower() in SOFT_DELETE_TABLES and _has_deleted_at(conn, table_name):
            where_clauses.append("deleted_at IS NULL")

        # Customer scope: only own records
        if user.user_type == UserType.customer:
            if table_name.lower() == "customers":
                where_clauses.append("customer_id_legacy = %s")
                params.append(user.user_id)
            elif table_name.lower() == "accounts":
                where_clauses.append("account_number = %s")
                params.append(user.user_id)

        # Column filter
        if filter_col and filter_val:
            where_clauses.append(f"{filter_col} = %s")
            params.append(filter_val)

        # Text search across all text columns
        if search:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "AND data_type IN ('character varying', 'text', 'character')",
                (table_name,)
            )
            text_cols = [r[0] for r in cursor.fetchall()]
            if text_cols:
                search_parts = [f"{c} ILIKE %s" for c in text_cols[:10]]
                where_clauses.append(f"({' OR '.join(search_parts)})")
                params.extend([f"%{search}%"] * len(search_parts))

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Count total
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}{where_sql}", params)
        total = cursor.fetchone()[0]

        # Sort
        order_sql = ""
        if sort:
            order_sql = f" ORDER BY {sort} {order.upper()}"

        # Paginate with LIMIT/OFFSET
        offset = (page - 1) * limit
        sql = f"SELECT * FROM {table_name}{where_sql}{order_sql} LIMIT %s OFFSET %s"
        cursor.execute(sql, params + [limit, offset])

        rows = [_row_to_dict(cursor, row) for row in cursor.fetchall()]

        return PaginatedResponse(
            rows=rows,
            total=total,
            page=page,
            limit=limit,
            pages=max(1, math.ceil(total / limit)),
        )


# ---------------------------------------------------------------------------
# Cold-storage endpoints (must be registered before /{record_id} catch-all)
# ---------------------------------------------------------------------------


@router.get("/{table_name}/cold-storage")
def list_cold_storage(
    table_name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(require_employee),
):
    """List soft-deleted records awaiting purge."""
    if table_name.lower() not in SOFT_DELETE_TABLES:
        raise HTTPException(status_code=400, detail="Table does not support cold storage")

    with _get_connection() as conn:
        if not _has_deleted_at(conn, table_name):
            return PaginatedResponse(rows=[], total=0, page=1, limit=limit, pages=1)

        cursor = conn.cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE deleted_at IS NOT NULL"
        )
        total = cursor.fetchone()[0]

        offset = (page - 1) * limit
        cursor.execute(
            f"SELECT * FROM {table_name} WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        rows = [_row_to_dict(cursor, r) for r in cursor.fetchall()]

        return PaginatedResponse(
            rows=rows,
            total=total,
            page=page,
            limit=limit,
            pages=max(1, math.ceil(total / limit)),
        )


@router.delete("/{table_name}/cold-storage/purge")
def purge_expired(
    table_name: str,
    user: CurrentUser = Depends(require_employee),
):
    """Permanently delete cold-storage records older than COLD_STORAGE_DAYS."""
    if user.role != CCRole.superadmin.value:
        raise HTTPException(status_code=403, detail="Purge requires superadmin role")

    if table_name.lower() not in SOFT_DELETE_TABLES:
        raise HTTPException(status_code=400, detail="Table does not support cold storage")

    with _get_connection() as conn:
        if not _has_deleted_at(conn, table_name):
            return {"purged": 0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=COLD_STORAGE_DAYS)
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM {table_name} "
            "WHERE deleted_at IS NOT NULL AND deleted_at < %s",
            (cutoff.isoformat(),),
        )
        purged = cursor.rowcount
        conn.commit()

        logger.info("Purged %d expired records from %s", purged, table_name)
        return {"purged": purged, "table": table_name, "cutoff": cutoff.isoformat()}


@router.post("/{table_name}/{record_id}/restore")
def restore_record(
    table_name: str,
    record_id: str,
    user: CurrentUser = Depends(require_employee),
):
    """Restore a soft-deleted record from cold storage."""
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Restore requires superadmin or onm_team role")

    if table_name.lower() not in SOFT_DELETE_TABLES:
        raise HTTPException(status_code=400, detail="Table does not support cold storage")

    with _get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key")

        cursor = conn.cursor()
        cursor.execute(
            f"SELECT deleted_at FROM {table_name} WHERE {pk} = %s", (record_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        if not row[0]:
            raise HTTPException(status_code=400, detail="Record is not in cold storage")

        cursor.execute(
            f"UPDATE {table_name} SET deleted_at = NULL, deleted_by = NULL "
            f"WHERE {pk} = %s",
            (record_id,),
        )
        conn.commit()

        log_mutation(user, "restore", table_name, record_id)

        return {"message": "Record restored", "table": table_name, "id": record_id}


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
        sql = f"SELECT * FROM {table_name} WHERE {pk} = %s"
        cursor.execute(sql, (record_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Record not found")

        record = _row_to_dict(cursor, row)

        # Customer scope check
        if user.user_type == UserType.customer:
            cid = record.get("customer_id_legacy") or record.get("customer_id")
            if cid and cid != user.user_id:
                raise HTTPException(status_code=403, detail="Access denied")

        return {"record": record, "primary_key": pk}


# ---------------------------------------------------------------------------
# Create record
# ---------------------------------------------------------------------------

# Country code → dialling prefix mapping.
# Used to strip country codes from phone numbers so they fit in integer columns.
# The country field in the same record tells us which prefix to strip.
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

# Column names that hold phone numbers
_PHONE_COLUMNS = {"cell_phone_1", "cell_phone_2", "phone"}


def _strip_country_code(raw_phone: str, country: str) -> str:
    """
    Strip the international country code from a phone number so only the
    local subscriber number remains.  Handles formats like:
      +266 5660 1826  →  56601826
      266-56601826    →  56601826
      0056601826      →  56601826   (00 international prefix)
      56601826        →  56601826   (already local)
    """
    digits = "".join(c for c in raw_phone if c.isdigit())
    if not digits:
        return ""

    code = _COUNTRY_DIAL_CODES.get(country.lower().strip(), "")
    if not code:
        return digits

    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(code):
        digits = digits[len(code):]
    if digits.startswith("0") and len(digits) > 8:
        digits = digits[1:]

    return digits


def _coerce_values(cursor, table_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce frontend string values to match PostgreSQL column types so that
    psycopg2 doesn't hit type mismatch errors.

    - Serial/identity columns are dropped (PostgreSQL auto-generates).
    - integer/smallint/bigint: strip non-digits, validate range.
      For phone columns, strip country code first (inferred from country field).
    - double precision/real/numeric/money: parse as float.
    - boolean: convert to bool.
    - Other types (text, timestamp, etc.): pass as string.

    Column name matching is case-insensitive (frontend may send mixed case).
    """
    col_types: Dict[str, str] = {}
    try:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,)
        )
        for row in cursor.fetchall():
            col_types[row[0]] = row[1]
    except Exception:
        return data

    # Detect serial/identity columns (auto-generated via nextval)
    serial_cols: set = set()
    try:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "AND column_default LIKE 'nextval%%'",
            (table_name,)
        )
        serial_cols = {r[0] for r in cursor.fetchall()}
    except Exception:
        pass

    country = str(data.get("country", "") or "").strip()

    coerced: Dict[str, Any] = {}
    for key, val in data.items():
        lkey = key.lower().strip()
        col_type = col_types.get(lkey, "character varying")

        # Skip serial/identity columns — PostgreSQL generates these
        if lkey in serial_cols:
            continue

        if val is None or (isinstance(val, str) and not val.strip()):
            coerced[lkey] = None
            continue

        str_val = str(val).strip()

        if col_type in ("integer", "smallint", "bigint"):
            is_phone = lkey in _PHONE_COLUMNS

            if is_phone and country:
                digits = _strip_country_code(str_val, country)
            else:
                digits = "".join(c for c in str_val if c.isdigit() or c == "-")

            if not digits or digits == "-":
                coerced[lkey] = None
                continue
            try:
                num = int(digits)
                if col_type == "smallint" and not (-32_768 <= num <= 32_767):
                    logger.warning(
                        "Skipping column %s: value %s overflows smallint", lkey, num,
                    )
                    continue
                if col_type == "integer" and not (-2_147_483_648 <= num <= 2_147_483_647):
                    logger.warning(
                        "Skipping column %s: value %s overflows integer (country=%s)",
                        lkey, num, country or "unknown",
                    )
                    continue
                coerced[lkey] = num
            except ValueError:
                coerced[lkey] = None

        elif col_type in ("double precision", "real", "numeric", "money"):
            try:
                coerced[lkey] = float(str_val)
            except ValueError:
                coerced[lkey] = None

        elif col_type == "boolean":
            coerced[lkey] = str_val.lower() in ("1", "true", "yes", "t")

        else:
            # text, character varying, timestamp, date, etc. — pass as string
            coerced[lkey] = str_val

    return coerced


# ---------------------------------------------------------------------------
# SparkMeter credit integration for transaction creates
# ---------------------------------------------------------------------------

def _maybe_credit_sm(
    record: dict, txn_id, background_tasks: BackgroundTasks,
) -> Optional[dict]:
    """If the record looks like a new payment, credit SparkMeter.

    Returns a summary dict when a credit was attempted, or None if
    the record isn't a creditable payment.
    """
    is_payment = record.get("is_payment")
    if isinstance(is_payment, str):
        is_payment = is_payment.lower() in ("1", "true", "yes", "t")
    if not is_payment:
        return None

    amount = record.get("transaction_amount")
    if amount is None:
        return None
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return None
    if amount <= 0:
        return None

    account = record.get("account_number", "")
    if not account:
        return None

    result = credit_sparkmeter(
        account_number=account,
        amount=amount,
        memo=f"CC portal txn {txn_id}",
        external_id=str(txn_id),
    )
    summary = {"success": result.success, "platform": result.platform}
    if result.sm_transaction_id:
        summary["sm_transaction_id"] = result.sm_transaction_id
    if result.error:
        summary["error"] = result.error
    if not result.success:
        logger.warning("SM credit via CRUD failed for %s: %s", account, result.error)
    else:
        logger.info(
            "SM credit via CRUD OK for %s M%.2f → %s",
            account, amount, result.platform,
        )
    return summary


@router.post("/{table_name}", status_code=201)
def create_record(
    table_name: str,
    req: RecordCreateRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_employee),
):
    """Create a new record. Requires write permission for the table."""
    if not can_write_table(user, table_name):
        raise HTTPException(status_code=403, detail="Write access denied for this table")

    if not req.data:
        raise HTTPException(status_code=400, detail="No data provided")

    with _get_connection() as conn:
        cursor = conn.cursor()

        coerced = _coerce_values(cursor, table_name, req.data)
        if not coerced:
            raise HTTPException(status_code=400, detail="No valid fields after type coercion")

        columns = list(coerced.keys())
        placeholders = ", ".join(["%s"] * len(columns))
        col_list = ", ".join(columns)
        values = [coerced[c] for c in columns]

        returning = " RETURNING id" if table_name == "transactions" else ""
        sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders}){returning}"
        try:
            cursor.execute(sql, values)
            new_id = cursor.fetchone() if returning else None
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Insert failed: {e}")

        pk = _get_primary_key(conn, table_name)
        rid = "unknown"
        if pk:
            for k, v in req.data.items():
                if k.lower() == pk:
                    rid = str(v)
                    break

        log_mutation(user, "create", table_name, rid, new_values=coerced)

        response: dict = {"message": "Record created", "table": table_name}

        if table_name == "transactions":
            sm = _maybe_credit_sm(coerced, new_id[0] if new_id else rid, background_tasks)
            if sm is not None:
                response["sm_credit"] = sm

        return response


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
        cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s", (record_id,))
        old_row = cursor.fetchone()
        old_values = _row_to_dict(cursor, old_row) if old_row else None

        coerced = _coerce_values(cursor, table_name, req.data)
        if not coerced:
            raise HTTPException(status_code=400, detail="No valid fields after type coercion")

        set_parts = [f"{col} = %s" for col in coerced.keys()]
        values = list(coerced.values()) + [record_id]

        sql = f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE {pk} = %s"
        try:
            cursor.execute(sql, values)
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Record not found")
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
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
    """Delete a record by primary key. Requires superadmin or onm_team role.

    For soft-delete tables (customers), records are moved to cold storage
    for 30 days before permanent purge.
    """
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Delete requires superadmin or onm_team role")

    with _get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key")

        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s", (record_id,))
        old_row = cursor.fetchone()
        old_values = _row_to_dict(cursor, old_row) if old_row else None

        if not old_row:
            raise HTTPException(status_code=404, detail="Record not found")

        soft = (
            table_name.lower() in SOFT_DELETE_TABLES
            and _has_deleted_at(conn, table_name)
        )

        try:
            if soft:
                if old_values and old_values.get("deleted_at"):
                    raise HTTPException(
                        status_code=400,
                        detail="Record is already in cold storage",
                    )
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    f"UPDATE {table_name} SET deleted_at = %s, deleted_by = %s "
                    f"WHERE {pk} = %s",
                    (now, user.user_id, record_id),
                )
                conn.commit()
                log_mutation(
                    user, "soft_delete", table_name, record_id,
                    old_values=old_values,
                )
                purge_date = (
                    datetime.now(timezone.utc) + timedelta(days=COLD_STORAGE_DAYS)
                ).strftime("%Y-%m-%d")
                return {
                    "message": "Record moved to cold storage",
                    "table": table_name,
                    "id": record_id,
                    "purge_after": purge_date,
                }
            else:
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE {pk} = %s", (record_id,)
                )
                conn.commit()
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Record not found")
                log_mutation(
                    user, "delete", table_name, record_id,
                    old_values=old_values,
                )
                return {
                    "message": "Record deleted",
                    "table": table_name,
                    "id": record_id,
                }
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Delete failed: {e}")


# ---------------------------------------------------------------------------
# Customer self-service
# ---------------------------------------------------------------------------

customer_router = APIRouter(prefix="/api/my", tags=["customer-self-service"])


@customer_router.get("/profile")
def my_profile(user: CurrentUser = Depends(get_current_user)):
    """Customer: get own profile.

    user_id is the account number (e.g. 0045MAK). We resolve to a customers
    record via meters when possible, and always include the account number
    and recent transaction info.
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

        # Resolve account -> customer via meters table
        try:
            cursor.execute(
                "SELECT customer_id_legacy FROM meters WHERE account_number = %s",
                (acct,),
            )
            meter_row = cursor.fetchone()
            if meter_row and meter_row[0]:
                cust_id = str(meter_row[0])
                cursor.execute(
                    "SELECT * FROM customers WHERE customer_id_legacy = %s",
                    (cust_id,),
                )
                cust_row = cursor.fetchone()
                if cust_row:
                    cust = _normalize_customer(_row_to_dict(cursor, cust_row))
                    cust["account_number"] = acct
                    cust["account_numbers"] = [acct]
        except Exception:
            pass

        # Also check accounts table for any additional accounts
        if cust.get("customer_id"):
            try:
                cursor.execute(
                    "SELECT account_number FROM accounts WHERE customer_id = %s",
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
    from datetime import datetime, timedelta, timezone
    from collections import defaultdict

    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")

    from customer_api import get_connection

    from country_config import UTC_OFFSET_HOURS
    _LOCAL_OFFSET = timedelta(hours=UTC_OFFSET_HOURS)

    def _to_local(dt):
        """Convert a UTC-aware or naive-UTC datetime to local time (naive)."""
        if dt is None:
            return dt
        if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt + _LOCAL_OFFSET

    with get_connection() as conn:
        cursor = conn.cursor()

        acct = user.user_id

        empty_dash = {
            "balance_kwh": 0,
            "last_payment": None,
            "avg_kwh_per_day": 0,
            "estimated_recharge_seconds": 0,
            "total_kwh_all_time": 0,
            "total_lsl_all_time": 0,
            "daily_7d": [],
            "daily_30d": [],
            "monthly_12m": [],
            "meters": [],
            "meter_comparison": [],
        }

        if not acct:
            return empty_dash

        # Fetch all meters for this account
        meter_list = []
        try:
            cursor.execute(
                "SELECT meter_id, platform, role, status FROM meters "
                "WHERE account_number = %s ORDER BY role, meter_id",
                (acct,),
            )
            for mr in cursor.fetchall():
                meter_list.append({
                    "meter_id": mr[0],
                    "platform": mr[1],
                    "role": mr[2],
                    "status": mr[3],
                })
        except Exception:
            pass

        history_rows = []

        cursor.execute(
            "SELECT account_number, kwh_value, transaction_amount, "
            "transaction_date "
            "FROM transactions WHERE account_number = %s "
            "ORDER BY transaction_date DESC",
            (acct,),
        )
        for r in cursor.fetchall():
            kwh = float(r[1] or 0)
            lsl = float(r[2] or 0)
            dt = _to_local(r[3])
            if dt is not None:
                if isinstance(dt, str):
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                        try:
                            dt = datetime.strptime(dt.strip(), fmt)
                            break
                        except ValueError:
                            continue
                    else:
                        dt = None
            history_rows.append({"kwh": kwh, "lsl": lsl, "date": dt})

        # Supplement with monthly_transactions for months beyond history
        latest_hist_dt: Optional[datetime] = None
        for r in history_rows:
            if r["date"] and isinstance(r["date"], datetime):
                if latest_hist_dt is None or r["date"] > latest_hist_dt:
                    latest_hist_dt = r["date"]

        try:
            cursor.execute(
                "SELECT year_month, amount_lsl, kwh_vended "
                "FROM monthly_transactions "
                "WHERE account_number = %s "
                "ORDER BY year_month DESC",
                (acct,),
            )
            for r2 in cursor.fetchall():
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
            logger.warning("Dashboard: failed to supplement from monthly_transactions: %s", e)

        # now_utc for DB queries, now_local for display boundaries
        now_utc = datetime.utcnow()
        now = now_utc + _LOCAL_OFFSET
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

        # Daily consumption for last 30 days (from transactions)
        daily = defaultdict(float)
        cutoff_30 = now - timedelta(days=30)
        for r in history_rows:
            if r["date"] and r["date"] >= cutoff_30 and r["kwh"] > 0:
                day_key = r["date"].strftime("%Y-%m-%d")
                daily[day_key] += r["kwh"]

        # Supplement with metered consumption, excluding check meters from totals
        consumption_daily = defaultdict(float)
        consumption_monthly = defaultdict(float)
        # Per-source daily breakdown for meter comparison (includes check meters)
        source_daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        try:
            cursor.execute(
                "SELECT h.reading_hour, h.kwh, h.source, "
                "       COALESCE(m.role, 'primary') AS role "
                "FROM hourly_consumption h "
                "LEFT JOIN meters m ON m.meter_id = h.meter_id "
                "WHERE h.account_number = %s AND h.reading_hour >= %s "
                "ORDER BY h.reading_hour",
                (acct, now_utc - timedelta(days=365)),
            )
            for row in cursor.fetchall():
                dt_h = row[0]
                kwh_h = float(row[1] or 0)
                src = row[2] or "unknown"
                role = row[3] or "primary"
                if kwh_h > 0 and dt_h is not None:
                    if isinstance(dt_h, str):
                        try:
                            dt_h = datetime.fromisoformat(dt_h)
                        except ValueError:
                            continue
                    dt_h = _to_local(dt_h)
                    day_str = dt_h.strftime("%Y-%m-%d")
                    source_daily[src][day_str] += kwh_h
                    if role != "check":
                        consumption_daily[day_str] += kwh_h
                        consumption_monthly[dt_h.strftime("%Y-%m")] += kwh_h
        except Exception as e:
            logger.debug("Dashboard: hourly_consumption query failed: %s", e)

        for day_key, kwh_val in consumption_daily.items():
            if kwh_val > daily.get(day_key, 0):
                daily[day_key] = kwh_val

        # Average kWh/day over last 30 days
        days_with_data = len(daily)
        avg_kwh_per_day = sum(daily.values()) / max(days_with_data, 1)

        # Balance via full-history balance engine
        from balance_engine import get_balance_kwh as _be_balance
        from country_config import get_tariff_rate_for_site, get_currency_for_site
        import re as _re
        _be_raw, _ = _be_balance(conn, acct)
        balance_kwh = max(0, _be_raw)
        _site_match = _re.search(r'[A-Z]{3}$', acct)
        _site_code = _site_match.group(0) if _site_match else ""
        _tariff_rate = get_tariff_rate_for_site(_site_code)
        _currency_code = get_currency_for_site(_site_code)

        # Estimated time to recharge (seconds)
        if avg_kwh_per_day > 0 and balance_kwh > 0:
            days_remaining = balance_kwh / avg_kwh_per_day
            estimated_seconds = int(days_remaining * 86400)
        else:
            estimated_seconds = 0

        # Build chart data
        daily_7d = []
        for i in range(6, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_7d.append({"date": d, "kwh": round(daily.get(d, 0), 2)})

        daily_30d = []
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_30d.append({"date": d, "kwh": round(daily.get(d, 0), 2)})

        monthly = defaultdict(float)
        cutoff_12m = now - timedelta(days=365)
        for r in history_rows:
            if r["date"] and r["date"] >= cutoff_12m and r["kwh"] > 0:
                mo_key = r["date"].strftime("%Y-%m")
                monthly[mo_key] += r["kwh"]

        for mo_key, kwh_val in consumption_monthly.items():
            if kwh_val > monthly.get(mo_key, 0):
                monthly[mo_key] = kwh_val

        monthly_12m = []
        for i in range(11, -1, -1):
            d = now - timedelta(days=i * 30)
            mo = d.strftime("%Y-%m")
            monthly_12m.append({"month": mo, "kwh": round(monthly.get(mo, 0), 1)})

        # Build meter comparison: last 7 days per source (for overlay chart)
        meter_comparison = []
        if len(source_daily) > 1:
            source_labels = {
                "thundercloud": "SparkMeter",
                "koios": "SparkMeter",
                "iot": "1Meter Prototype",
            }
            for i in range(6, -1, -1):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                point: dict = {"date": d}
                for src, daily_map in source_daily.items():
                    label = source_labels.get(src, src)
                    point[label] = round(daily_map.get(d, 0), 3)
                meter_comparison.append(point)

        # Hourly consumption for last 24 hours, per source
        hourly_24h: list[dict] = []
        try:
            cutoff_24h_utc = now_utc - timedelta(hours=24)
            cursor.execute(
                "SELECT h.reading_hour, h.kwh, h.source "
                "FROM hourly_consumption h "
                "WHERE h.account_number = %s AND h.reading_hour >= %s "
                "ORDER BY h.reading_hour",
                (acct, cutoff_24h_utc),
            )
            source_labels_h = {
                "thundercloud": "SparkMeter",
                "koios": "SparkMeter",
                "iot": "1Meter Prototype",
            }
            hourly_by_src: dict[str, dict[str, float]] = defaultdict(
                lambda: defaultdict(float)
            )
            for row in cursor.fetchall():
                dt_h = row[0]
                kwh_h = float(row[1] or 0)
                src = row[2] or "unknown"
                if kwh_h > 0 and dt_h is not None:
                    if isinstance(dt_h, str):
                        try:
                            dt_h = datetime.fromisoformat(dt_h)
                        except ValueError:
                            continue
                    dt_h = _to_local(dt_h)
                    hour_str = dt_h.strftime("%Y-%m-%d %H:00")
                    label = source_labels_h.get(src, src)
                    hourly_by_src[label][hour_str] += kwh_h

            all_sources_h = sorted(hourly_by_src.keys())
            cutoff_24h_local = now - timedelta(hours=24)
            for i in range(24):
                h = cutoff_24h_local + timedelta(hours=i)
                hour_str = h.strftime("%Y-%m-%d %H:00")
                pt: dict = {"hour": hour_str}
                for src_label in all_sources_h:
                    pt[src_label] = round(
                        hourly_by_src[src_label].get(hour_str, 0), 4
                    )
                if len(all_sources_h) == 1:
                    pt["kwh"] = pt[all_sources_h[0]]
                hourly_24h.append(pt)
        except Exception as e:
            logger.debug("Dashboard: hourly_24h query failed: %s", e)

        return {
            "balance_kwh": round(balance_kwh, 2),
            "balance_currency": round(balance_kwh * _tariff_rate, 2),
            "currency_code": _currency_code,
            "last_payment": last_payment,
            "avg_kwh_per_day": round(avg_kwh_per_day, 2),
            "estimated_recharge_seconds": estimated_seconds,
            "total_kwh_all_time": round(total_kwh, 1),
            "total_lsl_all_time": round(total_lsl, 2),
            "daily_7d": daily_7d,
            "daily_30d": daily_30d,
            "monthly_12m": monthly_12m,
            "meters": meter_list,
            "meter_comparison": meter_comparison,
            "hourly_24h": hourly_24h,
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

        # Parse account number pattern for community extraction
        import re as _re
        acct_match = _re.match(r"^(\d{3,4})([A-Za-z]{2,4})$", acct)

        # --- Resolve customer profile ---
        profile: dict = {
            "account_number": acct,
            "customer_id": None,
            "first_name": "",
            "last_name": "",
        }
        meter_info: dict = {}

        # Try meters table (single table — tblmeter and Copy Of tblmeter merged)
        try:
            cursor.execute(
                "SELECT * FROM meters WHERE account_number = %s",
                (acct,),
            )
            mrow = cursor.fetchone()
            if mrow:
                meter_info = _row_to_dict(cursor, mrow)
                cust_id = meter_info.get("customer_id_legacy")
                if cust_id:
                    cursor.execute(
                        "SELECT * FROM customers WHERE customer_id_legacy = %s",
                        (str(cust_id),),
                    )
                    crow = cursor.fetchone()
                    if crow:
                        profile = _normalize_customer(_row_to_dict(cursor, crow))
                        profile["account_number"] = acct
        except Exception:
            pass

        # If meters didn't find the customer, try reverse-deriving
        # from plot_number: account 0045MAK -> plot like 'MAK 0045%'
        if not profile.get("customer_id") and acct_match:
            num_part, comm = acct_match.group(1), acct_match.group(2).upper()
            plot_pattern = f"{comm} {num_part}%"
            try:
                cursor.execute(
                    "SELECT * FROM customers WHERE plot_number LIKE %s AND community = %s",
                    (plot_pattern, comm),
                )
                crow = cursor.fetchone()
                if crow:
                    profile = _normalize_customer(_row_to_dict(cursor, crow))
                    profile["account_number"] = acct
            except Exception:
                pass

        # If still no meter info, try to get meter ID from transactions
        if not meter_info:
            try:
                cursor.execute(
                    "SELECT meter_id FROM transactions "
                    "WHERE account_number = %s AND meter_id IS NOT NULL AND meter_id <> '' "
                    "LIMIT 1",
                    (acct,),
                )
                hrow = cursor.fetchone()
                if hrow and hrow[0]:
                    meter_info = {"meter_id": str(hrow[0]).strip()}
                    if acct_match:
                        meter_info["community"] = acct_match.group(2).upper()
            except Exception:
                pass

        # --- Transaction history (most recent first) ---
        transactions = []
        try:
            cursor.execute(
                "SELECT id, account_number, meter_id, transaction_date, "
                "transaction_amount, rate_used, kwh_value, is_payment, current_balance "
                "FROM transactions WHERE account_number = %s "
                "ORDER BY transaction_date DESC",
                (acct,),
            )
            from country_config import UTC_OFFSET_HOURS as _TXN_UTC_OFF
            _txn_offset = timedelta(hours=_TXN_UTC_OFF)
            for r in cursor.fetchall():
                dt_raw = r[3]
                dt_str = None
                if dt_raw is not None:
                    if isinstance(dt_raw, str):
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
                            try:
                                dt_parsed = datetime.strptime(dt_raw.strip(), fmt)
                                dt_str = (dt_parsed + _txn_offset).strftime("%Y-%m-%d %H:%M:%S")
                                break
                            except ValueError:
                                continue
                    else:
                        if hasattr(dt_raw, "strftime"):
                            local_dt = dt_raw.replace(tzinfo=None) + _txn_offset if hasattr(dt_raw, 'tzinfo') and dt_raw.tzinfo else dt_raw
                            dt_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            dt_str = str(dt_raw)

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
                try:
                    txn["balance"] = round(float(r[8] or 0), 2)
                except (ValueError, TypeError):
                    txn["balance"] = None
                transactions.append(txn)
        except Exception as e:
            logger.warning("customer-data: failed to read transactions: %s", e)

        # --- Supplement with monthly_transactions for months beyond history ---
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
            cursor.execute(
                "SELECT year_month, amount_lsl, kwh_vended, "
                "txn_count, meter_id, community "
                "FROM monthly_transactions "
                "WHERE account_number = %s "
                "ORDER BY year_month DESC",
                (acct,),
            )
            for r in cursor.fetchall():
                ym = str(r[0] or "").strip()
                if not ym:
                    continue
                try:
                    y, m_val = int(ym[:4]), int(ym[5:7])
                    row_dt = datetime(y, m_val, 15)  # mid-month placeholder
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
                    "txn_count": n_txn,
                    "yearmonth": ym,
                })
        except Exception as e:
            logger.warning("customer-data: failed to supplement from monthly_transactions: %s", e)

        # Re-sort by date descending after supplementing
        def _txn_sort_key(t):
            try:
                return datetime.strptime(t["date"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError, AttributeError):
                return datetime.min
        transactions.sort(key=_txn_sort_key, reverse=True)

        # --- Compute dashboard aggregates from transactions ---
        from country_config import UTC_OFFSET_HOURS as _EMP_UTC_OFF
        _emp_offset = timedelta(hours=_EMP_UTC_OFF)
        now_utc = datetime.utcnow()
        now = now_utc + _emp_offset
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

        # Balance via full-history balance engine
        from balance_engine import get_balance_kwh as _be_balance
        from country_config import get_tariff_rate_for_site, get_currency_for_site
        _be_raw, _ = _be_balance(conn, acct)
        balance_kwh = max(0, _be_raw)
        _emp_site = (meter_info or {}).get("community") or ""
        _emp_tariff = get_tariff_rate_for_site(_emp_site)
        _emp_currency = get_currency_for_site(_emp_site)

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
                "meter_id": meter_info.get("meter_id"),
                "community": meter_info.get("community"),
                "customer_type": meter_info.get("customer_type"),
                "village": meter_info.get("village_name"),
                "status": meter_info.get("status"),
                "connect_date": str(meter_info.get("customer_connect_date") or ""),
            } if meter_info else None,
            "tariff": tariff_info,
            "dashboard": {
                "balance_kwh": round(balance_kwh, 2),
                "balance_currency": round(balance_kwh * _emp_tariff, 2),
                "currency_code": _emp_currency,
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
