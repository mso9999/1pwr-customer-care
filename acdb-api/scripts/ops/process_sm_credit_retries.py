#!/usr/bin/env python3
"""Scheduled drain of the CC -> SparkMeter credit retry queue (one country / run).

Why this exists
---------------
``sm_credit_retry.process_due_sm_credit_retries`` is normally only triggered
opportunistically *after a successful credit* on the same backend. A low-volume
backend (Benin) can therefore leave deferred / blocked credits sitting in
``sm_credit_retry_queue`` indefinitely. This runs the same drain on a timer so
queued credits are delivered even when no fresh successful push happens to
trigger the in-process drain.

Benin self-heal (--auto-commission; default ON when COUNTRY_CODE=BN)
-------------------------------------------------------------------
The credit-eligibility guard (sm_credit_retry._account_credit_eligibility)
blocks accounts that aren't ``customer_commissioned`` + ``date_service_connected``
in CC. Benin customers go live on Koios (meter installed + consuming) without
always being commissioned through the CC portal, so their payments get deferred
forever as ``blocked_uncommissioned:customer_not_commissioned`` (RCA 2026-06-26).
The authoritative BN consumption importer lives in the *1PDB* repo
(/opt/1pdb-bn/import_benin_hourly.py), not here, so we self-heal on the CC side:
any BN account (…GBO / …SAM) with recent consumption but ``customer_commissioned
= false`` is marked commissioned (service-connected date = its earliest reading)
before the queue is drained. ``process_due`` then auto-reopens the now-eligible
blocked rows and credits them. Idempotent and safe to run repeatedly.

Run:
  process_sm_credit_retries.py [--limit N] [--no-auto-commission]
Env:
  systemd EnvironmentFile provides DATABASE_URL / KOIOS_* / COUNTRY_CODE. For
  manual runs pass --env-file (parsed literally, systemd-style, so secrets that
  contain '#', ')' or '@' don't break it the way bash ``source`` does).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# Only commission accounts whose meters have reported within this window, so a
# long-decommissioned account is never silently re-commissioned.
LIVE_WINDOW_DAYS = 45

AUTO_COMMISSION_SQL = f"""
WITH live AS (
    SELECT account_number,
           MIN(reading_hour) AS first_reading,
           MAX(reading_hour) AS last_reading
    FROM hourly_consumption
    GROUP BY account_number
)
UPDATE customers c
SET customer_commissioned      = TRUE,
    customer_commissioned_date = COALESCE(c.customer_commissioned_date, live.first_reading),
    date_service_connected     = COALESCE(c.date_service_connected, live.first_reading)
FROM accounts a
JOIN live ON live.account_number = a.account_number
WHERE a.customer_id = c.id
  AND COALESCE(c.customer_commissioned, FALSE) = FALSE
  AND live.last_reading >= NOW() - INTERVAL '{LIVE_WINDOW_DAYS} days'
  AND (a.account_number ~* 'GBO$' OR a.account_number ~* 'SAM$')
RETURNING a.account_number
"""


def _load_env_file_literal(path: str) -> None:
    for line in open(path):
        raw = line.rstrip("\n")
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        os.environ.setdefault(k, v)


def auto_commission_live_bn() -> list[str]:
    """Mark live-but-uncommissioned BN (GBO/SAM) accounts commissioned."""
    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(AUTO_COMMISSION_SQL)
        rows = [r[0] for r in (cur.fetchall() or [])]
        conn.commit()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--env-file", default=os.environ.get("OP_ENV_FILE"))
    ap.add_argument("--no-auto-commission", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL") and args.env_file and os.path.isfile(args.env_file):
        _load_env_file_literal(args.env_file)

    country = os.environ.get("COUNTRY_CODE", "").upper()
    commissioned: list[str] = []
    if country == "BN" and not args.no_auto_commission:
        commissioned = auto_commission_live_bn()
        if commissioned:
            print(json.dumps({"auto_commissioned": commissioned}))

    from sm_credit_retry import process_due_sm_credit_retries

    res = process_due_sm_credit_retries(limit=args.limit)
    res["country"] = country or None
    res["auto_commissioned_count"] = len(commissioned)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
