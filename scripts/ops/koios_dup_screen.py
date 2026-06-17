#!/usr/bin/env python3
"""Screen Koios /api/v2/report for duplicate-heartbeat inflation (RCA 2026-06-17).

Koios intermittently returns each 15-min heartbeat duplicated N times, which the
hourly importers summed -> inflated consumption -> over-debited balances. This
read-only tool samples each koios site per month and reports the raw/dedup ratio
so we can pinpoint the affected (site, month) set before remediation.

Usage:
    python3 koios_dup_screen.py 2025-09 2026-06
"""
import csv, io, sys, urllib.request, urllib.parse
from datetime import date

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
SAMPLE_DAYS = (5, 15, 25)


def load_env(path):
    e = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def day_ratio(env, site_id, d):
    qs = urllib.parse.urlencode({
        "granularity": "daily", "type": "readings", "site_id": site_id, "date": d,
    })
    url = env.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud") + "/api/v2/report?" + qs
    req = urllib.request.Request(url, headers={
        "X-API-KEY": env["KOIOS_API_KEY"], "X-API-SECRET": env["KOIOS_API_SECRET"]})
    try:
        raw = urllib.request.urlopen(req, timeout=180).read().decode()
    except urllib.error.HTTPError as ex:
        return None if ex.code == 404 else f"ERR{ex.code}"
    except Exception as ex:
        return f"ERR:{ex}"
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return None
    raw_sum = 0.0
    dedup = {}
    for r in rows:
        try:
            k = float(r.get("kilowatt_hours") or 0)
        except (ValueError, TypeError):
            k = 0.0
        raw_sum += k
        dedup[(r.get("meter/serial"), r.get("heartbeat_start"))] = k
    ds = sum(dedup.values())
    return (raw_sum, ds, raw_sum / ds if ds else 1.0)


def months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m = 1; y += 1


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-09"
    end = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y-%m")
    print(f"Screening {start}..{end}  (sample days {SAMPLE_DAYS})\n")
    print(f"{'CTY':3} {'SITE':4} {'MONTH':7} {'maxRatio':>8} {'meanRatio':>9}  flag")
    affected = []
    for cty, sites in SITES.items():
        env = load_env(ENVS[cty])
        for site, sid in sites.items():
            for mon in months(start, end):
                ratios = []
                for dd in SAMPLE_DAYS:
                    res = day_ratio(env, sid, f"{mon}-{dd:02d}")
                    if isinstance(res, tuple):
                        ratios.append(res[2])
                if not ratios:
                    continue
                mx, mn = max(ratios), sum(ratios) / len(ratios)
                flag = "INFLATED" if mx > 1.05 else ""
                if flag:
                    affected.append((cty, site, mon, round(mx, 2)))
                print(f"{cty:3} {site:4} {mon:7} {mx:8.2f} {mn:9.2f}  {flag}")
    print("\n=== AFFECTED (site, month, maxRatio) ===")
    for a in affected:
        print(" ", a)
    if not affected:
        print("  none")


if __name__ == "__main__":
    main()
