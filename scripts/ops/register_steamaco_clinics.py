#!/usr/bin/env python3
"""
Register the Steamaco clinic meters + their customer accounts in 1PDB.

One-off registry bootstrap for the PIH health-clinic fleet (3-phase Steamaco
Savi meters). For each clinic it creates:
  - customers row (customer_type='HC', community=site)
  - accounts row (next_account_number(site), billing_model='postpaid',
    billing_meter_priority='steamaco')
  - meters row (meter_id=Steamaco serial, platform='steamaco',
    role='primary', status='active')

Deliberately writes the DB directly: NO SparkMeter sync (these accounts have
no SM counterpart by design) and no fee-debt seeding (postpaid — PIH is
invoiced monthly; there is no customer-facing connection fee flow).

Idempotent: skips serials already present in meters. Default is DRY-RUN;
pass --apply to write.

Run on the CC host:
  cd /opt/cc-portal/backend && PYTHONPATH=. \
    python3 ../scripts/ops/register_steamaco_clinics.py --apply
(or locally with DATABASE_URL pointed at 1PDB-LS)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "acdb-api"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("register-steamaco-clinics")

# (serial, site, display name, district)
# Names are the Steamaco-platform customer labels from the weekly exports.
# 0179221230065 had no customer label in the exports — NKU attribution is by
# elimination (the NKU weekly file); CONFIRM before applying.
CLINICS = [
    ("0179221230032", "MAN", "Manamaneng Health Centre"),
    ("0179221230040", "MET", "Methalananeng Health Centre"),
    ("0179221230057", "KET", "Ketane Health Centre"),
    ("0179221230065", "NKU", "Ha Nkau Health Centre"),   # confirm name/serial pairing
    ("0179221230107", "BOB", "Bobete Health Centre"),
    ("0179221230123", "LEB", "Lebakeng Health Centre"),
    ("0179221230131", "TLH", "Tlhanyaku Health Centre"),
]

CREATED_BY = "steamaco-registry"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write to the DB (default: dry-run)")
    args = ap.parse_args()

    os.environ.setdefault("DATABASE_URL", "postgresql://cc_api@localhost:5432/onepower_cc")
    from customer_api import get_connection  # noqa: E402 — needs repo path

    with get_connection() as conn:
        cur = conn.cursor()
        for serial, site, name in CLINICS:
            cur.execute("SELECT 1 FROM meters WHERE meter_id = %s", (serial,))
            if cur.fetchone():
                log.info("SKIP %s — meter %s already registered", site, serial)
                continue

            cur.execute("SELECT next_account_number(%s)", (site,))
            account = str(cur.fetchone()[0]).strip().upper()

            if not args.apply:
                # next_account_number() is a pure MAX()-based read — no
                # sequence is consumed by previewing it here.
                log.info("DRY  %s -> %s  %s  (serial %s)", site, account, name, serial)
                continue

            first, _, last = name.partition(" ")
            cur.execute(
                """
                INSERT INTO customers (
                    first_name, last_name, community, country, customer_type,
                    is_active, created_by, updated_by
                ) VALUES (%s, %s, %s, 'Lesotho', 'HC', TRUE, %s, %s)
                RETURNING id
                """,
                (first, last or name, site, CREATED_BY, CREATED_BY),
            )
            customer_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO accounts (
                    account_number, customer_id, meter_id, community,
                    account_sequence, created_by, billing_model, billing_meter_priority
                ) VALUES (%s, %s, %s, %s, %s, %s, 'postpaid', 'steamaco')
                """,
                (account, customer_id, serial, site, int(account[:4]), CREATED_BY),
            )

            cur.execute(
                """
                INSERT INTO meters (
                    meter_id, account_number, community, platform, role, status
                ) VALUES (%s, %s, %s, 'steamaco', 'primary', 'active')
                """,
                (serial, account, site),
            )
            conn.commit()
            log.info("OK   %s -> %s  %s  (customer_id=%s)", site, account, name, customer_id)

    log.info("Done (%s).", "applied" if args.apply else "dry-run — pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
