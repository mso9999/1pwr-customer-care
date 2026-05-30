#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store
from gensite.adapters import REGISTRY
from gensite.adapters.base import SiteEquipment


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test SMA fetch_day for mapped LS sites.")
    ap.add_argument("--day", default=date.today().isoformat(), help="Local site day (YYYY-MM-DD)")
    args = ap.parse_args()
    day = date.fromisoformat(args.day)

    sites = ["BOB", "MAN", "MET", "NKU"]
    adapter = REGISTRY["sma"]
    for site_code in sites:
        cred = store.load_credential_for_adapter(site_code, "sma", "sunny_portal")
        equipment_rows = store.list_equipment(site_code)
        equipment = [
            SiteEquipment(
                id=int(r["id"]),
                site_code=str(r["site_code"]),
                vendor=str(r["vendor"]),
                kind=str(r["kind"]),
                model=r.get("model"),
                serial=r.get("serial"),
                role=r.get("role"),
            )
            for r in equipment_rows
            if r.get("vendor") == "sma"
        ]
        if not cred:
            print(f"{site_code} cred=missing")
            continue
        rows = adapter.fetch_day(cred, equipment, day)
        if not rows:
            print(f"{site_code} rows=0")
            continue
        first = rows[0]
        last = rows[-1]
        print(
            f"{site_code} rows={len(rows)} "
            f"first={first.ts_utc.isoformat()} pv_kw={first.pv_kw} ac_kw={first.ac_kw} "
            f"last={last.ts_utc.isoformat()} pv_kw={last.pv_kw} ac_kw={last.ac_kw}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

