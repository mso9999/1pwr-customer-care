#!/usr/bin/env python3
"""1Meter provisioning station — local bridge between the CC portal and virgin
gateways on a local provisioning network.

A technician runs this on a laptop that is on the ``1Meter`` provisioning LAN
*and* has internet to CC. It serves a small UI at http://localhost:8787 that:

  1. logs in to CC (employee credentials),
  2. scans the local subnet and enumerates virgin gateways (probes each unit's
     ``/v1/provision/status`` and resolves the PCB MAC from the ARP table),
  3. lets the tech pick the destination site + site Wi-Fi and confirm,
  4. batch-allocates stable gateway-pool Things + certs via CC
     (``POST /api/provisioning/gateways``) — no customer account yet,
  5. delivers each CC-issued bootstrap to the device's local API, with progress,
  6. shows the provisioned (still unallocated) gateways from CC.

Why a local agent: the CC portal is HTTPS and the device API is plain HTTP on a
private IP, so a browser on the CC page can't call the device (mixed content).
This agent serves the UI from http://localhost (no mixed content), talks to CC
over HTTPS, and to the device over HTTP. Every result is synced to CC; the agent
holds no durable state.

Stdlib only — no pip install. Python 3.9+.

  python3 provisioning_station.py --cc https://cc.1pwrafrica.com [--subnet 192.168.4.0/24] [--port 8787]
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
MAC_RE = re.compile(r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.I)

# ---------------------------------------------------------------------------
# In-memory session (single technician per running station)
# ---------------------------------------------------------------------------


class Session:
    def __init__(self, cc_base: str, subnet: str | None):
        self.cc_base = cc_base.rstrip("/")
        self.subnet = subnet
        self.token: str | None = None
        self.user: dict | None = None
        # pcb_mac -> {"ip":..., "bootstrap":..., "thing_name":...}
        self.pending: dict[str, dict] = {}
        self.lock = threading.Lock()


SESSION: Session


# ---------------------------------------------------------------------------
# CC client
# ---------------------------------------------------------------------------


def cc_request(method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
    url = f"{SESSION.cc_base}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if auth and SESSION.token:
        headers["Authorization"] = f"Bearer {SESSION.token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(f"CC {method} {path} -> {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"CC unreachable ({e.reason}). Check internet / --cc URL.")


# ---------------------------------------------------------------------------
# Local network discovery
# ---------------------------------------------------------------------------


def primary_ipv4() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def default_subnet() -> str:
    ip = primary_ipv4()
    if not ip:
        return "192.168.4.0/24"
    net = ipaddress.ip_network(ip + "/24", strict=False)
    return str(net)


def local_ipv4s() -> set[str]:
    """All local IPv4 addresses across interfaces (so a laptop hotspot subnet,
    e.g. Windows Mobile Hotspot 192.168.137.x, is included alongside the
    internet-facing one)."""
    ips: set[str] = set()
    p = primary_ipv4()
    if p:
        ips.add(p)
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = res[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    return ips


def local_subnets() -> list[str]:
    """Distinct /24 networks for every local IPv4 (the set we scan by default)."""
    nets: list[str] = []
    for ip in sorted(local_ipv4s()):
        try:
            n = str(ipaddress.ip_network(ip + "/24", strict=False))
            if n not in nets:
                nets.append(n)
        except Exception:
            pass
    return nets or [default_subnet()]


def scan_subnets(subnets: list[str]) -> list[dict]:
    """Scan several subnets and merge results (dedup by IP)."""
    seen: dict[str, dict] = {}
    for sn in subnets:
        sn = sn.strip()
        if not sn:
            continue
        for g in scan_subnet(sn):
            seen[g["ip"]] = g
    out = list(seen.values())
    out.sort(key=lambda d: ipaddress.ip_address(d["ip"]))
    return out


def probe_device(ip: str, timeout: float = 1.5) -> dict | None:
    """GET http://<ip>/v1/provision/status; return parsed JSON or None."""
    try:
        req = urllib.request.Request(f"http://{ip}/v1/provision/status", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            j = json.loads(resp.read().decode() or "{}")
            if isinstance(j, dict) and j.get("ok"):
                return j
    except Exception:
        return None
    return None


def resolve_mac(ip: str) -> str | None:
    """Best-effort PCB MAC via ping (populate ARP) + arp table parse."""
    system = platform.system().lower()
    try:
        if system == "windows":
            subprocess.run(["ping", "-n", "1", "-w", "800", ip],
                           capture_output=True, timeout=3)
            out = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=4).stdout
        else:
            count_flag = "-c"
            subprocess.run(["ping", count_flag, "1", "-W", "1", ip],
                           capture_output=True, timeout=3)
            out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return None
    m = MAC_RE.search(out or "")
    return m.group(0).lower().replace("-", ":") if m else None


def scan_subnet(subnet: str) -> list[dict]:
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    found: list[dict] = []
    found_lock = threading.Lock()

    def worker(ip: str):
        status = probe_device(ip)
        if status is None:
            return
        # Prefer the device-reported MAC (firmware >= the mDNS/MAC build);
        # fall back to ARP for older units that don't report it.
        mac = (status.get("pcb_mac") or "").strip().lower() or resolve_mac(ip)
        with found_lock:
            found.append({
                "ip": ip,
                "pcb_mac": mac,
                "provisioned": bool(status.get("provisioned")),
                "thing_name": status.get("thing_name"),
                "has_runtime_tls": bool(status.get("has_runtime_tls")),
                "has_runtime_wifi": bool(status.get("has_runtime_wifi")),
            })

    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(worker, hosts))
    found.sort(key=lambda d: ipaddress.ip_address(d["ip"]))
    return found


def reprobe_units(targets: list[dict]) -> list[dict]:
    """Fast targeted presence check: re-probe only the given {ip, pcb_mac} units
    (not a full subnet sweep) and report which are still responding, with their
    current status. Used by the 'Identify by elimination' flow: power off one
    unit, re-probe, the MAC that vanished is the unit you just eliminated."""
    out: list[dict] = []
    out_lock = threading.Lock()

    def worker(u: dict):
        ip = u.get("ip")
        status = probe_device(ip, timeout=2.0) if ip else None
        with out_lock:
            out.append({
                "ip": ip,
                "pcb_mac": (status.get("pcb_mac") or "").strip().lower() or u.get("pcb_mac"),
                "online": status is not None,
                "provisioned": bool(status.get("provisioned")) if status else None,
                "thing_name": status.get("thing_name") if status else None,
            })

    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(worker, targets))
    out.sort(key=lambda d: ipaddress.ip_address(d["ip"]) if d.get("ip") else "0.0.0.0")
    return out


def deliver_bootstrap(ip: str, bootstrap: dict, timeout: float = 30.0) -> dict:
    """POST the bootstrap to the device local API.

    The firmware persists the identity/cert/Wi-Fi and then immediately REBOOTS,
    which usually severs the TCP connection before the HTTP response is fully
    received. So a read timeout / connection reset here is the EXPECTED success
    path (the unit applied the bootstrap and is rebooting), not a failure. We
    return status="rebooting" for those, and only treat an explicit device error
    (HTTP 4xx/5xx) or an inability to connect as a real failure.
    """
    data = json.dumps(bootstrap).encode()
    req = urllib.request.Request(f"http://{ip}/v1/provision/bootstrap", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "done", "device_response": json.loads(resp.read().decode() or "{}")}
    except urllib.error.HTTPError as e:
        # Device responded with an error (e.g. 400 bad payload) -> real failure.
        body = ""
        try:
            body = e.read().decode(errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"device rejected bootstrap: HTTP {e.code} {body}".strip())
    except (socket.timeout, TimeoutError, http.client.RemoteDisconnected, ConnectionResetError):
        return {"status": "rebooting"}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError, ConnectionResetError)):
            return {"status": "rebooting"}
        raise  # e.g. connection refused / host unreachable -> real failure


# ---------------------------------------------------------------------------
# HTTP handler (serves UI + local JSON API)
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # quiet
        pass

    def _send(self, code: int, payload, content_type="application/json"):
        if content_type == "application/json":
            body = json.dumps(payload).encode()
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    # ---- GET ----
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(500, {"error": "index.html missing"})
        if self.path == "/api/session":
            return self._send(200, {
                "logged_in": SESSION.token is not None,
                "user": SESSION.user,
                "cc_base": SESSION.cc_base,
                # Default to ALL local subnets (comma-separated) so a laptop
                # hotspot subnet is scanned alongside the internet-facing one.
                "subnet": SESSION.subnet or ", ".join(local_subnets()),
            })
        if self.path == "/api/sitecodes":
            try:
                return self._send(200, {"sites": cc_request("GET", "/provisioning/site-codes")})
            except Exception as e:
                return self._send(502, {"error": str(e)})
        if self.path == "/api/provisioned":
            try:
                return self._send(200, cc_request("GET", "/provisioning/meters"))
            except Exception as e:
                return self._send(502, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"error": f"bad body: {e}"})

        if self.path == "/api/login":
            try:
                resp = cc_request("POST", "/auth/employee-login", {
                    "employee_id": body.get("employee_id"),
                    "password": body.get("password"),
                }, auth=False)
                SESSION.token = resp.get("access_token")
                SESSION.user = resp.get("user")
                return self._send(200, {"ok": True, "user": SESSION.user})
            except Exception as e:
                return self._send(401, {"error": str(e)})

        if self.path == "/api/scan":
            raw = (body.get("subnet") or SESSION.subnet or ", ".join(local_subnets()))
            subnets = [s.strip() for s in str(raw).split(",") if s.strip()]
            try:
                return self._send(200, {"subnet": ", ".join(subnets),
                                        "gateways": scan_subnets(subnets)})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if self.path == "/api/reprobe":
            units = body.get("units") or []
            if not units:
                return self._send(400, {"error": "no units to re-probe"})
            try:
                return self._send(200, {"units": reprobe_units(units)})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if self.path == "/api/allocate":
            return self._handle_allocate(body)

        if self.path == "/api/deliver":
            return self._handle_deliver(body)

        if self.path == "/api/reconcile":
            if not SESSION.token:
                return self._send(401, {"error": "not logged in to CC"})
            try:
                return self._send(200, cc_request("POST", "/provisioning/reconcile"))
            except Exception as e:
                return self._send(502, {"error": str(e)})

        return self._send(404, {"error": "not found"})

    def _handle_allocate(self, body: dict):
        if not SESSION.token:
            return self._send(401, {"error": "not logged in to CC"})
        units = body.get("units") or []
        if not units:
            return self._send(400, {"error": "no units selected"})
        ip_by_mac = {}
        cc_units = []
        for u in units:
            mac = (u.get("pcb_mac") or "").strip().lower()
            if not mac:
                continue
            ip_by_mac[mac] = u.get("ip")
            cc_units.append({"pcb_mac": mac, "box_label": u.get("box_label") or None})
        try:
            resp = cc_request("POST", "/provisioning/gateways", {
                "site_code": body.get("site_code"),
                "units": cc_units,
                "wifi_ssid": body.get("wifi_ssid"),
                "wifi_password": body.get("wifi_password"),
            })
        except Exception as e:
            return self._send(502, {"error": str(e)})

        with SESSION.lock:
            for g in resp.get("gateways", []):
                mac = (g.get("pcb_mac") or "").lower()
                SESSION.pending[mac] = {
                    "ip": ip_by_mac.get(mac),
                    "bootstrap": g.get("bootstrap"),
                    "thing_name": g.get("thing_name"),
                }
        # Don't return cert/key material to the browser; just identities.
        summary = [{"pcb_mac": g.get("pcb_mac"), "thing_name": g.get("thing_name"),
                    "box_label": g.get("box_label"), "ip": ip_by_mac.get((g.get("pcb_mac") or "").lower())}
                   for g in resp.get("gateways", [])]
        return self._send(200, {
            "site": resp.get("site"),
            "provisioned": resp.get("provisioned"),
            "failed": resp.get("failed"),
            "gateways": summary,
            "errors": resp.get("errors", []),
        })

    def _handle_deliver(self, body: dict):
        mac = (body.get("pcb_mac") or "").strip().lower()
        with SESSION.lock:
            pend = SESSION.pending.get(mac)
        if not pend or not pend.get("bootstrap"):
            return self._send(404, {"error": f"no pending bootstrap for {mac} (allocate first)"})
        ip = pend.get("ip")
        if not ip:
            return self._send(400, {"error": f"no device IP known for {mac}"})
        try:
            result = deliver_bootstrap(ip, pend["bootstrap"])
            with SESSION.lock:
                SESSION.pending.pop(mac, None)
            rebooting = result.get("status") == "rebooting"
            return self._send(200, {
                "ok": True,
                "rebooting": rebooting,
                "thing_name": pend.get("thing_name"),
                "device_response": result.get("device_response"),
                "note": ("device applied bootstrap and rebooted (no HTTP response, "
                         "which is normal) — confirm with Re-scan") if rebooting else None,
            })
        except Exception as e:
            return self._send(502, {"error": f"device delivery failed: {e}",
                                    "thing_name": pend.get("thing_name")})


def main():
    ap = argparse.ArgumentParser(description="1Meter provisioning station")
    ap.add_argument("--cc", default=os.environ.get("CC_BASE", "https://cc.1pwrafrica.com"),
                    help="CC portal base URL")
    ap.add_argument("--subnet", default=os.environ.get("PROV_SUBNET"),
                    help="provisioning LAN CIDR (default: auto-detect /24)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PROV_PORT", "8787")))
    a = ap.parse_args()

    global SESSION
    SESSION = Session(a.cc, a.subnet)
    sub = a.subnet or default_subnet()
    print("=" * 64)
    print(" 1Meter Provisioning Station")
    print(f"   CC:      {SESSION.cc_base}")
    print(f"   Subnet:  {sub}")
    print(f"   Open:    http://localhost:{a.port}")
    print("=" * 64)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
