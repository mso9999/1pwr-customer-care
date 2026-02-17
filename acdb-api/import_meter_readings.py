"""
Import monthly consumption data into tblmonthlyconsumption in the ACCDB.

Three data sources, processed in order:

  1. LOCAL ACCDB — Aggregate existing 10-min interval readings from
     tblmeterdata, tblmeterdata1, tblmeterdatadump into monthly kWh.
  2. Koios (SparkMeter Cloud) — 9 sites: KET, LSB, MAS, MAT, SEH, SHG, TLH, RIB, TOS
  3. ThunderCloud (SparkMeter Parquet) — 1 site: MAK

Target table: tblmonthlyconsumption
  Columns: accountnumber, meterid, yearmonth, kwh, community, source

Usage:
    # Full pipeline (local ACCDB + Koios + ThunderCloud):
    python import_meter_readings.py

    # Only aggregate existing ACCDB meter data tables:
    python import_meter_readings.py --local-only

    # Only external APIs (skip local ACCDB aggregation):
    python import_meter_readings.py --remote-only

    # Specific date range for external APIs:
    python import_meter_readings.py --from 2022-06 --to 2025-12

    # Only Koios sites:
    python import_meter_readings.py --koios-only

    # Only ThunderCloud (MAK):
    python import_meter_readings.py --thundercloud-only

    # Diagnostic: check what's in each table without modifying anything:
    python import_meter_readings.py --check

Environment:
    ACDB_PATH             – Path to .accdb file (auto-detected if not set)
    KOIOS_API_KEY         – Koios API key (has built-in default)
    KOIOS_API_SECRET      – Koios API secret (has built-in default)
    SPARKMETER_EMAIL      – ThunderCloud email (has built-in default)
    SPARKMETER_PASSWORD   – ThunderCloud password (has built-in default)

Runs on the Windows EC2 where the ACCDB is located.
"""

import argparse
import calendar
import csv
import glob
import io
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pyodbc
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("import-meter-readings")

# ---------------------------------------------------------------------------
# ACCDB connection (mirrors customer_api.py logic)
# ---------------------------------------------------------------------------

DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"
DEFAULT_SEARCH_PATHS = [
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\tuacc.accdb",
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\*.accdb",
    r"C:\Users\Administrator\Desktop\*.accdb",
    r".tmp\tuacc.accdb",
]

TABLE_NAME = "tblmonthlyconsumption"


def _find_accdb() -> str:
    env_path = os.environ.get("ACDB_PATH", "")
    if env_path and os.path.isfile(env_path):
        return env_path
    for pattern in DEFAULT_SEARCH_PATHS:
        if "*" in pattern:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        elif os.path.isfile(pattern):
            return pattern
    return ""


def get_accdb_connection(db_path: str) -> pyodbc.Connection:
    conn_str = f"Driver={DRIVER};DBQ={db_path}"
    return pyodbc.connect(conn_str, autocommit=True)


def ensure_table(conn: pyodbc.Connection) -> None:
    """Create tblmonthlyconsumption if it doesn't already exist."""
    cursor = conn.cursor()
    existing = {
        t.table_name.lower()
        for t in cursor.tables(tableType="TABLE")
    }
    if TABLE_NAME.lower() in existing:
        logger.info("Table [%s] already exists", TABLE_NAME)
        return

    logger.info("Creating table [%s]", TABLE_NAME)
    cursor.execute(f"""
        CREATE TABLE [{TABLE_NAME}] (
            id AUTOINCREMENT PRIMARY KEY,
            accountnumber TEXT(20),
            meterid TEXT(80),
            yearmonth TEXT(7),
            kwh DOUBLE,
            community TEXT(10),
            source TEXT(20)
        )
    """)
    cursor.execute(
        f"CREATE INDEX idx_mc_acct_ym ON [{TABLE_NAME}] (accountnumber, yearmonth)"
    )
    cursor.execute(
        f"CREATE INDEX idx_mc_community_ym ON [{TABLE_NAME}] (community, yearmonth)"
    )
    logger.info("Table [%s] created with indexes", TABLE_NAME)


# ---------------------------------------------------------------------------
# Step 1: Aggregate existing ACCDB meter data tables
# ---------------------------------------------------------------------------

ACCDB_METER_TABLES = ["tblmeterdata1", "tblmeterdata", "tblmeterdatadump"]


def check_accdb_tables(conn: pyodbc.Connection) -> None:
    """Diagnostic: report contents of all meter data tables without modifying."""
    cursor = conn.cursor()

    # Meter registry mapping
    meter_to_acct: Dict[str, Tuple[str, str]] = {}
    for meter_table in ["Copy Of tblmeter", "tblmeter"]:
        try:
            cursor.execute(
                f"SELECT [meterid], [accountnumber], [community] FROM [{meter_table}]"
            )
            for row in cursor.fetchall():
                mid = str(row[0] or "").strip()
                acct = str(row[1] or "").strip()
                community = str(row[2] or "").strip()
                if mid and acct:
                    meter_to_acct[mid] = (acct, community)
        except Exception as e:
            logger.warning("  %s: %s", meter_table, e)

    logger.info("Meter registry: %d meter-to-account mappings", len(meter_to_acct))

    for table in ACCDB_METER_TABLES:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
            total = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT COUNT(*) FROM [{table}] WHERE [powerkW] IS NOT NULL"
            )
            non_null = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT COUNT(DISTINCT [meterid]) FROM [{table}] "
                f"WHERE [powerkW] IS NOT NULL"
            )
            unique_meters = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT MIN([whdatetime]), MAX([whdatetime]) FROM [{table}] "
                f"WHERE [powerkW] IS NOT NULL"
            )
            date_row = cursor.fetchone()
            min_dt = str(date_row[0] or "N/A")
            max_dt = str(date_row[1] or "N/A")

            # Sample some meter IDs
            cursor.execute(
                f"SELECT DISTINCT TOP 10 [meterid] FROM [{table}] "
                f"WHERE [powerkW] IS NOT NULL"
            )
            sample_ids = [str(r[0]).strip() for r in cursor.fetchall()]

            # Check how many map to accounts
            mapped = sum(1 for mid in sample_ids if mid in meter_to_acct)

            logger.info("=" * 50)
            logger.info("TABLE: %s", table)
            logger.info("  Total rows: %d", total)
            logger.info("  Rows with powerkW: %d", non_null)
            logger.info("  Unique meters: %d", unique_meters)
            logger.info("  Date range: %s → %s", min_dt, max_dt)
            logger.info("  Sample meter IDs: %s", sample_ids)
            logger.info("  Sample mapped to accounts: %d/%d", mapped, len(sample_ids))
        except Exception as e:
            logger.error("TABLE %s: ERROR — %s", table, e)

    # Also check tblmonthlyconsumption if it exists
    existing = {
        t.table_name.lower() for t in cursor.tables(tableType="TABLE")
    }
    if TABLE_NAME.lower() in existing:
        cursor.execute(f"SELECT COUNT(*) FROM [{TABLE_NAME}]")
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT [source], COUNT(*) AS cnt FROM [{TABLE_NAME}] GROUP BY [source]"
        )
        by_source = {str(r[0]): r[1] for r in cursor.fetchall()}
        logger.info("=" * 50)
        logger.info("TARGET TABLE: %s", TABLE_NAME)
        logger.info("  Total rows: %d", total)
        logger.info("  By source: %s", by_source)
    else:
        logger.info("TARGET TABLE: %s — does not exist yet", TABLE_NAME)


def import_accdb_local(conn: pyodbc.Connection) -> int:
    """Aggregate existing ACCDB meter data tables into tblmonthlyconsumption.

    Reads 10-min powerkW readings from tblmeterdata, tblmeterdata1,
    tblmeterdatadump, converts to monthly kWh per meter, maps meter IDs
    to account numbers via the meter registry, and inserts.

    Uses "first table wins" for duplicate (meterid, yearmonth) pairs
    across tables (tblmeterdata1 processed first as the largest).
    """
    cursor = conn.cursor()

    # Build meterid → (accountnumber, community) from meter registry
    meter_to_acct: Dict[str, Tuple[str, str]] = {}
    for meter_table in ["Copy Of tblmeter", "tblmeter"]:
        try:
            cursor.execute(
                f"SELECT [meterid], [accountnumber], [community] FROM [{meter_table}]"
            )
            for row in cursor.fetchall():
                mid = str(row[0] or "").strip()
                acct = str(row[1] or "").strip()
                community = str(row[2] or "").strip()
                if mid and acct and mid not in meter_to_acct:
                    meter_to_acct[mid] = (acct, community)
        except Exception:
            continue
    logger.info("Meter → account mapping: %d entries", len(meter_to_acct))

    # Aggregate from each table. Key: (meterid, yearmonth) → kwh
    # "First table wins" — skip duplicates from later tables.
    monthly: Dict[Tuple[str, str], float] = {}
    tables_stats: Dict[str, Any] = {}

    for table in ACCDB_METER_TABLES:
        try:
            logger.info("Querying %s (hourly bin → monthly aggregation)...", table)
            # Inner query: average kW per meter per hour (robust to any interval).
            # Outer query: sum hourly averages → monthly kWh
            #   (avg kW in an hour × 1 h = kWh for that hour).
            cursor.execute(
                f"SELECT hourly.meterid, hourly.ym, Sum(hourly.avg_kw) AS kwh "
                f"FROM ("
                f"  SELECT [meterid], "
                f"    Format([whdatetime], 'yyyy-mm') AS ym, "
                f"    Format([whdatetime], 'yyyy-mm-dd hh') AS hour_bin, "
                f"    Avg([powerkW]) AS avg_kw "
                f"  FROM [{table}] "
                f"  WHERE [powerkW] IS NOT NULL "
                f"  GROUP BY [meterid], "
                f"    Format([whdatetime], 'yyyy-mm'), "
                f"    Format([whdatetime], 'yyyy-mm-dd hh')"
                f") AS hourly "
                f"GROUP BY hourly.meterid, hourly.ym"
            )
            new_count = 0
            dup_count = 0
            unique_meters: set = set()
            for row in cursor.fetchall():
                mid = str(row[0] or "").strip()
                ym = str(row[1] or "").strip()
                kwh = float(row[2] or 0)
                if not mid or not ym or kwh <= 0:
                    continue
                unique_meters.add(mid)
                key = (mid, ym)
                if key not in monthly:
                    monthly[key] = kwh
                    new_count += 1
                else:
                    dup_count += 1
            tables_stats[table] = {
                "new": new_count,
                "dup": dup_count,
                "meters": len(unique_meters),
            }
            logger.info(
                "  %s: %d new records, %d duplicates skipped, %d unique meters",
                table, new_count, dup_count, len(unique_meters),
            )
        except Exception as e:
            logger.error("  %s: query failed — %s", table, e)
            tables_stats[table] = {"error": str(e)}

    if not monthly:
        logger.warning("No meter readings found in any ACCDB table")
        return 0

    # Delete existing ACCDB-sourced rows and insert merged results
    cursor.execute(f"DELETE FROM [{TABLE_NAME}] WHERE source = ?", ("accdb",))

    inserted = 0
    all_meters: set = set()
    for (mid, ym), kwh in monthly.items():
        acct, community = meter_to_acct.get(mid, ("", ""))
        if not acct:
            acct = mid
        all_meters.add(mid)
        cursor.execute(
            f"INSERT INTO [{TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh, community, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (acct, mid, ym, kwh, community, "accdb"),
        )
        inserted += 1

    logger.info(
        "ACCDB local total: %d monthly records from %d unique meters",
        inserted, len(all_meters),
    )
    return inserted


# ---------------------------------------------------------------------------
# Step 2: Koios client (minimal, adapted from Email Overlord koios_client.py)
# ---------------------------------------------------------------------------

KOIOS_BASE_URL = "https://www.sparkmeter.cloud"
KOIOS_ORG_ID = "1cddcb07-6647-40aa-aaaa-70d762922029"
KOIOS_DEFAULT_API_KEY = "SGWcnZpgCj-R0fGoVRtjbwMcElV7BvZGz00EEmJDv54"
KOIOS_DEFAULT_API_SECRET = "gJ5gHPsw21W8Jwl&!aId9O5uoywpg#2G"

KOIOS_SITES = {
    "KET": "a075cbc1-e920-455e-9d5a-8595061dfec0",
    "LSB": "ed0766c4-9270-4254-a107-eb4464a96ed9",
    "MAS": "101c443e-6500-4a4d-8cdc-6bd15f4388c8",
    "MAT": "2f7c38b8-4a70-44fd-bf9c-ebf2b2aa78c0",
    "SEH": "0a4fdca5-2d78-4979-8051-10f21a216b16",
    "SHG": "bd7c477d-0742-4056-b75c-38b14ac7cf97",
    "TLH": "db5bf699-31ea-44b6-91c5-1b41e4a2d130",
    "RIB": "10f0846e-d541-4340-81d1-e667cb5026ba",
    "TOS": "b564c8d6-a6c1-43d4-98d1-87ed8cd8ffd7",
}


def _koios_session() -> requests.Session:
    api_key = os.environ.get("KOIOS_API_KEY", KOIOS_DEFAULT_API_KEY)
    api_secret = os.environ.get("KOIOS_API_SECRET", KOIOS_DEFAULT_API_SECRET)
    s = requests.Session()
    s.headers.update({
        "X-API-KEY": api_key,
        "X-API-SECRET": api_secret,
    })
    return s


def fetch_koios_monthly_csv(
    session: requests.Session,
    site_id: str,
    date_str: str,
) -> str:
    """Download Koios monthly readings CSV for one site and month.

    date_str format: YYYY-MM-01
    """
    r = session.get(
        f"{KOIOS_BASE_URL}/api/v2/report",
        params={
            "granularity": "monthly",
            "type": "readings",
            "site_id": site_id,
            "date": date_str,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.text


def parse_koios_meter_details(csv_text: str) -> List[Dict[str, str]]:
    """Parse the METER DETAILS section from a Koios readings CSV."""
    lines = csv_text.strip().split("\n")
    in_meters = False
    header: List[str] = []
    rows: List[Dict[str, str]] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "METER DETAILS":
            in_meters = True
            continue
        if stripped == "SUMMARY":
            in_meters = False
            continue
        if not in_meters:
            continue
        if not header:
            header = [h.strip() for h in stripped.split(",")]
            continue
        vals = stripped.split(",")
        if len(vals) == len(header):
            rows.append(dict(zip(header, [v.strip() for v in vals])))

    return rows


def import_koios_month(
    session: requests.Session,
    conn: pyodbc.Connection,
    site_code: str,
    site_id: str,
    yearmonth: str,
) -> int:
    """Import one month of Koios data for one site. Returns rows inserted."""
    date_str = f"{yearmonth}-01"
    try:
        csv_text = fetch_koios_monthly_csv(session, site_id, date_str)
    except requests.HTTPError as e:
        logger.warning("Koios %s %s: HTTP %s", site_code, yearmonth, e)
        return 0

    meters = parse_koios_meter_details(csv_text)
    if not meters:
        logger.debug("Koios %s %s: no meter details in CSV", site_code, yearmonth)
        return 0

    cursor = conn.cursor()

    # Delete existing rows for this site+month+source to ensure idempotency
    cursor.execute(
        f"DELETE FROM [{TABLE_NAME}] WHERE community = ? AND yearmonth = ? AND source = ?",
        (site_code, yearmonth, "koios"),
    )

    inserted = 0
    for m in meters:
        acct = m.get("customer_code", "").strip()
        meter_serial = m.get("meter_serial", "").strip()
        try:
            kwh = float(m.get("total_energy", "0") or "0")
        except (ValueError, TypeError):
            continue

        if not acct or kwh <= 0:
            continue

        cursor.execute(
            f"INSERT INTO [{TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh, community, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (acct, meter_serial, yearmonth, kwh, site_code, "koios"),
        )
        inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# ThunderCloud client (minimal, adapted from Email Overlord sparkmeter_client.py)
# ---------------------------------------------------------------------------

TC_BASE_URL = "https://opl-location001.sparkmeter.cloud"
TC_SITE_SERIAL = "u740425"
TC_DEFAULT_EMAIL = "makhoalinyane@1pwrafrica.com"
TC_DEFAULT_PASSWORD = "00001111"


class ThunderCloudClient:
    """Minimal ThunderCloud client for fetching monthly per-meter kWh."""

    def __init__(self):
        self.base_url = TC_BASE_URL
        self.email = os.environ.get("SPARKMETER_EMAIL", TC_DEFAULT_EMAIL)
        self.password = os.environ.get("SPARKMETER_PASSWORD", TC_DEFAULT_PASSWORD)
        self.session = requests.Session()
        self._logged_in = False

    def login(self) -> bool:
        if self._logged_in:
            return True
        try:
            r1 = self.session.get(f"{self.base_url}/login", timeout=15)
            csrf_match = re.findall(
                r'name="csrf_token"[^>]*value="([^"]+)"', r1.text
            )
            if not csrf_match:
                logger.error("ThunderCloud: could not find CSRF token")
                return False
            r2 = self.session.post(
                f"{self.base_url}/login",
                data={
                    "csrf_token": csrf_match[0],
                    "email": self.email,
                    "password": self.password,
                    "remember": "y",
                    "next": "",
                },
                timeout=15,
                allow_redirects=True,
            )
            if "/login" not in r2.url:
                self._logged_in = True
                logger.info("ThunderCloud: logged in as %s", self.email)
                return True
            logger.error("ThunderCloud: login failed")
            return False
        except requests.RequestException as e:
            logger.error("ThunderCloud login error: %s", e)
            return False

    def download_day_raw(self, year: int, month: int, day: int) -> Optional[bytes]:
        """Download a single day's Parquet file as raw bytes."""
        if not self.login():
            return None
        from urllib.parse import quote as urlquote
        filename = (
            f"year={year}/month={month:02d}/day={day:02d}/"
            f"{TC_SITE_SERIAL}_{year}_{month:02d}_{day:02d}_reading.parquet"
        )
        url = f"{self.base_url}/history/download/{urlquote(filename, safe='')}"
        try:
            r = self.session.get(url, timeout=60)
            if r.status_code != 200 or len(r.content) < 100:
                return None
            if r.content[:4] != b"PAR1":
                return None
            return r.content
        except requests.RequestException:
            return None

    def get_monthly_per_meter(
        self, year: int, month: int
    ) -> List[Dict[str, Any]]:
        """Download all daily Parquet files for a month, aggregate per meter.

        Returns list of dicts with keys: meter, customer_code, kwh.
        Requires pandas + pyarrow.
        """
        try:
            import pandas as pd
        except ImportError:
            logger.error(
                "pandas is required for ThunderCloud import. "
                "Install with: pip install pandas pyarrow"
            )
            return []

        last_day = calendar.monthrange(year, month)[1]
        all_frames = []

        for day in range(1, last_day + 1):
            raw = self.download_day_raw(year, month, day)
            if raw is None:
                continue
            try:
                df = pd.read_parquet(io.BytesIO(raw))
                all_frames.append(df)
            except Exception as e:
                logger.warning("ThunderCloud %d-%02d-%02d parse error: %s", year, month, day, e)

        if not all_frames:
            return []

        combined = pd.concat(all_frames, ignore_index=True)
        logger.info(
            "ThunderCloud %d-%02d: %d readings, %d meters",
            year, month, len(combined), combined["meter"].nunique(),
        )

        # Determine customer_code column
        code_col = None
        for col in ("snapshot_customer_code", "snapshot_customer_id"):
            if col in combined.columns:
                code_col = col
                break

        # Aggregate kWh per meter
        grouped = combined.groupby("meter").agg(
            kwh=("kilowatt_hours", "sum"),
        ).reset_index()

        results = []
        for _, row in grouped.iterrows():
            meter_id = str(row["meter"])
            kwh = float(row["kwh"])
            # Look up customer code from the raw data
            cust_code = ""
            if code_col:
                mask = combined["meter"] == row["meter"]
                codes = combined.loc[mask, code_col].dropna().unique()
                if len(codes) > 0:
                    cust_code = str(codes[0])
            results.append({
                "meter": meter_id,
                "customer_code": cust_code,
                "kwh": kwh,
            })

        return results


def import_thundercloud_month(
    tc: ThunderCloudClient,
    conn: pyodbc.Connection,
    yearmonth: str,
) -> int:
    """Import one month of ThunderCloud (MAK) data. Returns rows inserted."""
    parts = yearmonth.split("-")
    year, month = int(parts[0]), int(parts[1])

    meter_data = tc.get_monthly_per_meter(year, month)
    if not meter_data:
        logger.debug("ThunderCloud MAK %s: no data", yearmonth)
        return 0

    cursor = conn.cursor()
    cursor.execute(
        f"DELETE FROM [{TABLE_NAME}] WHERE community = ? AND yearmonth = ? AND source = ?",
        ("MAK", yearmonth, "thundercloud"),
    )

    inserted = 0
    for m in meter_data:
        acct = m.get("customer_code", "").strip()
        meter_serial = m.get("meter", "").strip()
        kwh = m.get("kwh", 0.0)

        if not acct or kwh <= 0:
            continue

        cursor.execute(
            f"INSERT INTO [{TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh, community, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (acct, meter_serial, yearmonth, kwh, "MAK", "thundercloud"),
        )
        inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# Month iteration helpers
# ---------------------------------------------------------------------------

def month_range(start: str, end: str) -> List[str]:
    """Generate list of YYYY-MM strings from start to end inclusive."""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import monthly meter readings into ACCDB"
    )
    parser.add_argument(
        "--from", dest="start", default="2019-01",
        help="Start month for external APIs (YYYY-MM), default: 2019-01",
    )
    parser.add_argument(
        "--to", dest="end", default=None,
        help="End month for external APIs (YYYY-MM), default: current month",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Diagnostic: report table contents without modifying anything",
    )
    parser.add_argument(
        "--local-only", action="store_true",
        help="Only aggregate existing ACCDB meter data tables (skip Koios/ThunderCloud)",
    )
    parser.add_argument(
        "--remote-only", action="store_true",
        help="Only fetch from Koios/ThunderCloud (skip ACCDB aggregation)",
    )
    parser.add_argument("--koios-only", action="store_true", help="Skip ThunderCloud")
    parser.add_argument("--thundercloud-only", action="store_true", help="Skip Koios")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without importing")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Connect to ACCDB
    db_path = _find_accdb()
    if not db_path:
        logger.error(
            "No .accdb file found. Set ACDB_PATH environment variable.\n"
            "Searched: %s", "\n  ".join(DEFAULT_SEARCH_PATHS)
        )
        sys.exit(1)
    logger.info("ACCDB: %s", db_path)

    conn = get_accdb_connection(db_path)

    # ── Check mode: report and exit ──
    if args.check:
        check_accdb_tables(conn)
        conn.close()
        return

    ensure_table(conn)

    # Determine date range for external APIs
    if args.end is None:
        now = datetime.now()
        args.end = f"{now.year:04d}-{now.month:02d}"

    months = month_range(args.start, args.end)

    if args.dry_run:
        logger.info("Import range: %s to %s (%d months)", args.start, args.end, len(months))
        if not args.remote_only:
            logger.info("Step 1: ACCDB local aggregation (%s)", ", ".join(ACCDB_METER_TABLES))
        if not args.local_only and not args.thundercloud_only:
            logger.info("Step 2: Koios (%s)", ", ".join(KOIOS_SITES.keys()))
        if not args.local_only and not args.koios_only:
            logger.info("Step 3: ThunderCloud (MAK)")
        return

    total_inserted = 0
    total_errors = 0

    # ── Step 1: Aggregate existing ACCDB meter data tables ──
    if not args.remote_only and not args.koios_only and not args.thundercloud_only:
        logger.info("=" * 60)
        logger.info("STEP 1: Aggregating ACCDB meter data tables")
        logger.info("=" * 60)
        try:
            n = import_accdb_local(conn)
            total_inserted += n
        except Exception as e:
            logger.error("ACCDB local aggregation failed: %s", e)
            total_errors += 1

    if args.local_only:
        conn.close()
        logger.info("=" * 60)
        logger.info("IMPORT COMPLETE (local only)")
        logger.info("  Total rows inserted: %d", total_inserted)
        logger.info("=" * 60)
        return

    logger.info(
        "External API range: %s to %s (%d months)", args.start, args.end, len(months)
    )

    # ── Step 2: Koios import ──
    if not args.thundercloud_only:
        logger.info("=" * 60)
        logger.info("STEP 2: Koios import")
        logger.info("=" * 60)
        koios_session = _koios_session()
        for site_code, site_id in KOIOS_SITES.items():
            consecutive_empty = 0
            site_started = False
            site_total = 0
            for ym in months:
                try:
                    n = import_koios_month(koios_session, conn, site_code, site_id, ym)
                    if n > 0:
                        logger.info("Koios %s %s: %d meters imported", site_code, ym, n)
                        site_started = True
                        consecutive_empty = 0
                        site_total += n
                    else:
                        consecutive_empty += 1
                    total_inserted += n
                except Exception as e:
                    logger.error("Koios %s %s failed: %s", site_code, ym, e)
                    total_errors += 1
                    consecutive_empty += 1

                if not site_started and consecutive_empty >= 6:
                    logger.info(
                        "Koios %s: no data in first %d months, fast-forwarding",
                        site_code, consecutive_empty,
                    )
                    consecutive_empty = 0
                time.sleep(0.3)
            logger.info("Koios %s: %d total rows imported", site_code, site_total)

    # ── Step 3: ThunderCloud import ──
    if not args.koios_only:
        logger.info("=" * 60)
        logger.info("STEP 3: ThunderCloud import (MAK)")
        logger.info("=" * 60)
        tc = ThunderCloudClient()
        consecutive_empty = 0
        tc_started = False
        for ym in months:
            try:
                n = import_thundercloud_month(tc, conn, ym)
                if n > 0:
                    logger.info("ThunderCloud MAK %s: %d meters imported", ym, n)
                    tc_started = True
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                total_inserted += n
            except Exception as e:
                logger.error("ThunderCloud MAK %s failed: %s", ym, e)
                total_errors += 1
                consecutive_empty += 1
            if not tc_started and consecutive_empty >= 6:
                consecutive_empty = 0

    conn.close()

    logger.info("=" * 60)
    logger.info("IMPORT COMPLETE")
    logger.info("  Total rows inserted: %d", total_inserted)
    logger.info("  Errors: %d", total_errors)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
