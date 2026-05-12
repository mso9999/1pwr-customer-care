#!/usr/bin/env python3
"""Batch-derive connection/readyboard paid flags from verified fees."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path("/")
ACDB_API = Path(os.environ.get("ACDB_API", "/opt/cc-portal/backend"))
if not (ACDB_API / "onboarding_derive.py").exists():
    ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

from onboarding_derive import derive_payment_steps_for_accounts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("derive_onboarding_steps")


def main() -> int:
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT account_number
            FROM payment_verifications
            WHERE status = 'verified'
              AND payment_type IN ('connection_fee', 'readyboard_fee')
            """
        )
        accounts = [row[0] for row in cur.fetchall()]
        updated = derive_payment_steps_for_accounts(conn, accounts)
        conn.commit()
        log.info("Updated %d commissioning payment steps across %d accounts", updated, len(accounts))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
