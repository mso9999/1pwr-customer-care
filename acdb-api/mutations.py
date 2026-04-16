"""
Mutation log: records create/update/delete through the CC Portal API (CRUD and
material workflows). Manual portal payments (``POST /api/payments/record``) log
here as ``transactions`` creates with ``metadata.kind = manual_payment``.
Provides list, detail, and revert endpoints.
"""

import json
import logging
import math
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query

from db_auth import get_auth_db
from middleware import require_employee
from models import CCRole, CurrentUser

logger = logging.getLogger("acdb-api.mutations")

router = APIRouter(prefix="/api/mutations", tags=["mutations"])

LEGACY_BACKFILL_SOURCE = "sqlite_backfill"
SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "private_key",
    "api_key",
    "authorization",
)
_REVERSIBLE_ACTIONS = {
    "create": "revert_delete",
    "update": "revert_update",
    "delete": "revert_create",
}
_legacy_backfill_done = False
_legacy_backfill_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def _sanitize_payload(value: Any) -> Any:
    """Recursively strip secrets and coerce payloads into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            sanitized[key] = "[redacted]" if _is_sensitive_key(key) else _sanitize_payload(item)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_payload(item) for item in value]
    return str(value)


def _json_arg(payload: Optional[Dict[str, Any]]) -> Any:
    sanitized = _sanitize_payload(payload)
    return psycopg2.extras.Json(sanitized) if sanitized is not None else None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    candidates = (
        text,
        text.replace("Z", "+00:00"),
        text.replace(" ", "T"),
        text.replace(" ", "T").replace("Z", "+00:00"),
    )
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _format_api_timestamp(value: Any) -> Optional[str]:
    parsed = _parse_timestamp(value)
    if parsed is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    text = str(value or "").strip()
    return text or None


def _decode_payload(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, memoryview):
        value = value.tobytes().decode()
    if isinstance(value, bytes):
        value = value.decode()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _mutation_failure_context(
    user: CurrentUser,
    action: str,
    table_name: str,
    record_id: str,
) -> str:
    payload = {
        "action": action,
        "table_name": table_name,
        "record_id": str(record_id),
        "user_id": user.user_id,
        "user_type": user.user_type.value,
        "actor_role": user.role,
    }
    return json.dumps(payload, sort_keys=True)


def log_mutation(
    user: CurrentUser,
    action: str,
    table_name: str,
    record_id: str,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    conn=None,
    reverts_mutation_id: Optional[int] = None,
    source_system: str = "cc_api",
    source_mutation_id: Optional[int] = None,
    timestamp_override: Any = None,
    skip_backfill: bool = False,
) -> int:
    """
    Record a mutation in the PostgreSQL audit log.
    If `conn` is provided, the audit row is written into the caller's transaction.
    """
    from customer_api import get_connection

    if not skip_backfill:
        _ensure_legacy_backfill()

    own_connection = conn is None
    if own_connection:
        ctx = get_connection()
        conn = ctx.__enter__()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cc_mutations (
                    timestamp,
                    user_type,
                    user_id,
                    user_name,
                    actor_role,
                    action,
                    table_name,
                    record_id,
                    old_values,
                    new_values,
                    event_metadata,
                    reverts_mutation_id,
                    source_system,
                    source_mutation_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_mutation_id) DO NOTHING
                RETURNING id
                """,
                (
                    _parse_timestamp(timestamp_override) or datetime.now(timezone.utc),
                    user.user_type.value,
                    user.user_id,
                    user.name or user.user_id,
                    user.role,
                    action,
                    table_name,
                    str(record_id),
                    _json_arg(old_values),
                    _json_arg(new_values),
                    _json_arg(metadata),
                    reverts_mutation_id,
                    source_system,
                    source_mutation_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT id FROM cc_mutations WHERE source_mutation_id = %s",
                    (source_mutation_id,),
                )
                row = cursor.fetchone()
            mutation_id = int(row[0])

        if own_connection:
            conn.commit()

        logger.info(
            "Mutation #%d: %s %s.%s by %s (%s)",
            mutation_id,
            action,
            table_name,
            record_id,
            user.user_id,
            user.user_type.value,
        )
        return mutation_id
    except Exception:
        if own_connection:
            conn.rollback()
        raise
    finally:
        if own_connection:
            ctx.__exit__(None, None, None)


def try_log_mutation(*args, **kwargs) -> Optional[int]:
    """Best-effort audit logging for non-PostgreSQL write paths."""
    user: CurrentUser = args[0] if len(args) > 0 else kwargs["user"]
    action: str = args[1] if len(args) > 1 else kwargs["action"]
    table_name: str = args[2] if len(args) > 2 else kwargs["table_name"]
    record_id: str = args[3] if len(args) > 3 else kwargs["record_id"]
    try:
        return log_mutation(*args, **kwargs)
    except Exception:
        logger.exception(
            "Audit logging failed %s",
            _mutation_failure_context(user, action, table_name, record_id),
        )
        return None


# ---------------------------------------------------------------------------
# Legacy SQLite backfill
# ---------------------------------------------------------------------------

def _ensure_legacy_backfill() -> None:
    global _legacy_backfill_done
    if _legacy_backfill_done:
        return

    with _legacy_backfill_lock:
        if _legacy_backfill_done:
            return
        _backfill_legacy_mutations()
        _legacy_backfill_done = True


def _backfill_legacy_mutations() -> None:
    from customer_api import get_connection

    try:
        with get_auth_db() as sqlite_conn:
            sqlite_rows = sqlite_conn.execute(
                """
                SELECT id, timestamp, user_type, user_id, user_name, action,
                       table_name, record_id, old_values, new_values,
                       reverted, reverted_by, reverted_at
                FROM cc_mutations
                ORDER BY id ASC
                """
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            logger.info("Legacy SQLite mutation table not present; skipping backfill")
            return
        raise

    if not sqlite_rows:
        return

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(source_mutation_id), 0) FROM cc_mutations WHERE source_system = %s",
                (LEGACY_BACKFILL_SOURCE,),
            )
            last_source_id = int(cursor.fetchone()[0] or 0)

        pending_rows = [dict(row) for row in sqlite_rows if int(row["id"]) > last_source_id]
        if not pending_rows:
            return

        logger.info("Backfilling %d legacy SQLite mutation rows into PostgreSQL", len(pending_rows))

        for row in pending_rows:
            metadata: Dict[str, Any] = {"legacy_source": "sqlite_cc_mutations"}
            if row.get("reverted"):
                metadata.update(
                    {
                        "legacy_reverted": True,
                        "legacy_reverted_by": row.get("reverted_by"),
                        "legacy_reverted_at": _format_api_timestamp(row.get("reverted_at")),
                    }
                )
            if str(row.get("action", "")).startswith("revert_"):
                metadata["legacy_unlinked_revert_event"] = True

            legacy_user = CurrentUser(
                user_type=row["user_type"],
                user_id=str(row["user_id"]),
                role="legacy_backfill",
                name=str(row["user_name"] or row["user_id"]),
            )
            log_mutation(
                legacy_user,
                str(row["action"]),
                str(row["table_name"]),
                str(row["record_id"]),
                old_values=_decode_payload(row.get("old_values")),
                new_values=_decode_payload(row.get("new_values")),
                metadata=metadata,
                conn=conn,
                source_system=LEGACY_BACKFILL_SOURCE,
                source_mutation_id=int(row["id"]),
                timestamp_override=row.get("timestamp"),
                skip_backfill=True,
            )

        conn.commit()


# ---------------------------------------------------------------------------
# Helper: get primary key column for a PostgreSQL table
# ---------------------------------------------------------------------------

def _get_primary_key(conn, table_name: str) -> Optional[str]:
    """Detect the primary key column for a PostgreSQL table via pg_index."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid
                                AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass
              AND i.indisprimary
            """,
            (table_name,),
        )
        row = cursor.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Helper: fetch a row from PostgreSQL
# ---------------------------------------------------------------------------

def _fetch_pg_row(table_name: str, pk: str, record_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single row from PostgreSQL as a dict."""
    from customer_api import get_connection

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s", (record_id,))
            row = cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            payload: Dict[str, Any] = {}
            for col, val in zip(columns, row):
                payload[col] = _sanitize_payload(val)
            return payload


def _mutation_select_sql(include_payload: bool = False) -> str:
    payload_sql = ", m.old_values, m.new_values" if include_payload else ""
    return f"""
        SELECT
            m.id,
            m.timestamp,
            m.user_type,
            m.user_id,
            m.user_name,
            m.actor_role,
            m.action,
            m.table_name,
            m.record_id,
            CASE
                WHEN rev.id IS NOT NULL THEN 1
                WHEN COALESCE((m.event_metadata ->> 'legacy_reverted')::boolean, FALSE) THEN 1
                ELSE 0
            END AS reverted,
            COALESCE(rev.user_id, m.event_metadata ->> 'legacy_reverted_by') AS reverted_by,
            COALESCE(
                TO_CHAR(rev.timestamp AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
                m.event_metadata ->> 'legacy_reverted_at'
            ) AS reverted_at
            {payload_sql}
        FROM cc_mutations m
        LEFT JOIN LATERAL (
            SELECT r.id, r.user_id, r.timestamp
            FROM cc_mutations r
            WHERE r.reverts_mutation_id = m.id
            ORDER BY r.id DESC
            LIMIT 1
        ) rev ON TRUE
    """


def _hydrate_mutation(row: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    result["timestamp"] = _format_api_timestamp(result.get("timestamp"))
    result["reverted"] = int(result.get("reverted") or 0)
    result["reverted_at"] = _format_api_timestamp(result.get("reverted_at"))
    if "old_values" in result:
        result["old_values"] = _decode_payload(result.get("old_values"))
    if "new_values" in result:
        result["new_values"] = _decode_payload(result.get("new_values"))
    return result


def _fetch_mutation_record(mutation_id: int, *, include_payload: bool) -> Optional[Dict[str, Any]]:
    from customer_api import get_connection

    _ensure_legacy_backfill()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                f"{_mutation_select_sql(include_payload)} WHERE m.id = %s",
                (mutation_id,),
            )
            row = cursor.fetchone()
    return _hydrate_mutation(dict(row)) if row else None


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
    from customer_api import get_connection

    _ensure_legacy_backfill()
    where_parts = []
    params = []

    if table:
        where_parts.append("m.table_name = %s")
        params.append(table)
    if user_id_filter:
        where_parts.append("m.user_id = %s")
        params.append(user_id_filter)
    if action:
        where_parts.append("m.action = %s")
        params.append(action)

    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    offset = (page - 1) * limit

    with get_connection() as conn:
        with conn.cursor() as count_cursor:
            count_cursor.execute(f"SELECT COUNT(*) FROM cc_mutations m{where_sql}", params)
            total = int(count_cursor.fetchone()[0])

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                f"""
                {_mutation_select_sql(include_payload=False)}
                {where_sql}
                ORDER BY m.id DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cursor.fetchall()

    return {
        "mutations": [_hydrate_mutation(dict(row)) for row in rows],
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
    mutation = _fetch_mutation_record(mutation_id, include_payload=True)
    if not mutation:
        raise HTTPException(status_code=404, detail="Mutation not found")
    return mutation


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

    mutation = _fetch_mutation_record(mutation_id, include_payload=True)
    if not mutation:
        raise HTTPException(status_code=404, detail="Mutation not found")
    if mutation["reverted"]:
        raise HTTPException(status_code=400, detail="Mutation already reverted")

    action = mutation["action"]
    table_name = mutation["table_name"]
    record_id = mutation["record_id"]
    old_values = mutation.get("old_values")
    new_values = mutation.get("new_values")

    if action not in _REVERSIBLE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Cannot revert action type: {action}")

    from customer_api import get_connection

    with get_connection() as conn:
        pk = _get_primary_key(conn, table_name)
        if not pk:
            raise HTTPException(status_code=400, detail="Cannot determine primary key for table")

        try:
            with conn.cursor() as cursor:
                if action == "create":
                    cursor.execute(f"DELETE FROM {table_name} WHERE {pk} = %s", (record_id,))
                    log_mutation(
                        user,
                        "revert_delete",
                        table_name,
                        record_id,
                        old_values=new_values,
                        conn=conn,
                        reverts_mutation_id=mutation_id,
                    )

                elif action == "update":
                    if not old_values:
                        raise HTTPException(status_code=400, detail="No old values to restore")
                    set_parts = [f"{col} = %s" for col in old_values.keys() if col != pk]
                    vals = [old_values[col] for col in old_values.keys() if col != pk] + [record_id]
                    cursor.execute(
                        f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE {pk} = %s",
                        vals,
                    )
                    log_mutation(
                        user,
                        "revert_update",
                        table_name,
                        record_id,
                        old_values=new_values,
                        new_values=old_values,
                        conn=conn,
                        reverts_mutation_id=mutation_id,
                    )

                elif action == "delete":
                    if not old_values:
                        raise HTTPException(status_code=400, detail="No old values to restore")
                    columns = list(old_values.keys())
                    placeholders = ", ".join(["%s"] * len(columns))
                    col_list = ", ".join(columns)
                    vals = [old_values[c] for c in columns]
                    cursor.execute(
                        f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})",
                        vals,
                    )
                    log_mutation(
                        user,
                        "revert_create",
                        table_name,
                        record_id,
                        new_values=old_values,
                        conn=conn,
                        reverts_mutation_id=mutation_id,
                    )

            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Revert failed: {e}")

    return {
        "message": f"Mutation #{mutation_id} reverted successfully",
        "action": action,
        "table": table_name,
        "record_id": record_id,
    }
