#!/usr/bin/env python3
"""
Normalize obvious watt-valued rows in meter_readings.power_kw.

This script is intentionally conservative:
- only touches rows where power_kw > 20
- limits updates to known at-risk sources by default
- supports dry-run previews before applying changes
"""

import argparse
import os
from typing import List

import psycopg2


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)
DEFAULT_SOURCES = ["iot", "thundercloud", "accdb"]


def _connect():
    return psycopg2.connect(DATABASE_URL)


def _source_filter_sql(sources: List[str]) -> tuple[str, List[str]]:
    if not sources:
        return "", []
    placeholders = ", ".join(["%s"] * len(sources))
    return f" AND source IN ({placeholders})", list(sources)


def preview_rows(conn, threshold_kw: float, sources: List[str]) -> None:
    where_sql, params = _source_filter_sql(sources)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT source, COUNT(*), MIN(power_kw), MAX(power_kw)
            FROM meter_readings
            WHERE power_kw IS NOT NULL
              AND power_kw > %s
              {where_sql}
            GROUP BY source
            ORDER BY source
            """,
            [threshold_kw, *params],
        )
        rows = cur.fetchall()
        if not rows:
            print("No suspect rows found.")
            return
        print("Suspect rows by source:")
        for source, count, min_kw, max_kw in rows:
            print(
                f"  {source}: {count} rows, min={float(min_kw):.4f}, "
                f"max={float(max_kw):.4f}"
            )


def apply_fix(conn, threshold_kw: float, sources: List[str]) -> int:
    where_sql, params = _source_filter_sql(sources)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE meter_readings
            SET power_kw = power_kw / 1000.0
            WHERE power_kw IS NOT NULL
              AND power_kw > %s
              {where_sql}
            """,
            [threshold_kw, *params],
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize mixed W/kW rows in meter_readings.power_kw")
    parser.add_argument(
        "--threshold-kw",
        type=float,
        default=20.0,
        help="Only normalize rows above this stored kW threshold (default: 20)",
    )
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help="Comma-separated sources to target (default: iot,thundercloud,accdb)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the update. Without this flag, the script runs in preview mode only.",
    )
    args = parser.parse_args()

    sources = [source.strip().lower() for source in args.sources.split(",") if source.strip()]

    with _connect() as conn:
        preview_rows(conn, args.threshold_kw, sources)
        if not args.apply:
            print("Preview only. Re-run with --apply to update rows.")
            return
        updated = apply_fix(conn, args.threshold_kw, sources)
        print(f"Updated {updated} rows.")


if __name__ == "__main__":
    main()
