#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store
from gensite.adapters import REGISTRY
from gensite.adapters.base import SiteEquipment


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test Deye fetch_live for one site.")
    ap.add_argument("--site-code", default="SAM")
    ap.add_argument("--write", action="store_true", help="Insert returned readings into inverter_readings")
    args = ap.parse_args()
    site_code = args.site_code.upper()

    cred = store.load_credential_for_adapter(site_code, "deye", "deyecloud")
    rows = store.list_equipment(site_code)
    eq = [
        SiteEquipment(
            id=int(r["id"]),
            site_code=str(r["site_code"]),
            vendor=str(r["vendor"]),
            kind=str(r["kind"]),
            model=r.get("model"),
            serial=r.get("serial"),
            role=r.get("role"),
        )
        for r in rows
        if r.get("vendor") == "deye"
    ]
    print(f"site={site_code} cred={bool(cred)} equipment={len(eq)}")
    if not cred:
        return 0
    out = REGISTRY["deye"].fetch_live(cred, eq)
    print(f"readings={len(out)}")
    if out:
        r = out[0]
        print(
            f"ts={r.ts_utc.isoformat()} pv_kw={r.pv_kw} ac_kw={r.ac_kw} "
            f"battery_kw={r.battery_kw} soc={r.battery_soc_pct} grid_kw={r.grid_kw}"
        )
        if args.write:
            inserted = store.insert_readings(out)
            print(f"inserted={inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

