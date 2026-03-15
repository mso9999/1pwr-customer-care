"""
Manual compact/repair for Access database.

Copies all user tables from a corrupt .accdb to a new clean .accdb,
effectively performing what Access's Compact and Repair does internally.

Usage:
    python compact_accdb.py [source.accdb]

The clean file is created alongside the source with a _clean suffix.
After verifying, rename manually to replace the original.
"""

import os
import sys
import time
import logging

import pyodbc

pyodbc.pooling = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"

# Access ODBC type_name → DDL type
DDL_TYPE_MAP = {
    "COUNTER": "AUTOINCREMENT",
    "LONG": "LONG",
    "INTEGER": "LONG",
    "SHORT": "SHORT",
    "SMALLINT": "SHORT",
    "BYTE": "BYTE",
    "TINYINT": "BYTE",
    "BIT": "BIT",
    "DOUBLE": "DOUBLE",
    "FLOAT": "DOUBLE",
    "SINGLE": "SINGLE",
    "REAL": "SINGLE",
    "CURRENCY": "CURRENCY",
    "DECIMAL": "CURRENCY",
    "NUMERIC": "DOUBLE",
    "DATETIME": "DATETIME",
    "DATE": "DATETIME",
    "VARCHAR": "TEXT",
    "CHAR": "TEXT",
    "LONGCHAR": "MEMO",
    "LONGBINARY": "LONGBINARY",
    "BINARY": "BINARY",
    "VARBINARY": "BINARY",
    "GUID": "TEXT(38)",
}


def get_user_tables(cursor):
    """Return list of user table names (excluding system/temp tables)."""
    tables = []
    for t in cursor.tables(tableType="TABLE"):
        name = t.table_name
        if name.startswith("MSys") or name.startswith("~"):
            continue
        tables.append(name)
    return sorted(tables)


def build_create_ddl(cursor, table_name):
    """Generate CREATE TABLE DDL from ODBC column metadata."""
    col_defs = []
    col_meta = []  # (name, is_autoincrement)

    for col in cursor.columns(table=table_name):
        cname = col.column_name
        tname = (col.type_name or "VARCHAR").upper().strip()
        size = col.column_size
        nullable = col.nullable

        if tname == "COUNTER":
            col_defs.append(f"[{cname}] AUTOINCREMENT PRIMARY KEY")
            col_meta.append((cname, True))
            continue

        ddl_type = DDL_TYPE_MAP.get(tname, "TEXT(255)")
        col_meta.append((cname, False))

        # Types that need a size specifier
        if ddl_type == "TEXT":
            s = min(size, 255) if size and size > 0 else 255
            ddl_type = f"TEXT({s})"
        elif ddl_type == "BINARY":
            s = min(size, 510) if size and size > 0 else 510
            ddl_type = f"BINARY({s})"

        null_clause = "" if nullable else " NOT NULL"
        col_defs.append(f"[{cname}] {ddl_type}{null_clause}")

    ddl = f"CREATE TABLE [{table_name}] (\n  " + ",\n  ".join(col_defs) + "\n)"
    return ddl, col_meta


def copy_table_data(src_conn, dst_conn, table_name, col_meta, batch_size=2000):
    """Copy all rows from source table to destination, skipping AUTOINCREMENT."""
    # Build column list (exclude autoincrement)
    copy_cols = [name for name, is_auto in col_meta if not is_auto]

    if not copy_cols:
        return 0

    col_list = ", ".join(f"[{c}]" for c in copy_cols)
    placeholders = ", ".join("?" for _ in copy_cols)
    insert_sql = (
        f"INSERT INTO [{table_name}] ({col_list}) VALUES ({placeholders})"
    )

    src_cur = src_conn.cursor()
    dst_cur = dst_conn.cursor()

    src_cur.execute(f"SELECT {col_list} FROM [{table_name}]")

    inserted = 0
    while True:
        rows = src_cur.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            dst_cur.execute(insert_sql, tuple(row))
            inserted += 1
        if inserted % 50000 == 0 and inserted > 0:
            logger.info("  %s: %d rows...", table_name, inserted)

    src_cur.close()
    dst_cur.close()
    return inserted


def main():
    # Paths
    src_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else r"C:\Users\Administrator\Desktop\AccessDB_Clone\0112023_1PWRKMETER.accdb"
    )
    dst_path = src_path.replace(".accdb", "_clean.accdb")

    if not os.path.isfile(src_path):
        logger.error("Source file not found: %s", src_path)
        sys.exit(1)

    if os.path.exists(dst_path):
        os.remove(dst_path)
        logger.info("Removed existing destination: %s", dst_path)

    # Create blank destination database via ADOX
    logger.info("Creating clean database...")
    try:
        import win32com.client

        cat = win32com.client.Dispatch("ADOX.Catalog")
        cat.Create(
            f"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={dst_path}"
        )
        cat.ActiveConnection.Close()
        logger.info("Created: %s", dst_path)
    except Exception as e:
        logger.error("Cannot create new database (ADOX): %s", e)
        logger.error(
            "Install pywin32 (pip install pywin32) and ensure ACE OLEDB is available."
        )
        sys.exit(1)

    # Open connections
    src_conn = pyodbc.connect(
        f"DRIVER={DRIVER};DBQ={src_path}", autocommit=True
    )
    dst_conn = pyodbc.connect(
        f"DRIVER={DRIVER};DBQ={dst_path}", autocommit=True
    )

    src_cur = src_conn.cursor()

    # Enumerate tables
    tables = get_user_tables(src_cur)
    logger.info("Found %d user tables to migrate", len(tables))

    t0 = time.time()
    total_rows = 0
    succeeded = 0
    failed = []

    for i, tbl in enumerate(tables, 1):
        logger.info("[%d/%d] %s", i, len(tables), tbl)

        # Build DDL
        try:
            ddl, col_meta = build_create_ddl(src_cur, tbl)
        except Exception as e:
            logger.error("  Schema read failed: %s", e)
            failed.append((tbl, f"schema: {e}"))
            continue

        # Create table in destination
        try:
            dst_cur = dst_conn.cursor()
            dst_cur.execute(ddl)
            dst_cur.close()
        except Exception as e:
            logger.error("  CREATE TABLE failed: %s", e)
            logger.error("  DDL: %s", ddl)
            failed.append((tbl, f"create: {e}"))
            continue

        # Copy data
        try:
            n = copy_table_data(src_conn, dst_conn, tbl, col_meta)
            total_rows += n
            succeeded += 1
            if n > 0:
                logger.info("  Copied %d rows", n)
            else:
                logger.info("  Empty table (schema only)")
        except Exception as e:
            logger.error("  Data copy failed: %s", e)
            failed.append((tbl, f"data: {e}"))

    elapsed = time.time() - t0
    src_conn.close()
    dst_conn.close()

    # Summary
    logger.info("=" * 60)
    logger.info("MIGRATION COMPLETE in %.1f seconds", elapsed)
    logger.info("  Tables succeeded: %d / %d", succeeded, len(tables))
    logger.info("  Total rows copied: %d", total_rows)
    if failed:
        logger.warning("  Failed tables:")
        for tbl, reason in failed:
            logger.warning("    %s: %s", tbl, reason)
    logger.info("=" * 60)

    src_base = os.path.basename(src_path)
    logger.info("")
    logger.info("To swap files, run:")
    logger.info(
        '  Rename-Item "%s" "%s.corrupt"',
        src_path,
        src_base,
    )
    logger.info(
        '  Rename-Item "%s" "%s"',
        dst_path,
        src_base,
    )


if __name__ == "__main__":
    main()
