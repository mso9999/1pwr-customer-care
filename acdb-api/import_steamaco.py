"""
Import hourly consumption for Steamaco Savi clinic meters (Nimbus AMI API).

Context: a small fleet of 3-phase Steamaco meters serves institutional health
clinics (PIH-invoiced, postpaid) at BOB / KET / LEB / MAN / MET / NKU / TLH.
These meters report hourly to Steamaco's cloud; this job pulls the readings
into 1PDB ``hourly_consumption`` with ``source='steamaco'`` so CC analytics,
coverage audit, and monthly PIH consumption invoicing all read from 1PDB.

API (per the documented Nimbus AMI / DRF-style API):
  POST {base}/api-token-auth/  {username, password}  -> {"token": "..."}
  GET  {base}/meters/?page_size=100                  -> paginated meter list
  GET  {base}/meters/{id}/utilities/1/readings/?start_time=..&end_time=..
       -> {"results": [{"timestamp", "reading", "usage_amount"}, ...]}

Registry contract: meters.platform='steamaco', meters.meter_id = Steamaco
serial (``reference``; leading-zero variants are normalised numerically),
meters.account_number set. Accounts for these meters are postpaid
(accounts.billing_model='postpaid', billing_meter_priority='steamaco').

Env:
  DATABASE_URL            PostgreSQL (defaults to Lesotho local)
  STEAMACO_API_BASE       default https://api.steama.co
  STEAMACO_USERNAME / STEAMACO_PASSWORD   portal credentials (ui.steama.co)
  STEAMACO_TOKEN          optional pre-existing token (skips token POST)
  STEAMACO_TOKEN_PATH     default /api-token-auth/
  STEAMACO_LOOKBACK_DAYS  first-run lookback when no watermark (default 7)

Usage:
  python3 import_steamaco.py             # incremental import
  python3 import_steamaco.py --dry-run   # preview only, no writes
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("steamaco_import")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)
API_BASE = os.environ.get("STEAMACO_API_BASE", "https://api.steama.co").rstrip("/")
TOKEN_PATH = os.environ.get("STEAMACO_TOKEN_PATH", "/api-token-auth/")
SOURCE = "steamaco"
UTILITY_ID = 1  # electricity
PAGE_SIZE = 100


def _norm_serial(serial: str) -> str:
    """Normalise Steamaco serial variants ('0179221230057' vs '179221230057')."""
    digits = "".join(c for c in str(serial or "") if c.isdigit())
    return str(int(digits)) if digits else ""


class SteamacoClient:
    def __init__(self) -> None:
        self.token = os.environ.get("STEAMACO_TOKEN", "").strip()
        self._http = requests.Session()

    def authenticate(self) -> None:
        if self.token:
            return
        user = os.environ.get("STEAMACO_USERNAME", "").strip()
        password = os.environ.get("STEAMACO_PASSWORD", "").strip()
        if not user or not password:
            raise RuntimeError(
                "Steamaco credentials missing: set STEAMACO_TOKEN or "
                "STEAMACO_USERNAME + STEAMACO_PASSWORD"
            )
        resp = self._http.post(
            f"{API_BASE}{TOKEN_PATH}",
            json={"username": user, "password": password},
            timeout=30,
        )
        if resp.status_code == 400:
            raise RuntimeError("Steamaco rejected the portal credentials (400)")
        resp.raise_for_status()
        self.token = resp.json()["token"]
        log.info("Authenticated to Steamaco API (%s)", API_BASE)

    def get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        resp = self._http.get(
            url,
            params=params,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json;charset=utf-8",
            },
            timeout=60,
        )
        if resp.status_code == 401:
            raise RuntimeError("Steamaco 401 — token expired or invalid")
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, params: dict | None = None) -> list[dict]:
        """Collect all pages of a DRF-paginated list endpoint."""
        out: list[dict] = []
        url: str | None = path
        while url:
            data = self.get(url, params)
            out.extend(data.get("results", []))
            url = data.get("next")  # absolute URL or null
            params = None
        return out

    def list_meters(self) -> dict[str, int]:
        """Map normalised serial (reference) -> numeric Steamaco meter id."""
        serial_to_id: dict[str, int] = {}
        for m in self._paged("/meters/", {"page_size": PAGE_SIZE}):
            key = _norm_serial(m.get("reference"))
            if key and m.get("id") is not None:
                serial_to_id[key] = int(m["id"])
        return serial_to_id

    def readings(self, meter_pk: int, start: datetime, end: datetime) -> list[dict]:
        """Hourly readings for one meter in [start, end]."""
        return self._paged(
            f"/meters/{meter_pk}/utilities/{UTILITY_ID}/readings/",
            {"start_time": start.isoformat(), "end_time": end.isoformat()},
        )


def registry_meters(conn) -> list[dict]:
    """Active Steamaco-platform meters from the 1PDB registry."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT meter_id, account_number, community
          FROM meters
         WHERE platform = 'steamaco'
           AND status = 'active'
           AND account_number IS NOT NULL
           AND account_number <> ''
        ORDER BY meter_id
        """
    )
    return [dict(r) for r in cur.fetchall()]


def last_imported_hour(cur, meter_id: str) -> datetime | None:
    cur.execute(
        "SELECT MAX(reading_hour) FROM hourly_consumption "
        "WHERE meter_id = %s AND source = %s",
        (meter_id, SOURCE),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Steamaco clinic-meter readings")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    args = parser.parse_args()

    lookback_days = int(os.environ.get("STEAMACO_LOOKBACK_DAYS", "7"))
    now = datetime.now(timezone.utc)

    conn = psycopg2.connect(DATABASE_URL)
    meters = registry_meters(conn)
    if not meters:
        log.info("No active steamaco-platform meters registered — nothing to do")
        conn.close()
        return 0
    log.info("%d registered Steamaco meter(s) in 1PDB", len(meters))

    client = SteamacoClient()
    client.authenticate()
    serial_to_id = client.list_meters()
    log.info("Steamaco API reports %d meter(s)", len(serial_to_id))

    total_inserted = 0
    cur = conn.cursor()
    for m in meters:
        serial = m["meter_id"]
        key = _norm_serial(serial)
        meter_pk = serial_to_id.get(key)
        if meter_pk is None:
            log.warning("  %s (%s): not found in Steamaco meter list — skipped",
                        serial, m["account_number"])
            continue

        watermark = last_imported_hour(cur, serial)
        start = watermark + timedelta(hours=1) if watermark else now - timedelta(days=lookback_days)
        if start >= now:
            log.info("  %s: up to date", serial)
            continue

        readings = client.readings(meter_pk, start, now)
        batch = []
        for r in readings:
            ts = r.get("timestamp")
            usage = r.get("usage_amount")
            if not ts or usage is None:
                continue
            try:
                hour = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                hour = hour.replace(minute=0, second=0, microsecond=0)
                kwh = float(usage)
            except (ValueError, TypeError):
                continue
            if kwh < 0:
                continue  # anomalous negative delta
            batch.append((m["account_number"], serial, hour, kwh, m["community"], SOURCE))

        # Dedupe within the batch (API can repeat boundary hours)
        seen = set()
        deduped = []
        for row in batch:
            k = (row[1], row[2])
            if k not in seen:
                seen.add(k)
                deduped.append(row)

        if args.dry_run:
            log.info("  %s (%s): %d hourly rows since %s [dry-run]",
                     serial, m["account_number"], len(deduped), start.date())
            continue

        if deduped:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO hourly_consumption
                    (account_number, meter_id, reading_hour, kwh, community, source)
                VALUES (%s, %s, %s, %s, %s, %s::transaction_source)
                ON CONFLICT (meter_id, reading_hour) DO NOTHING
                """,
                deduped,
                page_size=500,
            )
            conn.commit()
            total_inserted += len(deduped)
            log.info("  %s (%s): +%d rows", serial, m["account_number"], len(deduped))

    conn.close()
    log.info("Done. %d rows inserted%s", total_inserted, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
