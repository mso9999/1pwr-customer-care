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
    Uses pg_stat_user_tables for approximate row counts (single query).
    """
    with _get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT t.table_name,
                   COALESCE(c.reltuples, -1)::bigint AS row_count,
                   COUNT(col.column_name)::int AS column_count
            FROM information_schema.tables t
            LEFT JOIN pg_class c
                   ON c.relname = t.table_name
                  AND c.relnamespace = 'public'::regnamespace
            LEFT JOIN information_schema.columns col
                   ON col.table_schema = t.table_schema
                  AND col.table_name = t.table_name
            WHERE t.table_schema = 'public'
              AND t.table_type = 'BASE TABLE'
            GROUP BY t.table_name, c.reltuples
            ORDER BY t.table_name
        """)

        tables = []
        for row in cursor.fetchall():
            tables.append(TableInfo(
                name=row[0],
                row_count=max(row[1], 0),
                column_count=row[2],
            ))

        logger.info("Discovered %d tables in database", len(tables))
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
