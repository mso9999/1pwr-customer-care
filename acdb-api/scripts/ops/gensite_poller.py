#!/usr/bin/env python3
"""
CC gensite inverter telemetry poller.

Runs as a oneshot from ``cc-gensite-poll.timer`` every minute. Each invocation:

  1. Enumerates all stored ``site_credentials``.
  2. For each credential whose per-vendor cadence has elapsed, calls
     ``adapter.fetch_live(cred, equipment)`` and writes rows to
     ``inverter_readings``.
  3. On a slower cadence, calls ``adapter.fetch_alarms(...)`` and inserts
     new events into ``inverter_alarms``.
  4. Persists per-credential state in ``STATE_FILE`` so alerts fire only on
     transitions (offline / recovery / new CRITICAL alarm), not on every run.

Skips adapters whose ``implementation_status == 'stub'`` (Solarman, Sinosoar,
SMA for now) — they'd just fail.

Env (loaded by the systemd unit from ``/opt/1pdb/.env``):
  DATABASE_URL                     1PDB DSN                                   (required)
  CC_CREDENTIAL_ENCRYPTION_KEY     Fernet key                                 (required)
  CC_BRIDGE_NOTIFY_URL[_<CC>]      WhatsApp bridge /notify (per country)
  CC_BRIDGE_SECRET[_<CC>]          Bridge shared secret
  STATE_FILE                       Default /var/lib/cc-gensite-poll/state.json
  POLL_FAIL_ALERT_THRESHOLD        Consecutive failures before alerting. Default 3.
  GENSITE_DRY_RUN                  "1" to log only: no DB writes, no alerts.

Designed to be safely re-run on demand:
  sudo -u cc_api /opt/cc-portal/backend/venv/bin/python \\
       /opt/cc-portal/backend/scripts/ops/gensite_poller.py

Exit codes: 0 on success (incl. no-work-to-do); 2 on missing config.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make acdb-api/ importable when run as a script under /opt/cc-portal/backend/.
# Layout on the host: /opt/cc-portal/backend/scripts/ops/gensite_poller.py
# parents[2]           -> /opt/cc-portal/backend/ (== acdb-api/ after rsync)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gensite-poller")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STATE_FILE = Path(os.environ.get("STATE_FILE", "/var/lib/cc-gensite-poll/state.json"))
FAIL_THRESHOLD = int(os.environ.get("POLL_FAIL_ALERT_THRESHOLD", "3"))
DRY_RUN = os.environ.get("GENSITE_DRY_RUN", "0") == "1"

# Per-vendor live polling cadence (seconds). Keep conservative until we have
# real-world rate-limit experience. The timer fires every 60s regardless —
# these values define the *minimum* gap between adapter calls per credential.
VENDOR_LIVE_CADENCE = {
    "victron":  60,
    "deye":     300,
    "solarman": 300,
    "sinosoar": 120,
    "sma":      600,
    "other":    300,
}
ALARM_CADENCE_SECONDS = 300   # 5 min for all vendors


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"version": 1, "credentials": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError) as exc:
        log.warning("Could not read state file %s: %s; starting fresh", STATE_FILE, exc)
        return {"version": 1, "credentials": {}}


def save_state(state: Dict[str, Any]) -> None:
    if DRY_RUN:
        return
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True, default=str))
    except OSError as exc:
        log.warning("Could not write state file %s: %s", STATE_FILE, exc)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Alerting (WhatsApp bridge → CC phone + OnM Ticket Tracker group)
# ---------------------------------------------------------------------------

def alert(country_code: str, text: str) -> None:
    """Post a notification to the country's WhatsApp bridge. No-op in DRY_RUN."""
    if DRY_RUN:
        log.info("[DRY_RUN] would alert (%s): %s", country_code, text)
        return
    try:
        from cc_bridge_notify import notify_cc_bridge
    except ImportError:
        log.warning("cc_bridge_notify not importable; skipping alert")
        return
    try:
        notify_cc_bridge(
            {"kind": "gensite", "text": text},
            country_code=country_code,
        )
    except Exception as exc:
        log.warning("bridge notify failed: %s", exc)


def _format_offline_alert(
    site: str, vendor: str, failures: int, err: str, last_ok: Optional[datetime]
) -> str:
    last_ok_str = last_ok.isoformat(timespec="minutes") if last_ok else "never"
    return (
        f"Gensite outage — {site} {vendor} backend unreachable after {failures} "
        f"consecutive attempts. Last OK: {last_ok_str} UTC. Error: {err[:160]}. "
        f"https://cc.1pwrafrica.com/gensite/{site}"
    )


def _format_recovery_alert(site: str, vendor: str, failures: int) -> str:
    return (
        f"Gensite recovered — {site} {vendor} backend back online after "
        f"{failures} failed attempt(s). https://cc.1pwrafrica.com/gensite/{site}"
    )


def _format_alarm_alert(site: str, vendor: str, alarm) -> str:
    raised = (
        alarm.raised_at.isoformat(timespec="minutes")
        if hasattr(alarm.raised_at, "isoformat")
        else str(alarm.raised_at)
    )
    msg = (alarm.vendor_msg or alarm.vendor_code or "alarm").strip()
    return (
        f"Gensite alarm (CRITICAL) — {site} {vendor}: {msg[:200]}. "
        f"Raised {raised} UTC. "
        f"https://cc.1pwrafrica.com/gensite/{site}"
    )


# ---------------------------------------------------------------------------
# Per-credential poll
# ---------------------------------------------------------------------------

def poll_credential(
    cred_meta: Dict[str, Any],
    state: Dict[str, Any],
    now: datetime,
) -> None:
    """Poll a single (site, vendor, backend). Mutates ``state`` in place."""
    from gensite import store
    from gensite.adapters import REGISTRY
    from gensite.adapters.base import AdapterError
    from gensite.crypto import CredentialCryptoError

    site_code = cred_meta["site_code"]
    country = cred_meta["country"]
    vendor = cred_meta["vendor"]
    backend = cred_meta["backend"]
    cred_id = cred_meta["id"]

    key = f"{site_code}/{vendor}/{backend}"
    cs = state["credentials"].setdefault(key, {})

    adapter = REGISTRY.get(vendor)
    if adapter is None:
        log.debug("%s: no adapter registered", key)
        return
    if getattr(adapter, "implementation_status", "") == "stub":
        log.debug("%s: adapter is a stub, skipping", key)
        return

    # Cadence gate
    cadence = VENDOR_LIVE_CADENCE.get(vendor, 300)
    last_poll = parse_ts(cs.get("last_poll_at"))
    cf = int(cs.get("consecutive_failures", 0))
    effective_cadence = cadence * min(2 ** cf, 30)  # exponential backoff cap at 30x
    if last_poll and (now - last_poll).total_seconds() < effective_cadence:
        return

    # Decrypt
    try:
        cred = store.load_credential_for_adapter(site_code, vendor, backend)
    except CredentialCryptoError as exc:
        log.error("%s: decrypt failed: %s", key, exc)
        cs["last_poll_at"] = now.isoformat()
        cs["last_poll_ok"] = False
        cs["last_error"] = f"decrypt: {exc}"
        return
    if cred is None:
        log.warning("%s: credential row disappeared mid-run", key)
        return

    # Equipment filter
    all_eq = store.list_equipment(site_code, include_decommissioned=False)
    vendor_eq = [e for e in all_eq if e["vendor"] == vendor]
    if not vendor_eq:
        log.debug("%s: no active equipment for this vendor", key)
        return
    adapter_eq = [store.as_adapter_equipment(e) for e in vendor_eq]

    # Live fetch
    ok = False
    err_msg: Optional[str] = None
    try:
        readings = adapter.fetch_live(cred, adapter_eq)
        if not DRY_RUN and readings:
            store.insert_readings(readings)
        ok = True
        log.info("%s: OK, %d reading(s)", key, len(readings))
    except AdapterError as exc:
        err_msg = str(exc)
        log.warning("%s: adapter error: %s", key, err_msg)
    except Exception as exc:
        err_msg = f"unexpected: {exc}"
        log.exception("%s: unexpected poll error", key)

    # Update state + DB verify row
    last_ok = parse_ts(cs.get("last_ok_at"))
    cs["last_poll_at"] = now.isoformat()
    cs["last_poll_ok"] = ok
    cs["last_error"] = err_msg
    if ok:
        cs["last_ok_at"] = now.isoformat()
        prev_cf = cf
        cs["consecutive_failures"] = 0
        if cs.get("alerted_offline"):
            alert(country, _format_recovery_alert(site_code, vendor, prev_cf))
            cs["alerted_offline"] = False
    else:
        cs["consecutive_failures"] = cf + 1
        if (cf + 1) >= FAIL_THRESHOLD and not cs.get("alerted_offline"):
            alert(
                country,
                _format_offline_alert(site_code, vendor, cf + 1, err_msg or "", last_ok),
            )
            cs["alerted_offline"] = True

    if not DRY_RUN:
        try:
            store.update_credential_verify_result(cred_id, ok=ok, error=err_msg)
        except Exception as exc:
            log.warning("%s: could not update verify result: %s", key, exc)

    # Alarm fetch (only when live poll succeeded — otherwise we'd just re-fail)
    if not ok:
        return
    last_alarm_at = parse_ts(cs.get("last_alarm_fetch_at"))
    if last_alarm_at and (now - last_alarm_at).total_seconds() < ALARM_CADENCE_SECONDS:
        return
    try:
        since = last_alarm_at or (now - timedelta(hours=24))
        alarms = adapter.fetch_alarms(cred, adapter_eq, since)
    except Exception as exc:
        log.warning("%s: alarm fetch failed: %s", key, exc)
        return

    cs["last_alarm_fetch_at"] = now.isoformat()
    if not alarms:
        return

    if not DRY_RUN:
        try:
            inserted = store.insert_alarms(alarms)
            log.info("%s: %d alarm(s) fetched, %d new", key, len(alarms), inserted)
        except Exception as exc:
            log.warning("%s: insert_alarms failed: %s", key, exc)
            return

    # CRITICAL alarm WhatsApp transitions — one alert per unique alarm event.
    alerted_keys = set(cs.get("alerted_alarm_keys") or [])
    for a in alarms:
        if (a.severity or "").lower() != "critical":
            continue
        alarm_key = f"{a.equipment_id}|{a.vendor_code}|{a.raised_at.isoformat() if hasattr(a.raised_at, 'isoformat') else a.raised_at}"
        if alarm_key in alerted_keys:
            continue
        alert(country, _format_alarm_alert(site_code, vendor, a))
        alerted_keys.add(alarm_key)
    # Cap stored alarm keys to the most recent 200 to keep the state file small.
    cs["alerted_alarm_keys"] = list(alerted_keys)[-200:]


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    from gensite import store
    from gensite.crypto import key_is_configured

    if not key_is_configured():
        log.error(
            "CC_CREDENTIAL_ENCRYPTION_KEY is not set; cannot decrypt credentials. "
            "See docs/ops/gensite-credentials.md."
        )
        return 2

    try:
        credentials = store.enumerate_credentials_for_poller()
    except Exception as exc:
        log.exception("enumerate_credentials_for_poller failed: %s", exc)
        return 1

    if not credentials:
        log.info("No gensite credentials configured yet; nothing to poll.")
        return 0

    log.info(
        "gensite-poller starting: %d credential(s)%s",
        len(credentials),
        " [DRY_RUN]" if DRY_RUN else "",
    )

    state = load_state()
    now = datetime.now(timezone.utc)
    for cred_meta in credentials:
        try:
            poll_credential(cred_meta, state, now)
        except Exception as exc:
            log.exception(
                "Unhandled error polling %s/%s/%s: %s",
                cred_meta.get("site_code"),
                cred_meta.get("vendor"),
                cred_meta.get("backend"),
                exc,
            )
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
