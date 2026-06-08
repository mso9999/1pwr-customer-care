#!/usr/bin/env python3
"""Tiered near-depletion balance refresh scheduler (Part 5 of proactive freshness).

Every run (driven by ``cc-balance-refresh.timer``, ~5 min) this:
  1. Re-assesses each consuming account's predicted time-to-depletion by projecting
     the last known balance forward at its ``avg_kwh_per_hour`` rate, and assigns a
     cadence tier (no SparkMeter calls — DB only).
  2. Pulls the live SparkMeter balance for accounts whose tier says they're due,
     most-urgent-first, bounded by a per-run cap and a shared daily Koios budget.

Tiers (hours-to-depletion -> pull cadence), configurable via ``system_config``:
    > 24h            -> tier 0: no scheduled pull (activity + daily batch only)
    12 < h <= 24     -> tier 1: every 2h
     6 < h <= 12     -> tier 2: every 1h
     1 < h <=  6     -> tier 3: every 15 min
         h <=  1     -> tier 4: every 5 min
    depleted (<=0) or idle (rate ~ 0) -> off the urgent list (top-up is activity-triggered)

Runs per database (LS ``DATABASE_URL`` + BN ``DATABASE_URL_BN``). The live pull routes
by site to Koios (LS/BN) or ThunderCloud (MAK), reusing ``balance_live`` /
``sparkmeter_credit`` with the script's own connection (no API pool usage).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

# Allow `import balance_live` whether run from the repo or /opt/cc-portal/backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "acdb-api"))
sys.path.insert(0, "/opt/cc-portal/backend")


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


def _read_str(conn, key: str, fallback: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
    row = cur.fetchone()
    if not row or row[0] is None or str(row[0]).strip() == "":
        return fallback
    return str(row[0]).strip()


def _read_int(conn, key: str, fallback: int) -> int:
    try:
        return int(float(_read_str(conn, key, str(fallback))))
    except (TypeError, ValueError):
        return fallback


def _read_csv_floats(conn, key: str, fallback: list[float]) -> list[float]:
    raw = _read_str(conn, key, "")
    if not raw:
        return fallback
    try:
        return [float(x) for x in raw.split(",") if x.strip() != ""]
    except (TypeError, ValueError):
        return fallback


def _tier_for(hours_left: float | None, est_balance: float, boundaries: list[float]) -> int:
    """boundaries = [t0, t1, t2, t3] descending (e.g. 24,12,6,1)."""
    if est_balance <= 0 or hours_left is None:
        return 0
    t0, t1, t2, t3 = (boundaries + [24, 12, 6, 1])[:4]
    if hours_left > t0:
        return 0
    if hours_left > t1:
        return 1
    if hours_left > t2:
        return 2
    if hours_left > t3:
        return 3
    return 4


def _cadence_min(tier: int, cadences: list[float]) -> float | None:
    if tier <= 0:
        return None
    c = (cadences + [120, 60, 15, 5])[:4]
    return c[tier - 1]


def tick_for_db(db_url: str, label: str, *, dry_run: bool) -> int:
    import balance_live

    conn = psycopg2.connect(db_url)
    try:
        boundaries = _read_csv_floats(conn, "balance_refresh_tier_hours", [24, 12, 6, 1])
        cadences = _read_csv_floats(conn, "balance_refresh_tier_cadence_min", [120, 60, 15, 5])
        max_per_run = max(0, _read_int(conn, "balance_refresh_max_per_run", 400))
        daily_budget = max(0, _read_int(conn, "balance_refresh_daily_budget", 8000))
        bootstrap_per_run = max(0, _read_int(conn, "balance_refresh_bootstrap_per_run", 100))
        t0, t1, t2, t3 = (boundaries + [24, 12, 6, 1])[:4]
        c1, c2, c3, c4 = (cadences + [120, 60, 15, 5])[:4]

        cur = conn.cursor()

        # --- Phase 1: project balances forward & (re)assign tiers (DB only) ---
        if not dry_run:
            cur.execute(
                """
                WITH proj AS (
                    SELECT account_number,
                           last_balance_kwh
                             - avg_kwh_per_hour
                               * (EXTRACT(EPOCH FROM (NOW() - last_balance_at)) / 3600.0) AS est_balance
                    FROM balance_refresh_state
                    WHERE avg_kwh_per_hour > 0 AND last_balance_at IS NOT NULL
                ),
                tiered AS (
                    SELECT p.account_number,
                           p.est_balance,
                           CASE WHEN s.avg_kwh_per_hour > 0
                                THEN GREATEST(p.est_balance, 0) / s.avg_kwh_per_hour END AS hours_left
                    FROM proj p
                    JOIN balance_refresh_state s ON s.account_number = p.account_number
                )
                UPDATE balance_refresh_state s
                   SET hours_to_depletion = t.hours_left,
                       tier = CASE
                                WHEN t.est_balance <= 0 OR t.hours_left IS NULL THEN 0
                                WHEN t.hours_left > %s THEN 0
                                WHEN t.hours_left > %s THEN 1
                                WHEN t.hours_left > %s THEN 2
                                WHEN t.hours_left > %s THEN 3
                                ELSE 4
                              END,
                       next_due_at = CASE
                                WHEN t.est_balance <= 0 OR t.hours_left IS NULL OR t.hours_left > %s THEN NULL
                                ELSE COALESCE(s.last_pull_at, s.last_balance_at)
                                     + ((CASE
                                            WHEN t.hours_left > %s THEN %s
                                            WHEN t.hours_left > %s THEN %s
                                            WHEN t.hours_left > %s THEN %s
                                            ELSE %s
                                         END) || ' minutes')::interval
                              END,
                       updated_at = NOW()
                  FROM tiered t
                 WHERE s.account_number = t.account_number
                """,
                (t0, t1, t2, t3, t0, t1, c1, t2, c2, t3, c3, c4),
            )
            # Idle/zero-rate accounts: never on the urgent list.
            cur.execute(
                """
                UPDATE balance_refresh_state
                   SET tier = 0, next_due_at = NULL, updated_at = NOW()
                 WHERE avg_kwh_per_hour <= 0 AND (tier <> 0 OR next_due_at IS NOT NULL)
                """
            )
            conn.commit()

        # --- Budget: count today's live pulls (shared with activity-triggered + imports) ---
        cur.execute(
            """
            SELECT COUNT(*) FROM account_balance_live
            WHERE source IN ('koios', 'thundercloud') AND as_of >= date_trunc('day', NOW())
            """
        )
        pulled_today = int(cur.fetchone()[0])
        remaining_daily = max(0, daily_budget - pulled_today)
        run_budget = min(max_per_run, remaining_daily)

        # --- Phase 2: select due accounts (most urgent first) within budget ---
        cur.execute(
            """
            SELECT account_number FROM balance_refresh_state
            WHERE tier >= 1 AND next_due_at IS NOT NULL AND next_due_at <= NOW()
            ORDER BY hours_to_depletion ASC NULLS LAST, next_due_at ASC
            LIMIT %s
            """,
            (run_budget,),
        )
        due = [str(r[0]).strip() for r in cur.fetchall()]

        # Bootstrap: consuming accounts we have never pulled (no balance to project yet).
        boot_limit = min(bootstrap_per_run, max(0, run_budget - len(due)))
        boot: list[str] = []
        if boot_limit > 0:
            cur.execute(
                """
                SELECT account_number FROM balance_refresh_state
                WHERE avg_kwh_per_hour > 0 AND last_balance_at IS NULL
                ORDER BY avg_kwh_per_hour DESC
                LIMIT %s
                """,
                (boot_limit,),
            )
            boot = [str(r[0]).strip() for r in cur.fetchall()]

        targets = due + boot
        print(
            f"[{label}] pulled_today={pulled_today} remaining_daily={remaining_daily} "
            f"run_budget={run_budget} due={len(due)} bootstrap={len(boot)} dry_run={dry_run}"
        )

        if dry_run or not targets:
            return 0

        pulled = 0
        for account in targets:
            try:
                rec = balance_live.refresh_balance_live(
                    conn, account, force=True, write_conn=conn
                )
            except Exception as e:
                print(f"[{label}] refresh failed for {account}: {e}")
                conn.rollback()
                continue

            bal = rec.get("live_balance_kwh")
            cur.execute(
                "SELECT avg_kwh_per_hour FROM balance_refresh_state WHERE account_number = %s",
                (account,),
            )
            row = cur.fetchone()
            rate = float(row[0]) if row and row[0] is not None else 0.0
            est_balance = float(bal) if bal is not None else 0.0
            hours_left = (max(est_balance, 0.0) / rate) if rate > 0 else None
            tier = _tier_for(hours_left, est_balance, boundaries)
            cadence = _cadence_min(tier, cadences)

            cur.execute(
                """
                UPDATE balance_refresh_state
                   SET last_balance_kwh = %s,
                       last_balance_at = NOW(),
                       last_pull_at = NOW(),
                       hours_to_depletion = %s,
                       tier = %s,
                       next_due_at = CASE WHEN %s IS NULL THEN NULL
                                         ELSE NOW() + (%s || ' minutes')::interval END,
                       updated_at = NOW()
                 WHERE account_number = %s
                """,
                (bal, hours_left, tier, cadence, cadence, account),
            )
            conn.commit()
            pulled += 1

        print(f"[{label}] pulls_completed={pulled}")
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default="/opt/1pdb/.env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--country", choices=["LS", "BN"], help="Restrict to one country DB.")
    args = ap.parse_args()

    vals = _parse_env_file(args.env_file)
    # Make per-country Koios/TC credentials available to sparkmeter_credit.
    os.environ.update(vals)

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
            tick_for_db(db_url, label, dry_run=args.dry_run)
        except Exception as e:
            failures += 1
            print(f"[{label}] FAILED: {e}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
