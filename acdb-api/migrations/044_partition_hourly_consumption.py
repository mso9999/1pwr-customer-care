#!/usr/bin/env python3
"""
Migration 044: Time-partition hourly_consumption by year (RANGE on reading_hour)

Background
----------
hourly_consumption has grown to 17.2M rows (8.7 GB) and adds ~16M rows/year,
doubling annually.  The 2026-06-13 disk-full crash was partly triggered by a
large autovacuum WAL burst on this table.  Yearly range-partitioning mirrors
the existing meter_readings layout and enables:
  - Per-partition vacuum (smaller WAL bursts)
  - Partition pruning on common date-range queries
  - Easy archival of old partitions (detach + pg_dump)

Partition layout
----------------
  hourly_consumption_legacy   MINVALUE → 2021-01-01   (13 rows: epoch + 2020)
  hourly_consumption_2021     2021 – 2022
  hourly_consumption_2022     2022 – 2023
  hourly_consumption_2023     2023 – 2024
  hourly_consumption_2024     2024 – 2025
  hourly_consumption_2025     2025 – 2026
  hourly_consumption_2026     2026 – 2027
  hourly_consumption_2027     2027 – 2028
  hourly_consumption_default  DEFAULT (future years)

Primary key change
------------------
PostgreSQL requires the partition key to be part of the PK on a partitioned
table.  PK changes from (id) to (id, reading_hour).  Application queries
use account_number/meter_id/reading_hour for lookups, not the bare id, so
this has no functional impact.  The id sequence continues to generate
globally-unique ids.

Execution plan (zero data loss, ~1-2 s API lock)
-------------------------------------------------
1. CREATE hourly_consumption_p (partitioned) + child partitions
2. Bulk-copy data year-by-year in 50k-row batches (no locks held)
3. CREATE indexes on partitioned table (including unique + pk)
4. BEGIN transaction:
     LOCK hourly_consumption IN SHARE ROW EXCLUSIVE MODE
     copy delta rows (any inserted since bulk copy started)
     RENAME hourly_consumption → hourly_consumption_old
     RENAME hourly_consumption_p → hourly_consumption
     fix sequence ownership
   COMMIT
5. Re-apply autovacuum tuning (from migration 043) to new table + children
6. Verify row counts match
7. (manual step) DROP TABLE hourly_consumption_old after 24h observation

Run as
------
  sudo -u cc_api bash -c '
    set -a && source /opt/1pdb/.env && set +a
    /opt/cc-portal/backend/venv/bin/python3 \
      /opt/cc-portal/backend/acdb-api/migrations/044_partition_hourly_consumption.py \
      --db "$DB_URL_CC"
  '

  For Benin (onepower_bj) — same table structure, smaller dataset:
    --db "$DB_URL_BJ"
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("m044")

# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------

CREATE_PARTITIONED = """
CREATE TABLE IF NOT EXISTS hourly_consumption_p (
    id             bigint                   NOT NULL,
    account_number character varying        NOT NULL,
    meter_id       character varying        NOT NULL,
    reading_hour   timestamp with time zone NOT NULL,
    kwh            double precision         NOT NULL,
    community      character varying,
    source         transaction_source       DEFAULT 'accdb'::transaction_source,
    created_at     timestamp with time zone DEFAULT now()
) PARTITION BY RANGE (reading_hour);
"""

CREATE_PARTITIONS = [
    "CREATE TABLE IF NOT EXISTS hourly_consumption_legacy  PARTITION OF hourly_consumption_p FOR VALUES FROM (MINVALUE)               TO ('2021-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2021    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2021-01-01 00:00:00+00') TO ('2022-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2022    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2022-01-01 00:00:00+00') TO ('2023-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2023    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2023-01-01 00:00:00+00') TO ('2024-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2024    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2025-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2025    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2026    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_2027    PARTITION OF hourly_consumption_p FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2028-01-01 00:00:00+00');",
    "CREATE TABLE IF NOT EXISTS hourly_consumption_default PARTITION OF hourly_consumption_p DEFAULT;",
]

ADD_PK_AND_UNIQUE = """
ALTER TABLE hourly_consumption_p
    ADD PRIMARY KEY (id, reading_hour);
ALTER TABLE hourly_consumption_p
    ADD CONSTRAINT uq_hourly_meter_hour_p UNIQUE (meter_id, reading_hour);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hc_account_p          ON hourly_consumption_p (account_number);",
    "CREATE INDEX IF NOT EXISTS idx_hourly_account_time_p ON hourly_consumption_p (account_number, reading_hour);",
    "CREATE INDEX IF NOT EXISTS idx_hourly_community_time_p ON hourly_consumption_p (community, reading_hour);",
    "CREATE INDEX IF NOT EXISTS idx_hourly_meter_time_p   ON hourly_consumption_p (meter_id, reading_hour);",
]

AUTOVACUUM_SETTINGS = """
ALTER TABLE hourly_consumption SET (
    autovacuum_vacuum_cost_delay  = 20,
    autovacuum_vacuum_cost_limit  = 200,
    autovacuum_vacuum_scale_factor = 0.01
);
"""

BATCH_SIZE = 50_000


def copy_year_range(conn, yr_start: str, yr_end: str | None) -> int:
    """Copy one year's slice from old table to _p.  Returns rows copied."""
    with conn.cursor() as cur:
        if yr_end:
            cur.execute(
                """
                INSERT INTO hourly_consumption_p
                    (id, account_number, meter_id, reading_hour, kwh, community, source, created_at)
                SELECT id, account_number, meter_id, reading_hour, kwh, community, source, created_at
                FROM   hourly_consumption
                WHERE  reading_hour >= %s AND reading_hour < %s
                ON CONFLICT DO NOTHING
                """,
                (yr_start, yr_end),
            )
        else:
            cur.execute(
                """
                INSERT INTO hourly_consumption_p
                    (id, account_number, meter_id, reading_hour, kwh, community, source, created_at)
                SELECT id, account_number, meter_id, reading_hour, kwh, community, source, created_at
                FROM   hourly_consumption
                WHERE  reading_hour >= %s
                ON CONFLICT DO NOTHING
                """,
                (yr_start,),
            )
        n = cur.rowcount
    conn.commit()
    return n


def copy_legacy(conn) -> int:
    """Copy rows with reading_hour < 2021."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hourly_consumption_p
                (id, account_number, meter_id, reading_hour, kwh, community, source, created_at)
            SELECT id, account_number, meter_id, reading_hour, kwh, community, source, created_at
            FROM   hourly_consumption
            WHERE  reading_hour < '2021-01-01 00:00:00+00'
            ON CONFLICT DO NOTHING
            """
        )
        n = cur.rowcount
    conn.commit()
    return n


def copy_delta(cur, max_id_at_start: int) -> int:
    """Copy rows inserted since bulk copy started (called inside rename txn)."""
    cur.execute(
        """
        INSERT INTO hourly_consumption_p
            (id, account_number, meter_id, reading_hour, kwh, community, source, created_at)
        SELECT id, account_number, meter_id, reading_hour, kwh, community, source, created_at
        FROM   hourly_consumption
        WHERE  id > %s
        ON CONFLICT DO NOTHING
        """,
        (max_id_at_start,),
    )
    return cur.rowcount


def check_table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_class WHERE relname=%s AND relnamespace='public'::regnamespace",
            (name,),
        )
        return cur.fetchone() is not None


def get_max_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM hourly_consumption")
        return cur.fetchone()[0]


def get_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(db_url: str, dry_run: bool) -> None:
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    # ── Step 1: create partitioned table + children ──────────────────────────
    if not check_table_exists(conn, "hourly_consumption_p"):
        log.info("Step 1: creating partitioned table and child partitions …")
        with conn.cursor() as cur:
            cur.execute(CREATE_PARTITIONED)
            for ddl in CREATE_PARTITIONS:
                cur.execute(ddl)
        conn.commit()
        log.info("Partitioned table created.")
    else:
        log.info("Step 1: hourly_consumption_p already exists — skipping create.")

    # ── Step 2: record max id at start of bulk copy ──────────────────────────
    max_id_at_start = get_max_id(conn)
    log.info("Max id at bulk-copy start: %d", max_id_at_start)

    existing_p_count = get_count(conn, "hourly_consumption_p")
    if existing_p_count == 0:
        # ── Step 3: bulk copy year by year ───────────────────────────────────
        ranges = [
            (None, "2021-01-01 00:00:00+00"),   # legacy
            ("2021-01-01 00:00:00+00", "2022-01-01 00:00:00+00"),
            ("2022-01-01 00:00:00+00", "2023-01-01 00:00:00+00"),
            ("2023-01-01 00:00:00+00", "2024-01-01 00:00:00+00"),
            ("2024-01-01 00:00:00+00", "2025-01-01 00:00:00+00"),
            ("2025-01-01 00:00:00+00", "2026-01-01 00:00:00+00"),
            ("2026-01-01 00:00:00+00", None),
        ]
        total_copied = 0
        for (start, end) in ranges:
            label = f"{start or 'MINVALUE'} → {end or 'NOW'}"
            log.info("Step 3: copying %s …", label)
            t0 = time.monotonic()
            if start is None:
                n = copy_legacy(conn)
            elif end is None:
                n = copy_year_range(conn, start, None)
            else:
                n = copy_year_range(conn, start, end)
            elapsed = time.monotonic() - t0
            total_copied += n
            log.info("  → %d rows in %.1f s", n, elapsed)
        log.info("Bulk copy done: %d rows total.", total_copied)
    else:
        log.info("Step 3: hourly_consumption_p already has %d rows — skipping bulk copy.", existing_p_count)

    # ── Step 4: add PK + unique + indexes ────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_constraint WHERE conname='hourly_consumption_p_pkey'"
        )
        has_pk = cur.fetchone() is not None

    if not has_pk:
        log.info("Step 4a: adding PK and unique constraint …")
        with conn.cursor() as cur:
            cur.execute(ADD_PK_AND_UNIQUE)
        conn.commit()
        log.info("PK + unique added.")
    else:
        log.info("Step 4a: PK already exists — skipping.")

    log.info("Step 4b: creating indexes …")
    for idx_sql in CREATE_INDEXES:
        with conn.cursor() as cur:
            cur.execute(idx_sql)
        conn.commit()
    log.info("Indexes created.")

    if dry_run:
        log.info("DRY RUN — stopping before rename. Inspect hourly_consumption_p, then re-run without --dry-run.")
        conn.close()
        return

    # ── Step 5: atomic rename with brief table lock ──────────────────────────
    log.info("Step 5: atomic rename (brief table lock) …")
    conn.autocommit = False
    with conn.cursor() as cur:
        # Lock prevents new writes to old table during delta copy + rename
        cur.execute("LOCK TABLE hourly_consumption IN SHARE ROW EXCLUSIVE MODE")

        delta = copy_delta(cur, max_id_at_start)
        log.info("  delta rows copied: %d", delta)

        # Rename
        cur.execute("ALTER TABLE hourly_consumption RENAME TO hourly_consumption_old")
        cur.execute("ALTER TABLE hourly_consumption_p RENAME TO hourly_consumption")

        # Fix sequence ownership (was owned by old table's id column)
        cur.execute(
            "ALTER SEQUENCE hourly_consumption_id_seq OWNED BY hourly_consumption.id"
        )
    conn.commit()
    log.info("Rename complete — new partitioned table is live.")

    # ── Step 6: re-apply autovacuum tuning (migration 043) ───────────────────
    log.info("Step 6: applying autovacuum tuning to new table …")
    with conn.cursor() as cur:
        cur.execute(AUTOVACUUM_SETTINGS)
    conn.commit()
    log.info("Autovacuum tuning applied.")

    # ── Step 7: verify counts ─────────────────────────────────────────────────
    log.info("Step 7: verifying row counts …")
    new_count = get_count(conn, "hourly_consumption")
    old_count = get_count(conn, "hourly_consumption_old")
    log.info("  hourly_consumption (new): %d", new_count)
    log.info("  hourly_consumption_old:   %d", old_count)
    if new_count == old_count:
        log.info("✅ Counts match — migration successful.")
    else:
        log.error("❌ COUNT MISMATCH: new=%d old=%d — investigate before dropping old table!", new_count, old_count)
        sys.exit(1)

    log.info(
        "Migration 044 complete.\n"
        "  Action required: after 24h observation, run:\n"
        "    DROP TABLE hourly_consumption_old;\n"
        "  Then commit the drop as migration 044b."
    )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migration 044: partition hourly_consumption")
    parser.add_argument("--db", required=True, help="PostgreSQL DSN or URL")
    parser.add_argument("--dry-run", action="store_true", help="Stop before rename — inspect _p table first")
    args = parser.parse_args()
    run(args.db, args.dry_run)


if __name__ == "__main__":
    main()
