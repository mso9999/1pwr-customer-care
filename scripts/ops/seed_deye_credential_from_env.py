#!/usr/bin/env python3
"""
Seed a Deye credential row from an env file for a target site.

Required env keys in --env-file:
  - DATABASE_URL
  - CC_CREDENTIAL_ENCRYPTION_KEY
  - DEYE_APP_ID
  - DEYE_APP_SECRET
  - DEYE_EMAIL
  - DEYE_PASSWORD
Optional:
  - DEYE_COMPANY_ID
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store  # noqa: E402
from gensite.crypto import key_is_configured  # noqa: E402


def parse_env(path: Path) -> Dict[str, str]:
    vals: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        vals[k] = v
    return vals


def require(vals: Dict[str, str], key: str) -> str:
    v = vals.get(key, "").strip()
    if not v:
        raise SystemExit(f"Missing required env key: {key}")
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--site-code", required=True)
    ap.add_argument("--device-sn", required=True, help="Deye inverter SN for site_id_on_vendor")
    ap.add_argument("--created-by", default="ops")
    args = ap.parse_args()

    vals = parse_env(Path(args.env_file).expanduser().resolve())

    os.environ["DATABASE_URL"] = require(vals, "DATABASE_URL")
    os.environ["CC_CREDENTIAL_ENCRYPTION_KEY"] = require(vals, "CC_CREDENTIAL_ENCRYPTION_KEY")
    if not key_is_configured():
        raise SystemExit("CC_CREDENTIAL_ENCRYPTION_KEY not configured after env load")

    app_id = require(vals, "DEYE_APP_ID")
    app_secret = require(vals, "DEYE_APP_SECRET")
    email = require(vals, "DEYE_EMAIL")
    password = require(vals, "DEYE_PASSWORD")
    company_id = vals.get("DEYE_COMPANY_ID", "").strip()

    extra = {"appid": app_id}
    if company_id:
        extra["companyid"] = company_id

    row = store.upsert_credential(
        site_code=args.site_code.upper(),
        vendor="deye",
        backend="deyecloud",
        base_url="https://eu1-developer.deyecloud.com",
        username=email,
        secret=password,
        api_key=app_secret,
        site_id_on_vendor=args.device_sn.strip(),
        extra=extra,
        created_by=args.created_by,
        verify_ok=False,
        verify_error="seeded from env for Deye verify",
        verify_ts=datetime.now(timezone.utc),
    )
    print(
        {
            "site_code": row.get("site_code"),
            "vendor": row.get("vendor"),
            "backend": row.get("backend"),
            "site_id_on_vendor": row.get("site_id_on_vendor"),
            "has_secret": row.get("has_secret"),
            "has_api_key": row.get("has_api_key"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

