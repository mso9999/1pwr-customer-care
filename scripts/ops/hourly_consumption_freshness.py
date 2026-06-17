#!/usr/bin/env python3
"""
Freshness monitor for hourly_consumption (LS + BN).

Why this exists
---------------
On 2026-06-13 migration 044 dropped the ``id`` DEFAULT on the partitioned
``hourly_consumption`` table, so EVERY koios insert failed silently. Data simply
stopped at 2026-06-12 23:00 in both DBs and nobody noticed for 4 days. Nothing
alarmed. This monitor closes that gap.

It runs hourly, DB-only (no API calls), and is *self-calibrating*: it looks only
at sites with real recent activity (``MIN_ACTIVE_ROWS_30D`` rows in the last 30
days), so dormant/decommissioned/stray communities (e.g. old BN rows in the LS
DB, tiny low-traffic sites) never produce noise. It alarms when:

  * GLOBAL STALL — even the freshest active site's newest ``reading_hour`` is
    older than ``GLOBAL_STALL_DAYS``. koios lags ~1.5d normally, so a fleet-wide
    stall (broken importer/migration, Koios down) shows up within a day. This is
    exactly the signal the 044 outage would have tripped.
  * SITE LAG — a single active site's ``reading_hour`` is older than
    ``SITE_LAG_DAYS`` while the fleet is otherwise current (a per-site feed gap).

Queries use the (community, reading_hour) index with a 120-day window so they
hit only the current partition and stay cheap on the ~600M-row table.

Alerts go to the CC WhatsApp bridge /broadcast route, with a state file to
debounce repeats (mirrors disk_monitor.py). Runs as ``cc_api`` and reads each
DB's DATABASE_URL from its own env file.

Usage (systemd oneshot, hourly timer):
    CC_BRIDGE_NOTIFY_URL=... CC_BRIDGE_SECRET=... \
      python3 hourly_consumption_freshness.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cc-hourly-freshness")

# ── config ──────────────────────────────────────────────────────────────────
# Each DB is reached via the DATABASE_URL in its own env file (cc_api can read
# both), so this runs as cc_api like the other ops monitors — no postgres peer.
DB_ENVS = [
    ("onepower_cc", os.environ.get("CC_ENV_FILE", "/opt/1pdb/.env")),
    ("onepower_bj", os.environ.get("BN_ENV_FILE", "/opt/1pdb-bn/.env")),
]
# A site counts as "active" only if it has at least this many koios rows in the
# trailing 30 days; below it we treat the site as dormant/sparse and ignore it
# (avoids alarming on decommissioned sites or stray cross-DB communities).
MIN_ACTIVE_ROWS_30D = int(os.environ.get("MIN_ACTIVE_ROWS_30D", "5000"))
# Fleet-wide stall: even the most up-to-date active site is older than this.
GLOBAL_STALL_DAYS = float(os.environ.get("GLOBAL_STALL_DAYS", "2.5"))
# Single active site lagging while the fleet is fresh.
SITE_LAG_DAYS = float(os.environ.get("SITE_LAG_DAYS", "4"))
# Implausibly large single-hour kWh for one meter -> a garbage feed reading that
# slipped past the importer cap (defense in depth; a real meter-hour is < ~30).
OUTLIER_KWH = float(os.environ.get("OUTLIER_KWH", "200"))
STATE_FILE = Path(os.environ.get(
    "HOURLY_FRESH_STATE_FILE", "/var/run/cc-hourly-freshness.state"))
RESEND_AFTER_S = float(os.environ.get("RESEND_AFTER_S", str(6 * 3600)))

BRIDGE_URL = os.environ.get("CC_BRIDGE_NOTIFY_URL", "")
BRIDGE_SECRET = os.environ.get("CC_BRIDGE_SECRET", "")


# ── bridge send (mirrors disk_monitor.py) ───────────────────────────────────
def _send_whatsapp(text: str) -> bool:
    if not BRIDGE_URL or not BRIDGE_SECRET:
        log.warning("bridge not configured — alert not sent:\n%s", text)
        return False
    url = BRIDGE_URL
    for suffix in ("/notify/", "/notify"):
        if url.endswith(suffix):
            url = url[: -len(suffix)] + "/broadcast"
            break
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "X-Bridge-Secret": BRIDGE_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = 200 <= resp.status < 300
            log.info("bridge_broadcast status=%s ok=%s", resp.status, ok)
            return ok
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("bridge_broadcast failed: %s", exc)
        return False


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception as exc:
        log.warning("could not write state file: %s", exc)


# ── checks ──────────────────────────────────────────────────────────────────
def _dsn_from_env(env_file: str) -> str:
    for ln in open(env_file):
        ln = ln.strip()
        if ln.startswith("DATABASE_URL="):
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"DATABASE_URL not found in {env_file}")


def check_db(dbname: str, env_file: str) -> list[str]:
    """Return a list of problem strings for *dbname* (empty == healthy)."""
    problems: list[str] = []
    conn = psycopg2.connect(_dsn_from_env(env_file))
    try:
        cur = conn.cursor()
        # Indexed (community, reading_hour) + 120-day window -> current partition only.
        cur.execute(
            """
            SELECT community,
                   max(reading_hour) AS latest,
                   count(*) FILTER (WHERE reading_hour > now() - interval '30 days') AS rows30d,
                   max(kwh) AS max_kwh
            FROM hourly_consumption
            WHERE source = 'koios'
              AND reading_hour > now() - interval '120 days'
              AND community IS NOT NULL AND community <> ''
            GROUP BY community
            """
        )
        rows = cur.fetchall()
        cur.close()

        now = datetime.now(timezone.utc)
        active = {
            comm: (now - latest).total_seconds() / 86400.0
            for comm, latest, rows30d, _max_kwh in rows
            if rows30d >= MIN_ACTIVE_ROWS_30D
        }

        # Outlier guard (defense in depth vs the importer cap): any active site
        # with an implausibly large single meter-hour kWh.
        for comm, _latest, rows30d, max_kwh in rows:
            if rows30d >= MIN_ACTIVE_ROWS_30D and max_kwh is not None and max_kwh > OUTLIER_KWH:
                problems.append(
                    f"{dbname}/{comm}: implausible koios reading {max_kwh:.0f} kWh "
                    f"in one meter-hour (> {OUTLIER_KWH:.0f}) — likely garbage feed value"
                )
        if not active:
            problems.append(
                f"{dbname}: NO active koios sites in last 30d "
                f"(all imports may be down)"
            )
            return problems

        freshest = min(active.values())
        if freshest > GLOBAL_STALL_DAYS:
            # Fleet-wide stall — the 044 outage class. Report the whole fleet.
            worst = max(active.values())
            problems.append(
                f"{dbname}: koios feed STALLED fleet-wide — even the freshest of "
                f"{len(active)} active sites is {freshest:.1f}d old "
                f"(worst {worst:.1f}d); imports likely broken"
            )
        else:
            # Fleet is fresh; flag any individual lagging site.
            for comm, age in sorted(active.items()):
                if age > SITE_LAG_DAYS:
                    problems.append(
                        f"{dbname}/{comm}: koios reading_hour {age:.1f}d old "
                        f"(fleet freshest {freshest:.1f}d) — site feed gap"
                    )
    finally:
        conn.close()
    return problems


def main() -> int:
    all_problems: list[str] = []
    for db, env_file in DB_ENVS:
        try:
            all_problems.extend(check_db(db, env_file))
        except Exception as exc:
            all_problems.append(f"{db}: monitor query failed: {exc}")

    if not all_problems:
        log.info("hourly_consumption healthy across %s", [d for d, _ in DB_ENVS])
        STATE_FILE.unlink(missing_ok=True)
        return 0

    signature = "|".join(sorted(all_problems))
    state = _read_state()
    now = time.time()
    if state.get("sig") == signature and now - float(state.get("ts", 0)) < RESEND_AFTER_S:
        log.info("same problem set already alerted — skipping resend")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"🚨 *CC Consumption Feed Alert* [{ts}]\n"
        + "\n".join(f"• {p}" for p in all_problems)
        + "\n\nLikely causes: broken importer/migration, Koios outage, or feed gap."
    )
    log.warning("problems detected: %s", all_problems)
    _send_whatsapp(msg)
    _write_state({"sig": signature, "ts": now})
    return 0


if __name__ == "__main__":
    sys.exit(main())
