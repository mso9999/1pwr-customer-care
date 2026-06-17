#!/usr/bin/env python3
"""
Post-migration schema-invariant check for hourly_consumption (LS + BN).

Catches the *class* of defect that broke production on 2026-06-13: migration 044
partitioned ``hourly_consumption`` but dropped the ``id`` column DEFAULT, so every
insert failed silently for 4 days. A one-line assertion here would have caught it
immediately.

Invariants checked per DB:
  1. hourly_consumption.id has a nextval(...) DEFAULT (its absence is the exact
     044 defect that broke every insert).
  2. The id sequence is ahead of max(id) (else the next insert dup-keys).

Wire this into the deploy/migration pipeline (run after migrations, before
declaring success). Exits non-zero with a clear message if any invariant fails.

Run as the ``postgres`` OS user (local peer auth):
    python3 check_schema_invariants.py
"""
from __future__ import annotations

import sys

import psycopg2

DBS = ["onepower_cc", "onepower_bj"]


def check_db(dbname: str) -> list[str]:
    fails: list[str] = []
    conn = psycopg2.connect(f"dbname={dbname} user=postgres host=/var/run/postgresql")
    try:
        cur = conn.cursor()

        # 1. id must have a DEFAULT (nextval) — its absence broke all inserts.
        cur.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='hourly_consumption' AND column_name='id'"
        )
        row = cur.fetchone()
        default = row[0] if row else None
        if not default or "nextval" not in default:
            fails.append(f"{dbname}: hourly_consumption.id has no nextval DEFAULT (got {default!r})")

        # 2. The id sequence must be ahead of max(id) (else duplicate-key inserts).
        try:
            cur.execute("SELECT last_value FROM hourly_consumption_id_seq")
            seq = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(max(id), 0) FROM hourly_consumption")
            mx = cur.fetchone()[0]
            if seq < mx:
                fails.append(f"{dbname}: id sequence ({seq}) behind max(id) ({mx})")
        except Exception as exc:
            fails.append(f"{dbname}: could not verify id sequence: {exc}")

        cur.close()
    finally:
        conn.close()
    return fails


def main() -> int:
    all_fails: list[str] = []
    for db in DBS:
        try:
            all_fails.extend(check_db(db))
        except Exception as exc:
            all_fails.append(f"{db}: check failed to run: {exc}")

    if all_fails:
        print("SCHEMA INVARIANT CHECK FAILED:")
        for f in all_fails:
            print(f"  ✗ {f}")
        return 1
    print(f"schema invariants OK across {DBS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
