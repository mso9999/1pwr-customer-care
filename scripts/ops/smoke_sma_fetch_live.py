#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store
from gensite.adapters import REGISTRY
from gensite.adapters.base import SiteEquipment


def main() -> int:
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
        ]
        rows = adapter.fetch_live(cred, equipment) if cred else []
        if rows:
            row = rows[0]
            print(
                f"{site_code} rows={len(rows)} "
                f"ts={row.ts_utc.isoformat()} pv_kw={row.pv_kw} "
                f"ac_kw={row.ac_kw} soc={row.battery_soc_pct}"
            )
        else:
            print(f"{site_code} rows=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

