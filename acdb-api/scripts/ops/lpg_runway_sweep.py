#!/usr/bin/env python3
"""
Daily LPG runway sweep.

Deployed with the CC backend to:
  /opt/cc-portal/backend/scripts/ops/lpg_runway_sweep.py

The synchronous low-runway / critical alerts in the LPG module only re-evaluate
when a generator run is STOPPED. A site that simply sits low (few cylinders, no
new runs logged) would never re-trigger. This sweep closes that gap: once a day
it recomputes each tracked site's projected runway (days of LPG left at the
trailing-30d burn rate) and fires the same alerts, deduped per active-stock
period via lpg_batches.{low_runway_alert_sent_at, critical_alert_sent_at}.

LPG data is consolidated in onepower_cc (the CC API always writes via /api), so
one run against DATABASE_URL covers every country's sites; alerts are routed to
each site's country bridge.

Usage:
  python3 lpg_runway_sweep.py            # send alerts + mark sent
  python3 lpg_runway_sweep.py --dry-run  # report only, no send, no mark
"""
from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extras

# Default low-runway warn threshold (must match lpg.store.LOW_RUNWAY_WARN_DAYS).
WARN_DAYS_DEFAULT = 7
BURN_RATE_WINDOW_DAYS = 30
CRITICAL_REMAINING_THRESHOLD = 1

DRY_RUN = "--dry-run" in sys.argv

# Make the backend root importable so we can reuse the bridge + country helpers.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


def _load_env_file(path: str) -> None:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


_env_path = os.environ.get("OP_ENV_FILE") or os.environ.get("ONEPWR_ENV_FILE")
if not _env_path and os.path.isfile("/opt/1pdb/.env"):
    _env_path = "/opt/1pdb/.env"
if _env_path:
    _load_env_file(_env_path)


def _country_for_site(code: str):
    try:
        from country_config import get_country_for_site
        return get_country_for_site(code)
    except Exception:
        return None


def _send_alert(code: str, text: str, kind: str) -> bool:
    if DRY_RUN:
        print(f"  [dry-run] would alert {code} ({kind}): {text}")
        return True
    try:
        from cc_bridge_notify import notify_cc_bridge
        notify_cc_bridge(
            {"source": "lpg", "kind": kind, "site_code": code.upper(), "text": text},
            country_code=_country_for_site(code),
        )
        return True
    except Exception as exc:  # pragma: no cover — best effort
        print(f"  WARN: bridge notify failed for {code}: {exc}", file=sys.stderr)
        return False


SWEEP_SQL = """
WITH bal AS (
    SELECT site_code,
           COALESCE(SUM(cylinders_remaining) FILTER (WHERE status <> 'archived'), 0) AS remaining
    FROM lpg_batches
    GROUP BY site_code
),
cons AS (
    SELECT site_code, COALESCE(SUM(cylinders_consumed), 0) AS cyl_window
    FROM lpg_generator_runs
    WHERE started_at >= NOW() - (%s || ' days')::interval
    GROUP BY site_code
),
nb AS (
    SELECT DISTINCT ON (site_code)
        site_code, id AS batch_id, critical_alert_sent_at, low_runway_alert_sent_at
    FROM lpg_batches
    WHERE status = 'active'
    ORDER BY site_code, arrived_at DESC, id DESC
)
SELECT
    s.code,
    s.lpg_low_runway_warn_days,
    bal.remaining,
    COALESCE(cons.cyl_window, 0) AS cyl_window,
    nb.batch_id,
    nb.critical_alert_sent_at,
    nb.low_runway_alert_sent_at
FROM sites s
JOIN bal  ON bal.site_code  = s.code
LEFT JOIN cons ON cons.site_code = s.code
LEFT JOIN nb   ON nb.site_code   = s.code
ORDER BY s.code
"""


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (need /opt/1pdb/.env).", file=sys.stderr)
        return 2

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    critical_fired = 0
    low_fired = 0
    scanned = 0

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SWEEP_SQL, (BURN_RATE_WINDOW_DAYS,))
            rows = cur.fetchall()

        for r in rows:
            scanned += 1
            code = r["code"]
            remaining = int(r["remaining"] or 0)
            cyl_window = float(r["cyl_window"] or 0)
            per_day = cyl_window / float(BURN_RATE_WINDOW_DAYS)
            days_remaining = (remaining / per_day) if per_day > 0 else None
            warn_days = int(r["lpg_low_runway_warn_days"] or WARN_DAYS_DEFAULT)
            batch_id = r["batch_id"]

            is_critical = remaining <= CRITICAL_REMAINING_THRESHOLD

            # Critical (last cylinder) — only if there is still an active batch to
            # dedupe against and we haven't already alerted for it.
            if is_critical and batch_id is not None and r["critical_alert_sent_at"] is None:
                text = (
                    f"\u26a0\ufe0f LPG CRITICAL — {code.upper()} is down to its last cylinder "
                    f"({remaining} remaining). Schedule an LPG delivery to avoid a generator outage."
                )
                if _send_alert(code, text, "lpg_critical"):
                    critical_fired += 1
                    if not DRY_RUN:
                        with conn.cursor() as ucur:
                            ucur.execute(
                                "UPDATE lpg_batches SET critical_alert_sent_at = NOW() "
                                "WHERE id = %s AND critical_alert_sent_at IS NULL",
                                (batch_id,),
                            )
                continue

            # Low runway — projected days left below the (per-site) threshold, not
            # already critical, deduped per active-stock period.
            if (
                not is_critical
                and remaining > CRITICAL_REMAINING_THRESHOLD
                and days_remaining is not None
                and days_remaining < warn_days
                and batch_id is not None
                and r["low_runway_alert_sent_at"] is None
            ):
                days_disp = round(days_remaining, 1)
                text = (
                    f"\u26a0\ufe0f LPG LOW — {code.upper()} has about {days_disp} day(s) of LPG left at "
                    f"the current burn rate ({remaining} cylinders in stock). Plan a delivery."
                )
                if _send_alert(code, text, "lpg_low_runway"):
                    low_fired += 1
                    if not DRY_RUN:
                        with conn.cursor() as ucur:
                            ucur.execute(
                                "UPDATE lpg_batches SET low_runway_alert_sent_at = NOW() "
                                "WHERE id = %s AND low_runway_alert_sent_at IS NULL",
                                (batch_id,),
                            )

        if not DRY_RUN:
            conn.commit()
    finally:
        conn.close()

    print(
        f"LPG runway sweep: scanned={scanned} critical_alerts={critical_fired} "
        f"low_runway_alerts={low_fired}{' (dry-run)' if DRY_RUN else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
