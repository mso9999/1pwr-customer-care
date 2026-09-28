#!/usr/bin/env python3
"""Fail when Benin or Zambia is missing a public table or column Lesotho has.

Historical Lesotho-only objects are listed in schema_drift_allowlist.txt.
New drift is a deploy failure. Columns of an allowlisted missing table are
not required.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ALLOWLIST = Path(__file__).with_name("schema_drift_allowlist.txt")
BASE = "onepower_cc"
COUNTRIES = ("onepower_bj", "onepower_zm")

SQL_TABLES = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
)
SQL_COLUMNS = (
    "SELECT table_name || '.' || column_name FROM information_schema.columns "
    "WHERE table_schema = 'public'"
)


def load_allowlist(path: Path = ALLOWLIST) -> set[str]:
    allowed: set[str] = set()
    for line in path.read_text().splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            allowed.add(text)
    return allowed


def unexpected(keys: list[str], allow: set[str]) -> list[str]:
    return sorted(key for key in keys if key not in allow)


def _psql(db: str, sql: str) -> set[str]:
    out = subprocess.check_output(
        ["sudo", "-u", "postgres", "psql", "-d", db, "-Atc", sql],
        text=True,
    )
    return {line for line in out.splitlines() if line}


def missing_keys(base_tables: set[str], base_columns: set[str],
                 country: str, tables: set[str], columns: set[str]) -> list[str]:
    missing_tables = base_tables - tables
    keys = [f"{country} table:{name}" for name in sorted(missing_tables)]
    for column in sorted(base_columns - columns):
        table = column.split(".", 1)[0]
        if table in missing_tables:
            continue
        keys.append(f"{country} column:{column}")
    return keys


def main() -> int:
    allow = load_allowlist()
    base_tables = _psql(BASE, SQL_TABLES)
    base_columns = _psql(BASE, SQL_COLUMNS)
    failed = False
    for country in COUNTRIES:
        exists = subprocess.check_output(
            ["sudo", "-u", "postgres", "psql", "-d", "postgres", "-Atc",
             f"SELECT 1 FROM pg_database WHERE datname = '{country}'"],
            text=True,
        ).strip()
        if exists != "1":
            print(f"Skipping {country} (database not present)")
            continue
        keys = missing_keys(base_tables, base_columns, country, _psql(country, SQL_TABLES), _psql(country, SQL_COLUMNS))
        new = unexpected(keys, allow)
        if new:
            failed = True
            print(f"{country} is missing {len(new)} Lesotho object(s) not on the allowlist:")
            for item in new:
                print(f"  {item}")
        else:
            print(f"{country} schema drift: none beyond the allowlist ({len(keys)} historical)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
