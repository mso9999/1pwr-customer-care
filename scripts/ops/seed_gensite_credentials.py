#!/usr/bin/env python3
"""
Seed/upsert gensite credentials for a set of site codes.

Runs against the active DATABASE_URL (or --database-url) and writes encrypted
credential rows through gensite.store.upsert_credential.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store  # noqa: E402
from gensite.crypto import key_is_configured  # noqa: E402


def parse_env_file(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def parse_sites(raw: str) -> list[str]:
    vals = [x.strip().upper() for x in raw.split(",") if x.strip()]
    if not vals:
        raise SystemExit("No site codes provided")
    return vals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default="")
    ap.add_argument("--env-file", default="", help="Optional env file to load DATABASE_URL and CC_CREDENTIAL_ENCRYPTION_KEY")
    ap.add_argument("--sites", required=True, help="Comma-separated site codes")
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--username", default="")
    ap.add_argument("--secret", default="")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--site-id-on-vendor", default="")
    ap.add_argument("--created-by", default="ops")
    ap.add_argument("--verify-ok", action="store_true")
    ap.add_argument("--verify-error", default="")
    ap.add_argument(
        "--extra-json",
        default="{}",
        help="JSON object merged into credential extra field (e.g. '{\"appid\":\"...\"}')",
    )
    args = ap.parse_args()

    if args.env_file:
        env_vals = parse_env_file(Path(args.env_file).expanduser().resolve())
        if env_vals.get("DATABASE_URL") and not os.environ.get("DATABASE_URL"):
            os.environ["DATABASE_URL"] = env_vals["DATABASE_URL"]
        if env_vals.get("CC_CREDENTIAL_ENCRYPTION_KEY") and not os.environ.get("CC_CREDENTIAL_ENCRYPTION_KEY"):
            os.environ["CC_CREDENTIAL_ENCRYPTION_KEY"] = env_vals["CC_CREDENTIAL_ENCRYPTION_KEY"]

    database_url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL (or --database-url) is required")
    os.environ["DATABASE_URL"] = database_url

    if not key_is_configured():
        raise SystemExit("CC_CREDENTIAL_ENCRYPTION_KEY is not configured")

    try:
        extra = json.loads(args.extra_json or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--extra-json must be valid JSON object: {exc}") from exc
    if not isinstance(extra, dict):
        raise SystemExit("--extra-json must decode to a JSON object")

    sites = parse_sites(args.sites)
    now = datetime.now(timezone.utc)
    out = []

    for site_code in sites:
        row = store.upsert_credential(
            site_code=site_code,
            vendor=args.vendor.lower(),
            backend=args.backend.lower(),
            base_url=args.base_url or None,
            username=args.username or None,
            secret=args.secret or None,
            api_key=args.api_key or None,
            site_id_on_vendor=args.site_id_on_vendor or None,
            extra=extra,
            created_by=args.created_by,
            verify_ok=bool(args.verify_ok),
            verify_error=args.verify_error or None,
            verify_ts=now,
        )
        out.append(
            {
                "site_code": row.get("site_code", site_code),
                "vendor": row.get("vendor"),
                "backend": row.get("backend"),
                "has_secret": row.get("has_secret"),
                "has_api_key": row.get("has_api_key"),
                "last_verified_ok": row.get("last_verified_ok"),
            }
        )

    print(json.dumps({"seeded": len(out), "rows": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

