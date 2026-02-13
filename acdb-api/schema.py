"""
ACCDB schema introspection endpoints.

Uses pyodbc cursor.tables() and cursor.columns() to discover
all tables and their columns in the Access database.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from models import ColumnInfo, CurrentUser, TableInfo
from middleware import require_employee

logger = logging.getLogger("acdb-api.schema")

router = APIRouter(prefix="/api/schema", tags=["schema"])


# System/internal tables to exclude from listings
_SYSTEM_PREFIXES = ("MSys", "~", "f_")
_SYSTEM_TYPES = {"SYSTEM TABLE", "VIEW"}


def _get_connection():
    """Import lazily to avoid circular imports."""
    from customer_api import get_connection
    return get_connection()


def _discover_tables(cursor) -> list[str]:
    """
    Discover all user tables in the ACCDB.
    Tries multiple tableType values to catch standard tables,
    linked tables, and other Access-specific types.
    """
    seen = set()

    # First pass: no filter -- get everything, then filter by type
    try:
        for row in cursor.tables():
            name = row.table_name
            ttype = (row.table_type or "").upper()
            if ttype in _SYSTEM_TYPES:
                continue
            if any(name.startswith(p) for p in _SYSTEM_PREFIXES):
                continue
            seen.add(name)
    except Exception as e:
        logger.warning("Unfiltered table scan failed: %s", e)

    # Fallback: if nothing found, try specific types
    if not seen:
        for table_type in ("TABLE", "LINK", "ACCESS TABLE"):
            try:
                for row in cursor.tables(tableType=table_type):
                    name = row.table_name
                    if any(name.startswith(p) for p in _SYSTEM_PREFIXES):
                        continue
                    seen.add(name)
            except Exception:
                continue

    return sorted(seen)


@router.get("/tables", response_model=List[TableInfo])
def list_tables(user: CurrentUser = Depends(require_employee)):
    """
    List all user tables in the ACCDB with row counts and column counts.
    Excludes system tables (MSys*, ~TMP*, etc.).
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        table_names = _discover_tables(cursor)
        logger.info("Discovered %d tables in ACCDB", len(table_names))

        tables = []
        for name in table_names:
            # Get row count
            try:
                cursor.execute(f"SELECT COUNT(*) FROM [{name}]")
                row_count = cursor.fetchone()[0]
            except Exception as e:
                logger.debug("Could not count rows in %s: %s", name, e)
                row_count = -1

            # Get column count
            try:
                cols = list(cursor.columns(table=name))
                col_count = len(cols)
            except Exception:
                col_count = -1

            tables.append(TableInfo(name=name, row_count=row_count, column_count=col_count))

        return tables


@router.get("/tables/{table_name}/columns", response_model=List[ColumnInfo])
def list_columns(table_name: str, user: CurrentUser = Depends(require_employee)):
    """
    List all columns for a specific table with type info.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        columns = []
        for col in cursor.columns(table=table_name):
            columns.append(ColumnInfo(
                name=col.column_name,
                type_name=col.type_name,
                nullable=col.nullable == 1,
                size=col.column_size,
            ))

        if not columns:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found or has no columns")

        return columns
