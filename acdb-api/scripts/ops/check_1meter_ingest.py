#!/usr/bin/env python3
"""Alert when a commissioned 1Meter is publishing but CC is not taking its readings.

Compares ``meter_last_seen`` (DynamoDB, written on every gateway reading) with
``prototype_meter_state.last_synced_at`` (written only when CC accepts one).
Catches any cause: a 409 at ingest, Lambda routing, missing schema. Lesotho's
DynamoDB sync can mask ingest refusals, so each lane is checked on its own.

The lane comes from ``COUNTRY_CODE``/``DATABASE_URL`` in the EnvironmentFile.
WhatsApp goes to that country's bridge only; without one the run logs and
exits 1. Each meter alerts at most once per ``REALERT_HOURS``.
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("cc-1meter-ingest-check")

PUBLISHED_WITHIN = timedelta(minutes=int(os.environ.get("PUBLISHED_WITHIN_MIN", "60")))
ACCEPT_LAG = timedelta(minutes=int(os.environ.get("ACCEPT_LAG_MIN", "45")))
REALERT = timedelta(hours=int(os.environ.get("REALERT_HOURS", "24")))
DDB_TABLE = os.environ.get("DDB_TABLE", "meter_last_seen")


def _parse_seen(item: dict) -> datetime | None:
    for key in ("liveSeen", "last_seen"):
        raw = (item.get(key) or {}).get("S")
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def commissioned_meters(conn) -> list[tuple[str, str, datetime | None]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.meter_id, m.account_number, pms.last_synced_at
          FROM meters m
          JOIN accounts a ON UPPER(a.account_number) = UPPER(m.account_number)
          JOIN customers c ON c.id = a.customer_id AND c.customer_commissioned
          LEFT JOIN prototype_meter_state pms
            ON regexp_replace(pms.meter_id, '^0+', '') = regexp_replace(m.meter_id, '^0+', '')
         WHERE m.platform = 'prototype' AND m.status = 'active'
        """
    )
    return [(str(r[0]), str(r[1]), r[2]) for r in cur.fetchall()]


def last_published(meter_ids: list[str]) -> dict[str, datetime]:
    ddb = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    seen: dict[str, datetime] = {}
    for meter_id in meter_ids:
        key = meter_id.lstrip("0").zfill(12)
        item = ddb.get_item(TableName=DDB_TABLE, Key={"meterId": {"S": key}}).get("Item") or {}
        ts = _parse_seen(item)
        if ts:
            seen[meter_id] = ts
    return seen


def find_stuck(now: datetime, meters, published) -> list[dict]:
    stuck = []
    for meter_id, account, synced in meters:
        pub = published.get(meter_id)
        if not pub or now - pub > PUBLISHED_WITHIN:
            continue
        if synced is None or pub - synced > ACCEPT_LAG:
            stuck.append({
                "meter_id": meter_id,
                "account": account,
                "published": pub.isoformat(),
                "accepted": synced.isoformat() if synced else None,
            })
    return stuck


def main() -> int:
    from country_config import COUNTRY
    from cc_bridge_notify import bridge_credentials, notify_cc_bridge

    code = COUNTRY.code
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        meters = commissioned_meters(conn)
    finally:
        conn.close()
    now = datetime.now(timezone.utc)
    stuck = find_stuck(now, meters, last_published([m[0] for m in meters])) if meters else []
    log.info("%s: commissioned 1Meters=%d publishing-but-not-accepted=%d", code, len(meters), len(stuck))
    if not stuck:
        return 0

    for s in stuck:
        log.warning(
            "%s: %s (%s) published %s, CC last accepted %s",
            code, s["meter_id"], s["account"], s["published"], s["accepted"] or "never",
        )

    state_path = Path(os.environ.get("STATE_FILE", f"/var/tmp/cc-1meter-ingest-{code}.json"))
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    due = [
        s for s in stuck
        if not state.get(s["meter_id"])
        or now - datetime.fromisoformat(state[s["meter_id"]]) > REALERT
    ]
    if due and all(bridge_credentials(code)):
        lines = [
            f"- {s['account']} meter {s['meter_id']}: publishing, CC last accepted "
            f"{s['accepted'][:16].replace('T', ' ') + ' UTC' if s['accepted'] else 'never'}"
            for s in due
        ]
        notify_cc_bridge(
            {
                "source": "1meter_ingest_check",
                "category": "fleet_health",
                "text": f"[{code}] 1Meter readings not reaching billing:\n" + "\n".join(lines),
            },
            country_code=code,
        )
        for s in due:
            state[s["meter_id"]] = now.isoformat()
        state_path.write_text(json.dumps(state))
    elif due:
        log.warning("%s: no WhatsApp bridge configured; alert logged only", code)
    return 1


if __name__ == "__main__":
    sys.exit(main())
