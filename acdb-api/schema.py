"""
PostgreSQL schema introspection endpoints.

Uses information_schema to discover all tables and their columns
in the 1PDB PostgreSQL database.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from models import ColumnInfo, CurrentUser, TableInfo
from middleware import require_employee

logger = logging.getLogger("cc-api.schema")

router = APIRouter(prefix="/api/schema", tags=["schema"])


def _get_connection():
    """Import lazily to avoid circular imports."""
    from customer_api import get_connection
    return get_connection()


@router.get("/tables", response_model=List[TableInfo])
def list_tables(user: CurrentUser = Depends(require_employee)):
    """
    List all user tables in the database with row counts and column counts.
    Excludes system tables.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        # Get all public tables
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        table_names = [r[0] for r in cursor.fetchall()]
        logger.info("Discovered %d tables in database", len(table_names))

        tables = []
        for name in table_names:
            # Get row count (approximate for large tables)
            try:
                cursor.execute(
                    "SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
                    (name,),
                )
                result = cursor.fetchone()
                row_count = result[0] if result else -1
                # If stats are stale (0 for non-empty table), do exact count
                if row_count == 0:
                    cursor.execute(f"SELECT COUNT(*) FROM {name}")
                    row_count = cursor.fetchone()[0]
            except Exception as e:
                logger.debug("Could not count rows in %s: %s", name, e)
                row_count = -1

            # Get column count
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    (name,),
                )
                col_count = cursor.fetchone()[0]
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
        cursor.execute(
            "SELECT column_name, data_type, is_nullable, "
            "character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table_name,),
        )
        rows = cursor.fetchall()

        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"Table '{table_name}' not found or has no columns",
            )

        columns = []
        for r in rows:
            columns.append(ColumnInfo(
                name=r[0],
                type_name=r[1],
                nullable=r[2] == "YES",
                size=r[3],
            ))

        return columns
