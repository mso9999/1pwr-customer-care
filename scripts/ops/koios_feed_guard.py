#!/usr/bin/env python3
"""
Daily guard against Koios duplicate-heartbeat inflation (RCA 2026-06-17).

Koios's /api/v2/report intermittently returns each 15-min heartbeat duplicated
N times. Our importers now dedup before summing, so stored data is safe — but we
still want to KNOW when the upstream feed is misbehaving (it inflated Benin for
months before a human noticed). This guard samples the most recent fully-reported
days for every koios site and alarms when the raw/dedup ratio exceeds a
threshold, so we hear about feed defects in a day instead of a quarter.

Read-only against Koios; no DB writes. Alerts via the CC WhatsApp bridge.

Usage (systemd oneshot, daily timer):
    CC_BRIDGE_NOTIFY_URL=... CC_BRIDGE_SECRET=... \
      python3 koios_feed_guard.py
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout
)
log = logging.getLogger("koios-feed-guard")

# (country, env_file) -> {site_code: site_uuid}
ENVS = {"LS": "/opt/1pdb/.env", "BN": "/opt/1pdb-bn/.env"}
SITES = {
    "LS": {
        "MAT": "2f7c38b8-4a70-44fd-bf9c-ebf2b2aa78c0",
        "TLH": "db5bf699-31ea-44b6-91c5-1b41e4a2d130",
        "MAS": "101c443e-6500-4a4d-8cdc-6bd15f4388c8",
        "SHG": "bd7c477d-0742-4056-b75c-38b14ac7cf97",
        "KET": "a075cbc1-e920-455e-9d5a-8595061dfec0",
        "LSB": "ed0766c4-9270-4254-a107-eb4464a96ed9",
        "SEH": "0a4fdca5-2d78-4979-8051-10f21a216b16",
        "TOS": "b564c8d6-a6c1-43d4-98d1-87ed8cd8ffd7",
    },
    "BN": {
        "GBO": "a23c334e-33f7-473d-9ae3-9e631d5336e4",
        "SAM": "8f80b0a8-0502-4e26-9043-7152979360aa",
    },
}

RATIO_THRESHOLD = float(os.environ.get("FEED_RATIO_THRESHOLD", "1.05"))
# Sample these day-offsets back from today (skip today/yesterday: still filling).
SAMPLE_OFFSETS = [int(x) for x in os.environ.get("FEED_SAMPLE_OFFSETS", "2,3,4").split(",")]
STATE_FILE = Path(os.environ.get("FEED_GUARD_STATE_FILE", "/var/run/cc-koios-feed-guard.state"))
RESEND_AFTER_S = float(os.environ.get("RESEND_AFTER_S", str(12 * 3600)))

BRIDGE_URL = os.environ.get("CC_BRIDGE_NOTIFY_URL", "")
BRIDGE_SECRET = os.environ.get("CC_BRIDGE_SECRET", "")


def load_env(path: str) -> dict:
    e = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def day_ratio(env: dict, site_id: str, d: str):
    qs = urllib.parse.urlencode({
        "granularity": "daily", "type": "readings", "site_id": site_id, "date": d})
    url = env.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud") + "/api/v2/report?" + qs
    req = urllib.request.Request(url, headers={
        "X-API-KEY": env["KOIOS_API_KEY"], "X-API-SECRET": env["KOIOS_API_SECRET"]})
    try:
        raw = urllib.request.urlopen(req, timeout=180).read().decode()
    except urllib.error.HTTPError as ex:
        return None if ex.code == 404 else f"http{ex.code}"
    except Exception as ex:
        return f"err:{ex}"
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return None
    raw_sum = 0.0
    dedup: dict = {}
    for r in rows:
        try:
            k = float(r.get("kilowatt_hours") or 0)
        except (ValueError, TypeError):
            k = 0.0
        raw_sum += k
        dedup[(r.get("meter/serial"), r.get("heartbeat_start"))] = k
    ds = sum(dedup.values())
    return raw_sum / ds if ds else 1.0


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
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("bridge_broadcast failed: %s", exc)
        return False


def main() -> int:
    today = date.today()
    sample_days = [(today - timedelta(days=o)).strftime("%Y-%m-%d") for o in SAMPLE_OFFSETS]
    inflated: list[str] = []
    for cty, sites in SITES.items():
        env = load_env(ENVS[cty])
        for site, sid in sites.items():
            worst = 0.0
            for d in sample_days:
                r = day_ratio(env, sid, d)
                if isinstance(r, (int, float)):
                    worst = max(worst, r)
                time.sleep(0.3)
            if worst > RATIO_THRESHOLD:
                inflated.append(f"{cty}/{site}: feed ratio {worst:.2f}x")
                log.warning("%s/%s worst ratio %.2f", cty, site, worst)
            else:
                log.info("%s/%s ok (worst %.2f)", cty, site, worst)

    if not inflated:
        log.info("koios feed clean across all sites")
        STATE_FILE.unlink(missing_ok=True)
        return 0

    signature = "|".join(sorted(inflated))
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = {}
    now = time.time()
    if state.get("sig") == signature and now - float(state.get("ts", 0)) < RESEND_AFTER_S:
        log.info("same inflation set already alerted — skipping")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"⚠️ *Koios feed duplication detected* [{ts}]\n"
        + "\n".join(f"• {x}" for x in inflated)
        + "\n\nImporters dedup before storing, so balances are protected, but the "
        "upstream Koios feed is returning duplicate heartbeats. Flag to SparkMeter."
    )
    _send_whatsapp(msg)
    try:
        STATE_FILE.write_text(json.dumps({"sig": signature, "ts": now}))
    except Exception as exc:
        log.warning("could not write state: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
