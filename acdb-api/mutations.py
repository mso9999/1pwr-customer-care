"""
Mutation log: records every create/update/delete through the CC Portal API.
Provides list, detail, and revert endpoints.
"""

import json
import logging
import math
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models import CCRole, CurrentUser
from middleware import get_current_user, require_employee
from db_auth import get_auth_db

logger = logging.getLogger("acdb-api.mutations")

router = APIRouter(prefix="/api/mutations", tags=["mutations"])


# ---------------------------------------------------------------------------
# Logging helper (called from crud.py)
# ---------------------------------------------------------------------------

def log_mutation(
    user: CurrentUser,
    action: str,
    table_name: str,
    record_id: str,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Record a mutation in the SQLite audit log.
    Returns the mutation ID.
    """
    with get_auth_db() as conn:
        cursor = conn.execute(
            """INSERT INTO cc_mutations
               (user_type, user_id, user_name, action, table_name, record_id, old_values, new_values)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.user_type.value,
                user.user_id,
                user.name or user.user_id,
                action,
                table_name,
                str(record_id),
                json.dumps(old_values) if old_values else None,
                json.dumps(new_values) if new_values else None,
            ),
        )
        mutation_id = cursor.lastrowid
        logger.info(
            "Mutation #%d: %s %s.%s by %s (%s)",
            mutation_id, action, table_name, record_id, user.user_id, user.user_type.value,
        )
        return mutation_id


# ---------------------------------------------------------------------------
# Helper: get primary key column for a PostgreSQL table
# ---------------------------------------------------------------------------

def _get_primary_key(conn, table_name: str) -> Optional[str]:
    """Detect the primary key column for a PostgreSQL table via pg_index."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid
                            AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass
          AND i.indisprimary
    """, (table_name,))
    row = cursor.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Helper: fetch a row from PostgreSQL
# ---------------------------------------------------------------------------

def _fetch_pg_row(table_name: str, pk: str, record_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single row from PostgreSQL as a dict."""
    from customer_api import get_connection

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s", (record_id,))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        d = {}
        for col, val in zip(columns, row):
            if val is not None and not isinstance(val, (str, int, float, bool)):
                val = str(val)
            d[col] = val
        return d


# ---------------------------------------------------------------------------
# List mutations
# ---------------------------------------------------------------------------

@router.get("")
def list_mutations(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    table: Optional[str] = Query(None),
    user_id_filter: Optional[str] = Query(None, alias="user"),
    action: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """List mutations with pagination and optional filters."""
    with get_auth_db() as conn:
        where_parts = []
        params = []

        if table:
            where_parts.append("table_name = ?")
            params.append(table)
        if user_id_filter:
            where_parts.append("user_id = ?")
            params.append(user_id_filter)
        if action:
            where_parts.append("action = ?")
            params.append(action)

        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

        # Count
        count_row = conn.execute(f"SELECT COUNT(*) FROM cc_mutations{where_sql}", params).fetchone()
        total = count_row[0]

        # Fetch page
        offset = (page - 1) * limit
        rows = conn.execute(
            f"""SELECT id, timestamp, user_type, user_id, user_name, action,
                       table_name, record_id, reverted, reverted_by, reverted_at
                FROM cc_mutations{where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return {
            "mutations": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, math.ceil(total / limit)),
        }


# ---------------------------------------------------------------------------
# Get single mutation detail
# ---------------------------------------------------------------------------

@router.get("/{mutation_id}")
def get_mutation(
    mutation_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Get full mutation detail including old/new values."""
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM cc_mutations WHERE id = ?", (mutation_id,)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Mutation not found")

        result = dict(row)
        # Parse JSON fields
        if result.get("old_values"):
            result["old_values"] = json.loads(result["old_values"])
        if result.get("new_values"):
            result["new_values"] = json.loads(result["new_values"])

        return result


# ---------------------------------------------------------------------------
# Revert a mutation
# ---------------------------------------------------------------------------

@router.post("/{mutation_id}/revert")
def revert_mutation(
    mutation_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """
    Revert a mutation. Superadmin or onm_team only.
    - create -> DELETE the record
    - update -> UPDATE back to old_values
    - delete -> INSERT old_values back
    """
    if user.role not in (CCRole.superadmin.value, CCRole.onm_team.value):
        raise HTTPException(status_code=403, detail="Revert requires superadmin or onm_team role")

    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM cc_mutations WHERE id = ?", (mutation_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Mutation not found")

    mutation = dict(row)
    if mutation["reverted"]:
        raise HTTPException(status_code=400, detail="Mutation already reverted")

    action = mutation["action"]
    table_name = mutation["table_name"]
    record_id = mutation["record_id"]
    old_values = json.loads(mutation["old_values"]) if mutation["old_values"] else None
    new_values = json.loads(mutation["new_values"]) if mutation["new_values"] else None

    from customer_api import get_connection

    with get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key for table")

        cursor = conn.cursor()

        try:
            if action == "create":
                # Undo create = delete the record
                cursor.execute(f"DELETE FROM {table_name} WHERE {pk} = %s", (record_id,))
                conn.commit()
                log_mutation(user, "revert_delete", table_name, record_id, old_values=new_values)

            elif action == "update":
                if not old_values:
                    raise HTTPException(status_code=400, detail="No old values to restore")
                # Undo update = set back to old values
                set_parts = [f"{col} = %s" for col in old_values.keys() if col != pk]
                vals = [old_values[col] for col in old_values.keys() if col != pk] + [record_id]
                cursor.execute(
                    f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE {pk} = %s",
                    vals,
                )
                conn.commit()
                log_mutation(user, "revert_update", table_name, record_id, old_values=new_values, new_values=old_values)

            elif action == "delete":
                if not old_values:
                    raise HTTPException(status_code=400, detail="No old values to restore")
                # Undo delete = re-insert old values
                columns = list(old_values.keys())
                placeholders = ", ".join(["%s"] * len(columns))
                col_list = ", ".join(columns)
                vals = [old_values[c] for c in columns]
                cursor.execute(
                    f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})",
                    vals,
                )
                conn.commit()
                log_mutation(user, "revert_create", table_name, record_id, new_values=old_values)

            else:
                raise HTTPException(status_code=400, detail=f"Cannot revert action type: {action}")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Revert failed: {e}")

    # Mark original mutation as reverted
    from datetime import datetime
    with get_auth_db() as conn:
        conn.execute(
            "UPDATE cc_mutations SET reverted = 1, reverted_by = ?, reverted_at = ? WHERE id = ?",
            (user.user_id, datetime.utcnow().isoformat(), mutation_id),
        )

    return {"message": f"Mutation #{mutation_id} reverted successfully", "action": action, "table": table_name, "record_id": record_id}
