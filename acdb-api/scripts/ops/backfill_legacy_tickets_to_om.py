#!/usr/bin/env python3
"""
Backfill legacy CC `tickets` rows into the OM ticket store (source of truth).

Phase 3 of the OM portal refactor. Reads every row from the legacy `tickets`
table, maps it to the OM ticket schema (via om_tickets._cc_to_om_create), and
POSTs it to the OM store through the same proxy the app uses. Idempotent-ish:
skips rows whose fault_description already appears in the OM store for the same
site (best-effort — the OM store keys on its own generated ticket_id).

Run ON THE CC HOST (needs the same env as the API: OM_TICKETS_BASE_URL,
OM_TICKETS_API_KEY):

    cd /opt/cc-portal/backend
    OM_TICKETS_SOURCE=legacy venv/bin/python scripts/ops/backfill_legacy_tickets_to_om.py --dry-run
    OM_TICKETS_SOURCE=legacy venv/bin/python scripts/ops/backfill_legacy_tickets_to_om.py --apply

Keep OM_TICKETS_SOURCE=legacy while running so the proxy's create path talks to
OM but the legacy table stays readable. Flip the live service to
OM_TICKETS_SOURCE=om only AFTER this reports success.
"""

import argparse
import sys
from types import SimpleNamespace

import tickets as legacy
import om_tickets as proxy


def _svc_user():
    # A minimal CurrentUser-like object for the proxy/legacy calls.
    return SimpleNamespace(user_id="backfill", role="superadmin", name="Backfill Script")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually create OM tickets")
    ap.add_argument("--dry-run", action="store_true", help="report only (default)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    user = _svc_user()
    listing = legacy.list_tickets(limit=500, offset=0, site_code=None,
                                  account_number=None, status=None, search=None, user=user)
    rows = listing.get("tickets", []) if isinstance(listing, dict) else []
    print(f"Legacy tickets found: {len(rows)}")

    # Snapshot existing OM tickets (fault_description+site) for a crude dedupe.
    existing = set()
    try:
        mapped = proxy._fetch_om_tickets_mapped(user, site_code=None, status=None)
        for t in mapped:
            existing.add(((t.get("site_code") or "").strip(),
                          (t.get("fault_description") or "").strip()[:80]))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: could not snapshot OM store for dedupe: {exc}")

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
        if key in existing:
            skipped += 1
            print(f"  skip (dup): {row.get('id')} {body.get('fault_description', '')[:50]}")
            continue

        if not apply:
            created += 1
            print(f"  would create: {row.get('id')} -> "
                  f"class={'customer_grievance' if body.get('account_number') else 'asset_fault'} "
                  f"| {body.get('fault_description', '')[:50]}")
            continue

        try:
            proxy.create_om_ticket(body=body, user=user)
            created += 1
            existing.add(key)
            print(f"  created from legacy {row.get('id')}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL legacy {row.get('id')}: {exc}")

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: "
          f"created={created} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
