#!/usr/bin/env python3
"""
Backfill real-time SMS payments that resolved to no account into the Unmatched
Payments match list (``merchant_unmatched_payments``).

CONTEXT (RCA 2026-06-16)
  Every site phone runs the SMS gateway app and forwards to one of the two
  mirrors (electricity + fee/finance). When CC's live ingest can't resolve an
  account, it wrote ``sms_inbound_log.outcome='no_account'`` and dropped the
  payment — it never reached the match list. The match list was therefore fed
  only by the monthly merchant-statement import, so some real-time payments were
  invisible (money received, customer uncredited, no O&M visibility).

  ingest.py is now fixed to park these going forward. This one-off backfills the
  historical ``no_account`` rows that are NOT already booked and NOT already in
  the match list.

USAGE (on the server, where DATABASE_URL + acdb-api modules live)
  PYTHONPATH=/opt/cc-portal/backend \
    /opt/cc-portal/backend/venv/bin/python \
    scripts/ops/backfill_sms_no_account_to_match_list.py            # dry-run
  ... --apply                                                       # write
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2

# Distinct no_account receipts that are neither booked nor already parked.
SELECT_LOST = """
    WITH na AS (
        SELECT
            receipt_key,
            max(amount)                                   AS amount,
            max(received_at)                              AS paid_at,
            (array_agg(content       ORDER BY received_at DESC))[1] AS content,
            (array_agg(parse_result  ORDER BY received_at DESC))[1] AS parse_result
        FROM sms_inbound_log
        WHERE outcome = 'no_account'
          AND receipt_key IS NOT NULL AND receipt_key <> ''
        GROUP BY receipt_key
    )
    SELECT receipt_key, amount, paid_at, content, parse_result
    FROM na
    WHERE NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE lower(trim(t.payment_reference)) = lower(trim(na.receipt_key))
          )
      AND NOT EXISTS (
            SELECT 1 FROM merchant_unmatched_payments m
            WHERE lower(m.receipt) = lower(na.receipt_key)
          )
    ORDER BY paid_at
"""


def _phone_from(parse_result, content: str) -> str:
    if parse_result:
        try:
            data = parse_result if isinstance(parse_result, dict) else json.loads(parse_result)
            ph = "".join(c for c in str(data.get("phone") or "") if c.isdigit())
            if ph:
                return ph
        except (ValueError, TypeError):
            pass
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Write to merchant_unmatched_payments")
    args = ap.parse_args()

    db = os.environ.get("DATABASE_URL", "").strip()
    if not db:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        return 2

    from merchant_unmatched import park_unmatched_payment

    conn = psycopg2.connect(db)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(SELECT_LOST)
    rows = cur.fetchall()
    print(f"Found {len(rows)} lost no_account payments (not booked, not in match list)")

    parked = skipped = 0
    total = 0.0
    for receipt_key, amount, paid_at, content, parse_result in rows:
        provider = "ecocash" if "ecocash" in (content or "").lower() else "mpesa"
        phone = _phone_from(parse_result, content or "")
        total += float(amount or 0)
        if not args.apply:
            print(f"  WOULD PARK {receipt_key} M{amount} {paid_at:%Y-%m-%d} phone={phone or '?'}")
            continue
        try:
            if park_unmatched_payment(
                conn,
                receipt=receipt_key,
                amount=float(amount),
                paid_at=paid_at,
                reference_text=content or "",
                payer_phone=phone,
                provider=provider,
                source_file="sms_gateway_backfill",
            ):
                parked += 1
            else:
                skipped += 1
        except Exception as exc:
            conn.rollback()
            print(f"  ERROR parking {receipt_key}: {exc}", file=sys.stderr)

    if args.apply:
        conn.commit()
        print(f"APPLIED: parked={parked} skipped_existing={skipped} value=M{total:.2f}")
    else:
        print(f"DRY-RUN: would park {len(rows)} payments, value=M{total:.2f}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
