#!/usr/bin/env python3
"""Repoint STA Wi-Fi on an already-provisioned 1Meter. Keeps Thing + certs.

Use this when the unit is no longer on the 1Meter provisioning LAN (STA is
stuck on an old site SSID) so CC Config / MQTT cfg/network cannot reach it.

  1. Join the unit's SoftAP: 1Meter_<last6 of MAC> / 1Meter00
  2. python3 repoint_wifi.py --ssid 1PWRBENIN_SIN --password '...'

Works on 1.1.68 (bootstrap fallback) and newer firmware that has
POST /v1/provision/network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_IP = "192.168.4.1"


def get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def post_json(url: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "done", "body": json.loads(resp.read().decode() or "{}")}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        err = SystemExit(f"device rejected {url}: HTTP {e.code} {body}")
        err.code = e.code  # type: ignore[attr-defined]
        raise err
    except Exception as e:
        # Persist-then-reboot usually drops the TCP session.
        return {"status": "rebooting", "note": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Repoint 1Meter STA Wi-Fi over SoftAP")
    ap.add_argument("--ip", default=DEFAULT_IP, help="device IP (default 192.168.4.1)")
    ap.add_argument("--ssid", required=True, help="new site STA SSID (not 1Meter)")
    ap.add_argument("--password", required=True, help="new site STA password")
    ap.add_argument("--thing", default="", help="override Thing name (default: from device)")
    args = ap.parse_args()

    if args.ssid in ("1Meter", "1Meter00"):
        raise SystemExit("refusing to set STA to the provisioning LAN")

    try:
        status = get_json(f"http://{args.ip}/v1/provision/status")
    except Exception as e:
        raise SystemExit(
            f"no device at {args.ip} ({e}). Join SoftAP 1Meter_xxxxxx / 1Meter00 first."
        )

    thing = args.thing or (status.get("thing_name") or "").strip()
    print(f"device: thing={thing or '—'} provisioned={status.get('provisioned')} "
          f"fw_wifi_ver={status.get('wifi_version')} mac={status.get('pcb_mac')}")
    if not status.get("provisioned") and not status.get("has_runtime_tls"):
        raise SystemExit("device looks virgin — use the station provision flow, not this script")
    if not thing:
        raise SystemExit("device has no Thing name; aborting so we do not invent an identity")

    version = max(int(status.get("wifi_version") or 0) + 1, 1)
    payload = {"ssid": args.ssid, "password": args.password, "version": version}

    used = "/v1/provision/network"
    try:
        result = post_json(f"http://{args.ip}{used}", payload)
    except SystemExit as e:
        if getattr(e, "code", None) not in (404, 405):
            raise
        used = "/v1/provision/bootstrap"
        payload["thing_name"] = thing
        result = post_json(f"http://{args.ip}{used}", payload)

    print(f"applied via {used}: {result}")
    print(f"{thing} will join {args.ssid}. It will not come back to 1Meter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
