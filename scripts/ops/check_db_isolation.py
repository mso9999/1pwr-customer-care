#!/usr/bin/env python3
"""Cross-country data-isolation check (read-only).

Fails if a country's database holds accounts whose site code belongs to ANOTHER
registered country — the cross-DB pollution signature from the 2026-06 BN mirror
incident (see docs/sop-add-new-country.md "Cross-country data isolation").

Runs per database discovered in the env file:
  - DATABASE_URL        -> COUNTRY_CODE (default LS)
  - DATABASE_URL_{CC}   -> {CC}   (e.g. DATABASE_URL_BN, DATABASE_URL_ZM)

Classification per DB:
  - cross_country: account site is owned by a DIFFERENT registered country  -> CRITICAL
                   (with --check this exits 1 so the timer surfaces it)
  - unknown_site:  account site is not in any country's site_abbrev         -> warn only
                   (legacy/test codes like BVW/LAB; reported, not fatal)

Usage:
    python3 check_db_isolation.py            # report all DBs
    python3 check_db_isolation.py --check    # exit 1 if any cross-country pollution
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg2

HERE = Path(__file__).resolve()
for _root in (HERE.parents[2] if len(HERE.parents) >= 3 else HERE.parent, Path("/opt/cc-portal/backend")):
    _acdb = _root / "acdb-api"
    if _acdb.exists() and str(_acdb) not in sys.path:
        sys.path.insert(0, str(_acdb))
    if _root.exists() and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from country_config import _REGISTRY, get_country_for_site  # noqa: E402

SITE_RE = re.compile(r"([A-Z]{2,4})$")


def _parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _site_of(account: str) -> str:
    m = SITE_RE.search((account or "").upper())
    return m.group(1) if m else ""


def check_db(db_url: str, cc: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return (cross_country_by_owner, unknown_site_accounts) for one DB."""
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT account_number FROM transactions")
        cross: dict[str, list[str]] = {}
        unknown: list[str] = []
        for (acct,) in cur.fetchall():
            owner = get_country_for_site(_site_of(acct))
            if owner and owner != cc:
                cross.setdefault(owner, []).append(str(acct))
            elif not owner:
                unknown.append(str(acct))
        return cross, unknown
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default="/opt/1pdb/.env")
    ap.add_argument("--check", action="store_true", help="Exit 1 if any cross-country pollution found")
    args = ap.parse_args()

    vals = _parse_env_file(args.env_file)
    jobs: list[tuple[str, str]] = []
    seen: set[str] = set()
    if vals.get("DATABASE_URL"):
        cc = (vals.get("COUNTRY_CODE", "LS") or "LS").upper()
        jobs.append((cc, vals["DATABASE_URL"]))
        seen.add(vals["DATABASE_URL"])
    for cc in _REGISTRY:
        url = vals.get(f"DATABASE_URL_{cc}")
        if url and url not in seen:
            jobs.append((cc, url))
            seen.add(url)
    if not jobs:
        raise SystemExit("No database URLs found in env file")

    polluted = 0
    for cc, db_url in jobs:
        try:
            cross, unknown = check_db(db_url, cc)
        except Exception as e:
            print(f"[{cc}] ERROR: {e}")
            polluted += 1
            continue
        n_cross = sum(len(v) for v in cross.values())
        print(f"[{cc}] cross_country_accts={n_cross}  unknown_site_accts={len(unknown)}")
        for owner, accts in sorted(cross.items()):
            print(f"   !! {len(accts)} {owner}-site accounts in {cc} DB: e.g. {', '.join(accts[:8])}")
        if unknown:
            print(f"   ~ unknown/legacy sites (warn): {len(unknown)} e.g. {', '.join(unknown[:8])}")
        if n_cross:
            polluted += 1

    if polluted and args.check:
        print(f"ISOLATION FAILED: {polluted} database(s) with cross-country pollution")
        return 1
    print("OK: no cross-country pollution detected" if not polluted else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
