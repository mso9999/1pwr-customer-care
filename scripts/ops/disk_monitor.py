#!/usr/bin/env python3
"""
Hourly disk-space monitor for the CC production host.

Sends a WhatsApp broadcast via the CC bridge when the root filesystem
usage exceeds a warning (default 80%) or emergency (default 90%) threshold.

A state file prevents repeat alerts within the same severity window so
the tracker group isn't flooded if the condition persists.

Usage (systemd oneshot):
    PYTHONPATH=/opt/cc-portal/backend venv/bin/python3 scripts/ops/disk_monitor.py

Env vars (read from /opt/1pdb/.env via systemd EnvironmentFile):
    CC_BRIDGE_NOTIFY_URL   / CC_BRIDGE_SECRET       — LS bridge (required)
    DISK_WARN_PCT          — warning threshold, default 80
    DISK_EMERGENCY_PCT     — emergency threshold, default 90
    DISK_MONITOR_PATH      — filesystem path to check, default /
    DISK_STATE_FILE        — path to last-alerted state file,
                             default /var/run/cc-disk-monitor.state
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cc-disk-monitor")

# ── config ────────────────────────────────────────────────────────────────────
WARN_PCT       = int(os.environ.get("DISK_WARN_PCT", "80"))
EMERGENCY_PCT  = int(os.environ.get("DISK_EMERGENCY_PCT", "90"))
CHECK_PATH     = os.environ.get("DISK_MONITOR_PATH", "/")
STATE_FILE     = Path(os.environ.get("DISK_STATE_FILE", "/var/run/cc-disk-monitor.state"))
# Re-alert after this many seconds even if severity unchanged (avoids silent drift)
RESEND_AFTER_S = 6 * 3600  # 6 hours

BRIDGE_URL    = os.environ.get("CC_BRIDGE_NOTIFY_URL", "")
BRIDGE_SECRET = os.environ.get("CC_BRIDGE_SECRET", "")


# ── bridge send ───────────────────────────────────────────────────────────────
def _send_whatsapp(text: str) -> bool:
    """POST a verbatim message to the bridge /broadcast route."""
    if not BRIDGE_URL or not BRIDGE_SECRET:
        log.warning("bridge not configured (CC_BRIDGE_NOTIFY_URL / CC_BRIDGE_SECRET unset) — alert not sent")
        return False
    # Derive broadcast URL from notify URL
    url = BRIDGE_URL
    for suffix in ("/notify/", "/notify"):
        if url.endswith(suffix):
            url = url[: -len(suffix)] + "/broadcast"
            break
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
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


# ── state helpers ─────────────────────────────────────────────────────────────
def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _write_state(severity: str) -> None:
    try:
        STATE_FILE.write_text(json.dumps({"severity": severity, "ts": time.time()}))
    except Exception as exc:
        log.warning("could not write state file: %s", exc)


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    usage = shutil.disk_usage(CHECK_PATH)
    pct = int(usage.used * 100 / usage.total)
    free_gb = usage.free / 1024 ** 3
    total_gb = usage.total / 1024 ** 3
    log.info("disk %s: %d%% used, %.1f GB free of %.1f GB", CHECK_PATH, pct, free_gb, total_gb)

    if pct < WARN_PCT:
        # All clear — clear state if previously alerted
        if STATE_FILE.exists():
            STATE_FILE.unlink(missing_ok=True)
        log.info("disk OK (below %d%% warning threshold)", WARN_PCT)
        return 0

    # Determine current severity
    severity = "EMERGENCY" if pct >= EMERGENCY_PCT else "WARNING"
    now = time.time()
    state = _read_state()
    last_severity = state.get("severity")
    last_ts = float(state.get("ts", 0))
    already_alerted = (last_severity == severity) and (now - last_ts < RESEND_AFTER_S)

    if already_alerted:
        log.info("disk %s at %d%% — alert already sent for this severity, skipping", severity, pct)
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    emoji = "🚨" if severity == "EMERGENCY" else "⚠️"

    msg = (
        f"{emoji} *CC Host Disk {severity}* [{ts}]\n"
        f"Root filesystem: *{pct}% used* ({free_gb:.1f} GB free of {total_gb:.0f} GB)\n"
    )
    if severity == "EMERGENCY":
        msg += (
            "Postgres may crash if disk fills completely.\n"
            "Action: SSH to host and run:\n"
            "  sudo du -sh /var/backups/1pwr-cc/*/\n"
            "  sudo journalctl --vacuum-size=200M\n"
            "  sudo systemctl start postgresql@16-main  # if already crashed"
        )
    else:
        msg += (
            "No immediate action required, but investigate soon.\n"
            "Check: /var/backups/1pwr-cc/ (keep only last 3), /var/log/ journals."
        )

    log.warning("disk %s: %d%% — sending WhatsApp alert", severity, pct)
    _send_whatsapp(msg)
    _write_state(severity)
    return 0


if __name__ == "__main__":
    sys.exit(main())
