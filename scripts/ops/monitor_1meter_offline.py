#!/usr/bin/env python3
"""
1Meter fleet connectivity monitor (DynamoDB ``meter_last_seen``).

Alerts the WhatsApp Customer Care tracker group when prototype meters are silent
past ``THRESHOLD_HOURS`` and again when they recover. State is persisted to
``STATE_FILE`` so alerts do not repeat on every run. Intended for a systemd timer
or cron every 15 minutes on the CC host.

Env (all optional unless noted):
  THRESHOLD_HOURS         Float. Default ``6``.
  AWS_REGION              Default ``us-east-1`` (DynamoDB region).
  DDB_TABLE               Default ``meter_last_seen``.
  DATABASE_URL            1PDB connection string (read ``meters`` table for role
                          labels). Falls back to ``postgresql://cc_api@localhost:5432/onepower_cc``.
  CC_BRIDGE_NOTIFY_URL    WhatsApp bridge ``/notify`` URL (required to post).
  CC_BRIDGE_SECRET        Bridge shared secret.
  STATE_FILE              Default ``/var/lib/cc-fleet-monitor/state.json``.
  FLEET_SITE              Label used in the WA text. Default ``MAK``.
  DRY_RUN                 ``1`` to log but not POST.

Exit 0 on success (including "no alerts"). Non-zero on fatal errors.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("1meter-monitor")

THRESHOLD_HOURS = float(os.environ.get("THRESHOLD_HOURS", "6"))
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DDB_TABLE = os.environ.get("DDB_TABLE", "meter_last_seen")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://cc_api@localhost:5432/onepower_cc"
)
NOTIFY_URL = os.environ.get("CC_BRIDGE_NOTIFY_URL", "")
NOTIFY_SECRET = os.environ.get("CC_BRIDGE_SECRET", "")
STATE_FILE = Path(os.environ.get("STATE_FILE", "/var/lib/cc-fleet-monitor/state.json"))
FLEET_SITE = os.environ.get("FLEET_SITE", "MAK")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# lastAcceptedTime in ``meter_last_seen`` is SAST (UTC+2) YYYYMMDDHHMM.
_SAST = timezone(timedelta(hours=2))


def _short_id(meter_id_12: str) -> str:
    """``000023022673`` -> ``23022673``."""
    s = str(meter_id_12 or "").strip()
    return s.lstrip("0") or s


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"offline": {}, "version": 1}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        log.warning("Could not parse %s; starting fresh", STATE_FILE)
        return {"offline": {}, "version": 1}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError as exc:
        log.warning("Could not write state file %s: %s", STATE_FILE, exc)


def load_roles(meter_ids: list[str]) -> dict[str, dict]:
    """Return ``{short_meter_id: {account, role, status}}`` from ``meters``."""
    if not meter_ids:
        return {}
    try:
        import psycopg2
    except ImportError:
        log.warning("psycopg2 not available; role metadata will be empty")
        return {}
    out: dict[str, dict] = {}
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT meter_id, account_number, role, status "
                    "FROM meters WHERE meter_id = ANY(%s)",
                    (meter_ids,),
                )
                for mid, acct, role, status in cur.fetchall():
                    out[str(mid).strip()] = {
                        "account": (acct or "").strip(),
                        "role": (role or "").strip(),
                        "status": (status or "").strip(),
                    }
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not query meters table: %s", exc)
    return out


def fetch_meter_last_seen() -> list[dict]:
    """Return ``[{meter_id, last_accepted_utc, hours_ago}]`` from DynamoDB."""
    try:
        import boto3
    except ImportError:
        log.error("boto3 not installed — cannot query DynamoDB")
        sys.exit(2)
    ddb = boto3.client("dynamodb", region_name=AWS_REGION)
    items: list[dict] = []
    scan_kwargs: dict = {"TableName": DDB_TABLE}
    while True:
        resp = ddb.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for it in items:
        raw_id = (it.get("meterId") or {}).get("S", "")
        raw_ts = (it.get("lastAcceptedTime") or {}).get("S", "")
        mid = _short_id(raw_id)
        if not mid:
            continue
        last_utc: datetime | None = None
        hours_ago: float | None = None
        try:
            last_utc = (
                datetime.strptime(raw_ts, "%Y%m%d%H%M").replace(tzinfo=_SAST).astimezone(timezone.utc)
            )
            hours_ago = (now - last_utc).total_seconds() / 3600.0
        except ValueError:
            pass
        rows.append(
            {
                "meter_id": mid,
                "last_accepted_sast": raw_ts,
                "last_accepted_utc": last_utc.isoformat() if last_utc else None,
                "hours_ago": hours_ago,
            }
        )
    return rows


def post_bridge_notify(text: str, payload_extra: dict | None = None) -> bool:
    """POST to the WhatsApp bridge ``/notify``. Returns True on 2xx.

    Best-effort: *any* exception (URL, socket, HTTP, TLS, TimeoutError) is
    swallowed so the caller can continue and persist state. The bridge sends
    the WhatsApp message after this POST, and a timeout on the HTTP return
    does **not** mean the message failed to reach the tracker group.
    """
    if not NOTIFY_URL or not NOTIFY_SECRET:
        log.warning("No CC_BRIDGE_NOTIFY_URL/CC_BRIDGE_SECRET — skipping WA post")
        return False
    payload = {
        "source": "1meter_fleet_monitor",
        "category": "fleet_health",
        "text": text,
    }
    if payload_extra:
        payload.update(payload_extra)
    if DRY_RUN:
        log.info("[DRY_RUN] would POST to %s: %s", NOTIFY_URL, text)
        return True
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        NOTIFY_URL,
        data=data,
        headers={"Content-Type": "application/json", "X-Bridge-Secret": NOTIFY_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 - best-effort notify
        log.warning("bridge notify failed (best-effort, state will still save): %s", exc)
        return False


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return f"{round(hours * 60)}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _label(role_info: dict, mid: str) -> str:
    role = role_info.get("role") or ""
    status = role_info.get("status") or ""
    acct = role_info.get("account") or ""
    if role == "check" and status == "active" and acct:
        return f"{mid} ({acct}, check)"
    if role and acct:
        return f"{mid} ({acct}, {role}/{status})"
    if role:
        return f"{mid} ({role})"
    return mid


def main() -> int:
    rows = fetch_meter_last_seen()
    roles = load_roles([r["meter_id"] for r in rows])
    state = load_state()
    prev_offline: dict = state.get("offline", {})

    offline_now: dict[str, dict] = {}
    for r in rows:
        age = r["hours_ago"]
        if age is None or age >= THRESHOLD_HOURS:
            offline_now[r["meter_id"]] = r

    new_offline = [mid for mid in offline_now if mid not in prev_offline]
    recovered = [mid for mid in prev_offline if mid not in offline_now]

    log.info(
        "fleet=%d online=%d offline=%d new=%d recovered=%d threshold=%.1fh",
        len(rows),
        len(rows) - len(offline_now),
        len(offline_now),
        len(new_offline),
        len(recovered),
        THRESHOLD_HOURS,
    )

    alerts: list[str] = []
    if new_offline or (offline_now and not prev_offline):
        lines = [
            f"⚠️ 1Meter fleet alert ({FLEET_SITE}): "
            f"{len(offline_now)}/{len(rows)} offline (>{THRESHOLD_HOURS:.0f}h)"
        ]
        for mid in sorted(offline_now):
            r = offline_now[mid]
            lines.append(f"• {_label(roles.get(mid, {}), mid)} — last: {_fmt_age(r['hours_ago'])} ago")
        alerts.append("\n".join(lines))
    if recovered:
        lines = [f"✅ 1Meter recovered ({FLEET_SITE}):"]
        for mid in sorted(recovered):
            lines.append(f"• {_label(roles.get(mid, {}), mid)} back online")
        alerts.append("\n".join(lines))

    # Persist the observed state FIRST so transient notify failures don't cause
    # the same alert to re-fire on the next tick. The observation (from
    # DynamoDB) is authoritative; the WhatsApp post is best-effort.
    if not DRY_RUN:
        state["offline"] = {
            mid: {
                "first_seen_offline_utc": prev_offline.get(mid, {}).get(
                    "first_seen_offline_utc",
                    datetime.now(timezone.utc).isoformat(),
                ),
                "last_accepted_utc": offline_now[mid]["last_accepted_utc"],
                "hours_ago": offline_now[mid]["hours_ago"],
            }
            for mid in offline_now
        }
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
    else:
        log.info("DRY_RUN: not persisting state.")

    for text in alerts:
        log.info("alert:\n%s", text)
        post_bridge_notify(
            text,
            {
                "site": FLEET_SITE,
                "threshold_hours": THRESHOLD_HOURS,
                "offline_count": len(offline_now),
                "fleet_size": len(rows),
            },
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
