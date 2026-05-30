#!/usr/bin/env python3
"""
Verify configured gensite vendor credentials in bulk.

Purpose:
- Run adapter `verify()` across existing `site_credentials` rows for selected vendors.
- Optionally persist the latest verify status back to `site_credentials`.
- Emit CSV/JSON reports for operations tracking.

Typical usage on CC host:
  sudo -u cc_api /opt/cc-portal/backend/venv/bin/python \
    /opt/cc-portal/backend/scripts/ops/run_gensite_adapter_verify.py \
    --vendors victron,sinosoar,sma \
    --write-results \
    --output-csv /tmp/gensite_verify.csv \
    --output-json /tmp/gensite_verify.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gensite import store  # noqa: E402
from gensite.adapters import REGISTRY  # noqa: E402
from gensite.adapters.base import AdapterError  # noqa: E402
from gensite.crypto import CredentialCryptoError, key_is_configured  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_url(args_db_url: str) -> str:
    val = args_db_url or os.environ.get("DATABASE_URL", "")
    if not val:
        raise SystemExit("DATABASE_URL (or --database-url) is required")
    return val


def _parse_vendors(raw: str) -> list[str]:
    vals = [v.strip().lower() for v in raw.split(",") if v.strip()]
    if not vals:
        raise SystemExit("At least one vendor is required")
    unknown = [v for v in vals if v not in REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown vendors: {unknown}. Known: {sorted(REGISTRY.keys())}")
    return vals


def _query_credential_rows(database_url: str, vendors: list[str]) -> list[dict[str, Any]]:
    with psycopg2.connect(database_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.id,
                       c.site_code,
                       c.vendor,
                       c.backend,
                       c.site_id_on_vendor,
                       c.last_verified_at,
                       c.last_verified_ok,
                       c.last_verify_error,
                       s.country,
                       s.display_name
                FROM site_credentials c
                LEFT JOIN sites s ON s.code = c.site_code
                WHERE c.vendor = ANY(%s)
                ORDER BY c.vendor, c.site_code, c.backend
                """,
                (vendors,),
            )
            return [dict(r) for r in cur.fetchall()]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default="")
    ap.add_argument("--vendors", default="victron,sinosoar,sma")
    ap.add_argument("--write-results", action="store_true")
    ap.add_argument("--output-csv", default="")
    ap.add_argument("--output-json", default="")
    args = ap.parse_args()

    if not key_is_configured():
        raise SystemExit("CC_CREDENTIAL_ENCRYPTION_KEY is not configured on this host")

    database_url = _db_url(args.database_url)
    os.environ["DATABASE_URL"] = database_url

    vendors = _parse_vendors(args.vendors)
    rows = _query_credential_rows(database_url, vendors)

    report_rows: list[dict[str, Any]] = []
    for row in rows:
        adapter = REGISTRY[row["vendor"]]
        result_ok = False
        result_msg = ""
        discovered_site_id = None
        discovered_count = 0

        try:
            cred = store.load_credential_for_adapter(
                row["site_code"], row["vendor"], row["backend"]
            )
            if not cred:
                result_ok = False
                result_msg = "credential not found during load"
            else:
                vr = adapter.verify(cred)
                result_ok = bool(vr.ok)
                result_msg = vr.message
                discovered_site_id = vr.discovered_site_id
                discovered_count = len(vr.discovered_equipment or [])
        except CredentialCryptoError as exc:
            result_ok = False
            result_msg = f"decrypt error: {exc}"
        except AdapterError as exc:
            result_ok = False
            result_msg = f"adapter error: {exc}"
        except Exception as exc:  # noqa: BLE001
            result_ok = False
            result_msg = f"unexpected: {exc}"

        if args.write_results:
            store.update_credential_verify_result(
                int(row["id"]),
                ok=result_ok,
                error=None if result_ok else result_msg,
                discovered_site_id=discovered_site_id,
            )

        report_rows.append(
            {
                "verified_utc": _utc_now(),
                "credential_id": row["id"],
                "country": row.get("country") or "",
                "site_code": row["site_code"],
                "site_name": row.get("display_name") or "",
                "vendor": row["vendor"],
                "backend": row["backend"],
                "site_id_on_vendor_before": row.get("site_id_on_vendor") or "",
                "verify_ok": "yes" if result_ok else "no",
                "verify_message": result_msg,
                "discovered_site_id": discovered_site_id or "",
                "discovered_equipment_count": discovered_count,
                "write_results": "yes" if args.write_results else "no",
            }
        )

    payload = {
        "generated_at_utc": _utc_now(),
        "vendors": vendors,
        "write_results": bool(args.write_results),
        "rows": report_rows,
        "totals": {
            "credentials": len(report_rows),
            "ok": sum(1 for r in report_rows if r["verify_ok"] == "yes"),
            "failed": sum(1 for r in report_rows if r["verify_ok"] == "no"),
        },
    }

    if args.output_csv:
        out_csv = Path(args.output_csv).expanduser().resolve()
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(out_csv, report_rows)
    else:
        out_csv = None

    if args.output_json:
        out_json = Path(args.output_json).expanduser().resolve()
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    else:
        out_json = None

    print(
        json.dumps(
            {
                "vendors": vendors,
                "credentials": payload["totals"]["credentials"],
                "ok": payload["totals"]["ok"],
                "failed": payload["totals"]["failed"],
                "write_results": bool(args.write_results),
                "output_csv": str(out_csv) if out_csv else "",
                "output_json": str(out_json) if out_json else "",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

