#!/usr/bin/env python3
"""Recompute per-account blended consumption rate (kWh/hour) for balance freshness.

Writes ``balance_refresh_state.avg_kwh_per_hour`` from ``hourly_consumption`` so the
tiered scheduler (``balance_refresh_scheduler.py``) can estimate time-to-depletion.

Rate blend (accounts for the ~1-day import lag; the rate changes slowly):
    rate = w * (kWh_recent / recent_hours) + (1 - w) * (kWh_window / window_hours)
with ``w`` / windows from ``system_config`` (``balance_refresh_rate_*``).

Also pulls the freshest balance from ``account_balance_live`` into
``balance_refresh_state.last_balance_kwh`` so the scheduler has a balance to project
forward without an extra SparkMeter call.

Runs per database (LS ``DATABASE_URL`` + BN ``DATABASE_URL_BN``), mirroring
``run_sm_credit_mirror_incremental.py``. No SparkMeter calls — DB only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


def _parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _read_float(conn, key: str, fallback: float) -> float:
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
    row = cur.fetchone()
    if not row or row[0] is None or str(row[0]).strip() == "":
        return fallback
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return fallback


def recompute_for_db(db_url: str, label: str, *, dry_run: bool) -> int:
    conn = psycopg2.connect(db_url)
    try:
        w_recent = min(1.0, max(0.0, _read_float(conn, "balance_refresh_rate_w_recent", 0.6)))
        recent_h = max(1.0, _read_float(conn, "balance_refresh_rate_recent_hours", 48))
        window_h = max(recent_h, _read_float(conn, "balance_refresh_rate_window_hours", 168))

        cur = conn.cursor()
        cur.execute(
            """
            WITH dedup AS (
                SELECT account_number, reading_hour, MAX(kwh) AS kwh
                FROM hourly_consumption
                WHERE reading_hour >= NOW() - (%s || ' hours')::interval
                GROUP BY account_number, reading_hour
            )
            SELECT account_number,
                   COALESCE(SUM(GREATEST(kwh, 0)) FILTER (
                       WHERE reading_hour >= NOW() - (%s || ' hours')::interval
                   ), 0) AS kwh_recent,
                   COALESCE(SUM(GREATEST(kwh, 0)), 0) AS kwh_window
            FROM dedup
            GROUP BY account_number
            """,
            (window_h, recent_h),
        )
        rows = cur.fetchall()

        records: list[tuple[str, float]] = []
        for account_number, kwh_recent, kwh_window in rows:
            rate = (
                w_recent * (float(kwh_recent) / recent_h)
                + (1.0 - w_recent) * (float(kwh_window) / window_h)
            )
            records.append((str(account_number).strip(), round(max(0.0, rate), 6)))

        active = sum(1 for _, r in records if r > 0)
        print(
            f"[{label}] accounts={len(records)} consuming={active} "
            f"w_recent={w_recent} recent_h={recent_h:.0f} window_h={window_h:.0f} dry_run={dry_run}"
        )

        if dry_run or not records:
            return 0

        execute_values(
            cur,
            """
            INSERT INTO balance_refresh_state (account_number, avg_kwh_per_hour, updated_at)
            VALUES %s
            ON CONFLICT (account_number) DO UPDATE
                SET avg_kwh_per_hour = EXCLUDED.avg_kwh_per_hour,
                    updated_at = NOW()
            """,
            records,
            template="(%s, %s, NOW())",
            page_size=1000,
        )

        # Pull the freshest live-cache balance forward so the scheduler can project it.
        cur.execute(
            """
            UPDATE balance_refresh_state s
               SET last_balance_kwh = l.live_balance_kwh,
                   last_balance_at  = l.as_of,
                   updated_at       = NOW()
              FROM account_balance_live l
             WHERE l.account_number = s.account_number
               AND l.as_of IS NOT NULL
               AND l.live_balance_kwh IS NOT NULL
               AND (s.last_balance_at IS NULL OR l.as_of > s.last_balance_at)
            """
        )
        conn.commit()
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default="/opt/1pdb/.env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--country",
        choices=["LS", "BN"],
        help="Restrict to a single country DB (default: all configured).",
    )
    args = ap.parse_args()

    vals = _parse_env_file(args.env_file)
    jobs: list[tuple[str, str]] = []
    if args.country in (None, "LS") and vals.get("DATABASE_URL"):
        jobs.append(("LS", vals["DATABASE_URL"]))
    if args.country in (None, "BN") and vals.get("DATABASE_URL_BN"):
        jobs.append(("BN", vals["DATABASE_URL_BN"]))
    if not jobs:
        raise SystemExit("No database URLs found in env file")

    failures = 0
    for label, db_url in jobs:
        try:
            recompute_for_db(db_url, label, dry_run=args.dry_run)
        except Exception as e:  # one country failing must not block the other
            failures += 1
            print(f"[{label}] FAILED: {e}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
