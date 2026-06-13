#!/usr/bin/env python3
"""
Apply O&M receipt→account mappings from no_reference_payments_om.csv to the
merchant_unmatched_payments queue.

Uses the same claim path as the portal (suffix dedup; no double-credit).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_API = _REPO / "acdb-api"
if not (_API / "customer_api.py").exists():
    _API = _REPO  # deployed layout: backend/ is the API root
sys.path.insert(0, str(_API))

_ACCOUNT_RE = re.compile(r"\b(\d{4}[A-Z]{2,4})\b")
_NOT_FOUND_RE = re.compile(r"^not\s*found", re.I)


def _normalize_account(raw: str) -> str | None:
    s = (raw or "").strip().upper()
    if not s or _NOT_FOUND_RE.match(s):
        return None
    m = _ACCOUNT_RE.search(s.replace(" ", ""))
    if m:
        return m.group(1).upper()
    # e.g. 007MAK → try padding to 0074MAK won't work; O&M usually gives full ID
    return None


def _load_mappings(path: Path) -> dict[str, str]:
    """receipt (lower) -> account"""
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            receipt = (row.get("receipt") or "").strip()
            acct_raw = row.get("Customer ID") or row.get("customer id") or ""
            acct = _normalize_account(acct_raw)
            if receipt and acct:
                out[receipt.lower()] = acct
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "csv",
        nargs="?",
        default=str(_REPO / "docs/ops/merchant-unmatched-2026-06/no_reference_payments_om.csv"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    mappings = _load_mappings(Path(args.csv))
    print(f"Loaded {len(mappings)} receipt→account mappings from CSV")

    from customer_api import get_connection
    from merchant_unmatched import claim_unmatched_row

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, lower(receipt), resolved_at
            FROM merchant_unmatched_payments
            WHERE category = 'customer'
            """
        )
        rows = {r[1]: (r[0], r[2]) for r in cur.fetchall()}

        cur.execute("SELECT account_number FROM accounts")
        valid_accounts = {r[0].upper() for r in cur.fetchall()}

        to_claim: list[tuple[int, str, str]] = []
        skipped_resolved = []
        skipped_no_mapping = []
        skipped_bad_account: list[tuple[str, str]] = []
        skipped_not_in_queue = []

        cur.execute(
            """
            SELECT id, lower(receipt)
            FROM merchant_unmatched_payments
            WHERE resolved_at IS NULL AND category = 'customer'
            """
        )
        open_rows = {r[1]: r[0] for r in cur.fetchall()}

        for receipt_l, account in sorted(mappings.items()):
            if receipt_l not in rows:
                skipped_not_in_queue.append(receipt_l)
                continue
            row_id, resolved_at = rows[receipt_l]
            if resolved_at is not None:
                skipped_resolved.append(receipt_l)
                continue
            if account not in valid_accounts:
                skipped_bad_account.append((receipt_l, account))
                continue
            to_claim.append((row_id, receipt_l, account))

        for receipt_l in open_rows:
            if receipt_l not in mappings:
                skipped_no_mapping.append(receipt_l)

        print(f"\nOpen in queue: {len(open_rows)}")
        print(f"Will claim: {len(to_claim)}")
        print(f"Already resolved (mapped): {len(skipped_resolved)}")
        print(f"Open, no O&M mapping: {len(skipped_no_mapping)}")
        print(f"Bad account (not in CC): {len(skipped_bad_account)}")
        print(f"Mapped but never in queue: {len(skipped_not_in_queue)}")

        if skipped_bad_account:
            print("\n--- Bad / missing accounts ---")
            for r, a in skipped_bad_account:
                print(f"  {r} -> {a}")

        if skipped_no_mapping:
            print("\n--- Still open, no mapping ---")
            for r in skipped_no_mapping:
                print(f"  {r}")

        if to_claim:
            print("\n--- Claims ---")
            for row_id, receipt_l, account in to_claim:
                print(f"  {receipt_l} -> {account}")

        if not args.apply:
            print("\n(dry-run; pass --apply to execute)")
            return 0

        booked = 0
        skipped_dup = 0
        errors = []
        for row_id, receipt_l, account in to_claim:
            try:
                result = claim_unmatched_row(conn, row_id, account)
                if result.get("skipped"):
                    skipped_dup += 1
                else:
                    booked += 1
            except Exception as exc:
                errors.append((receipt_l, str(exc)))
        conn.commit()

        print(f"\nAPPLIED: booked={booked} skipped_dup={skipped_dup} errors={len(errors)}")
        for r, e in errors:
            print(f"  ERROR {r}: {e}")

        cur.execute(
            """
            SELECT count(*), COALESCE(sum(amount), 0)
            FROM merchant_unmatched_payments
            WHERE resolved_at IS NULL AND category = 'customer'
            """
        )
        rem_count, rem_total = cur.fetchone()
        print(f"Remaining open: {rem_count} (M{float(rem_total):.2f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
