#!/usr/bin/env python3
"""
Backfill legacy CC `tickets` rows into the OM ticket store (source of truth).

Phase 3 of the OM portal refactor. Purely HTTP-based (avoids the CC backend's
circular imports): reads the legacy table via GET /api/tickets and re-creates
each row in the OM store via POST /api/om-tickets (the proxy translates to the
OM schema and sets ticket_class). Run while the CC service is still in
OM_TICKETS_SOURCE=legacy so both endpoints are live.

    CC_TOKEN=<employee JWT> python backfill_legacy_tickets_to_om.py --dry-run
    CC_TOKEN=<employee JWT> python backfill_legacy_tickets_to_om.py --apply

Get a token: POST /api/auth/employee-login {employee_id, password:<monthly PIN>}.
Idempotent-ish: skips a legacy row if an OM ticket with the same site +
fault_description prefix already exists.
"""

import argparse
import os
import sys

import requests

BASE = os.environ.get("CC_BASE_URL", "https://cc.1pwrafrica.com").rstrip("/")
TOKEN = os.environ.get("CC_TOKEN", "")


def _headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run
    if not TOKEN:
        print("ERROR: set CC_TOKEN to an employee JWT", file=sys.stderr)
        return 2

    legacy = requests.get(f"{BASE}/api/tickets", params={"limit": 500},
                          headers=_headers(), timeout=30)
    legacy.raise_for_status()
    rows = legacy.json().get("tickets", [])
    print(f"Legacy tickets found: {len(rows)}")

    om = requests.get(f"{BASE}/api/om-tickets", params={"limit": 500},
                      headers=_headers(), timeout=60)
    om.raise_for_status()
    existing = {
        ((t.get("site_code") or "").strip(), (t.get("fault_description") or "").strip()[:80])
        for t in om.json().get("tickets", [])
    }

    created = skipped = failed = 0
    for row in rows:
        body = {
            "site_code": row.get("site_code"),
            "account_number": row.get("account_number"),
            "category": row.get("category"),
            "fault_description": row.get("fault_description"),
            "ticket_name": row.get("ticket_name"),
            "priority": row.get("priority"),
            "reported_by": row.get("reported_by"),
            "services_affected": row.get("services_affected"),
            "phone": row.get("phone"),
        }
        key = ((body.get("site_code") or "").strip(),
               (body.get("fault_description") or "").strip()[:80])
        klass = "customer_grievance" if body.get("account_number") else "asset_fault"
        if key in existing:
            skipped += 1
            print(f"  skip (dup): legacy {row.get('id')} | {(body.get('fault_description') or '')[:50]}")
            continue
        if not apply:
            created += 1
            print(f"  would create: legacy {row.get('id')} -> {klass} | {(body.get('fault_description') or '')[:50]}")
            continue
        try:
            r = requests.post(f"{BASE}/api/om-tickets", json=body, headers=_headers(), timeout=30)
            r.raise_for_status()
            created += 1
            existing.add(key)
            print(f"  created from legacy {row.get('id')} -> {klass}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            detail = getattr(getattr(exc, "response", None), "text", "")
            print(f"  FAIL legacy {row.get('id')}: {exc} {detail[:200]}")

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: created={created} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
