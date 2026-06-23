# 1Meter Provisioning Station

A small local tool a technician runs on a laptop to **batch‑provision virgin
gateways** on a local provisioning network, driven by and synced to the CC
portal. It bridges the gap that CC (HTTPS, internet) cannot reach a virgin
gateway directly: a fresh unit has no certificate, so it can't connect to AWS
IoT, and a browser on the HTTPS CC page can't call the device's plain‑HTTP local
API (mixed content). This agent serves its UI from `http://localhost`, talks to
**CC over HTTPS** and to the **device over HTTP**.

## What it does

1. **Sign in to CC** (employee credentials).
2. **Scan** the provisioning subnet, enumerate gateways (probes each unit's
   `/v1/provision/status`, resolves the PCB MAC from the ARP table), and flags
   which are *virgin* vs already provisioned.
3. **Assign** a destination **site** (canonical CC site code) + site Wi‑Fi, and
   confirm the batch.
4. **Provision**: CC allocates a stable gateway‑pool Thing per unit
   (`<SITE>-GW-####`) + certificate (no customer account yet), and the station
   delivers each bootstrap to the device's local API, with a progress bar.
5. **See provisioned (unallocated) gateways** straight from CC, with their
   lifecycle stage.

Everything is recorded in CC (DynamoDB registry + 1PDB `meter_provisioning`); the
station keeps no durable state. The customer‑account link happens later in the CC
commissioning workflow; the gateway auto‑acquires its meter serial from telemetry
once installed and online (CC reconcile fills it in).

## Requirements

- Python 3.9+ (standard library only — no `pip install`).
- The laptop on the **`1Meter` provisioning LAN** (so virgin units are reachable)
  **and** with internet to CC.
- A CC account with `superadmin` or `onm_team` role.

## Run

```bash
python3 provisioning_station.py --cc https://cc.1pwrafrica.com
# optional: --subnet 192.168.4.0/24   --port 8787
```

Then open `http://localhost:8787`.

## Notes / current limitations

- **MAC discovery:** firmware from the mDNS/MAC build reports `pcb_mac` directly
  in `/v1/provision/status` (the station uses it automatically). Older units fall
  back to ping + the OS `arp` table; if the MAC still can't be resolved the unit
  shows "unknown" and can't be selected (the MAC is the registry key). That build
  also advertises mDNS (`_onemeter._tcp`) and uses a per-unit SoftAP SSID
  (`1Meter_<mac3>`) so multiple units can be enumerated without SSID collisions.
- The device must be reachable over the LAN (units join STA `1Meter`/`1Meter00`).
  Provide an AP with that SSID at the bench, or scan each unit's SoftAP one at a
  time (set `--subnet` to the SoftAP range, e.g. `192.168.4.0/24`).
- "Provisioned" means identity + cert + **site** Wi‑Fi are written; the unit
  reaches AWS IoT only once it's on the real site network at install time.

See `Docs/SOP-1meter-operational-ota-provisioning.md` and
`1PWR CC/docs/ops/1meter-provisioning-portal.md` for the full workflow.
