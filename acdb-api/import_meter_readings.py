"""
Import monthly consumption AND transaction data into the ACCDB.

Populates two tables:

  tblmonthlyconsumption  — per-meter monthly kWh consumed (meter readings)
  tblmonthlytransactions — per-meter monthly kWh vended + LSL paid (payment records)

Data sources (processed in order):

  1. LOCAL ACCDB — Stream powerkW readings from tblmeterdata,
     tblmeterdata1, tblmeterdatadump; bin to hourly avg kW; sum to
     monthly kWh. Processing done in Python (not Jet SQL) for speed.
  2. Koios (SparkMeter Cloud) — 9 sites: KET, LSB, MAS, MAT, SEH, SHG, TLH, RIB, TOS
     - Readings CSV: per-meter kWh consumed
     - Payments v1 API: per-customer payment records (kWh vended + LSL amount)
  3. ThunderCloud (SparkMeter Parquet) — 1 site: MAK
     - Parquet files: kWh consumed + cost per reading

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

    # Only transaction/payment import (no consumption):
    python import_meter_readings.py --transactions-only

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
TXN_TABLE_NAME = "tblmonthlytransactions"

# Portfolio payment CSV paths (SparkMeter exports on Dropbox)
# These are the authoritative transaction records, including manual corrections
PORTFOLIO_CSV_SEARCH_PATHS = [
    os.path.join(
        os.path.expanduser("~"), "Dropbox", "1PWR", "1PWR OM TEAM",
        "22. Raw Data", "1. Mini Grids", "UNCLEANED", "PURCHASES",
    ),
]


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


def ensure_txn_table(conn: pyodbc.Connection) -> None:
    """Create tblmonthlytransactions if it doesn't already exist."""
    cursor = conn.cursor()
    existing = {
        t.table_name.lower()
        for t in cursor.tables(tableType="TABLE")
    }
    if TXN_TABLE_NAME.lower() in existing:
        logger.info("Table [%s] already exists", TXN_TABLE_NAME)
        return

    logger.info("Creating table [%s]", TXN_TABLE_NAME)
    cursor.execute(f"""
        CREATE TABLE [{TXN_TABLE_NAME}] (
            id AUTOINCREMENT PRIMARY KEY,
            accountnumber TEXT(20),
            meterid TEXT(80),
            yearmonth TEXT(7),
            kwh_vended DOUBLE,
            amount_lsl DOUBLE,
            txn_count LONG,
            community TEXT(10),
            source TEXT(20)
        )
    """)
    cursor.execute(
        f"CREATE INDEX idx_mt_acct_ym ON [{TXN_TABLE_NAME}] (accountnumber, yearmonth)"
    )
    cursor.execute(
        f"CREATE INDEX idx_mt_community_ym ON [{TXN_TABLE_NAME}] (community, yearmonth)"
    )
    logger.info("Table [%s] created with indexes", TXN_TABLE_NAME)


# ---------------------------------------------------------------------------
# Portfolio payment CSV import (SparkMeter exports from Dropbox)
# ---------------------------------------------------------------------------
# These CSVs are the authoritative transaction records, including manual
# corrections made by the O&M team directly on the SparkMeter UI.
#
# File format: portfolio_<org_id>_payments_<from>_<to>_<timestamp>.csv
# Key columns: site_name, type, currency, amount, creator_name,
#   customer_code, meter_serial_number, status, processed_date, reversed_date
# ---------------------------------------------------------------------------


def find_portfolio_csvs() -> List[str]:
    """Discover all portfolio payment CSVs on the filesystem."""
    csv_files: List[str] = []
    for base_path in PORTFOLIO_CSV_SEARCH_PATHS:
        if not os.path.isdir(base_path):
            continue
        for root, _dirs, files in os.walk(base_path):
            for f in files:
                if f.startswith("portfolio_") and f.endswith(".csv"):
                    csv_files.append(os.path.join(root, f))
    return sorted(csv_files)


def parse_portfolio_csv(
    filepath: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Parse a portfolio payment CSV into per-site-per-month buckets.

    Returns: {
        "MAS|2025-11": [
            {"customer_code": "0014MAS", "meter_serial": "SMRSD-...",
             "amount_lsl": 40.0, "type": "payment", "status": "processed",
             "creator": "payment-gateway"},
            ...
        ],
        ...
    }
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            site = (row.get("site_name") or "").strip()
            if not site:
                continue

            # Parse date
            processed_date = (row.get("processed_date") or "").strip()
            if not processed_date:
                processed_date = (row.get("Created") or "").strip()
            if not processed_date:
                continue

            try:
                dt = datetime.strptime(processed_date[:10], "%Y-%m-%d")
            except (ValueError, IndexError):
                try:
                    dt = datetime.strptime(processed_date[:10], "%m/%d/%Y")
                except (ValueError, IndexError):
                    continue
            ym = f"{dt.year:04d}-{dt.month:02d}"

            # Extract fields
            try:
                amount = float(row.get("amount") or row.get("Amount") or "0")
            except (ValueError, TypeError):
                continue

            status = (row.get("status") or row.get("State") or "").strip().lower()
            txn_type = (row.get("type") or row.get("Type") or "").strip().lower()
            customer_code = (
                row.get("customer_code") or row.get("To") or ""
            ).strip()
            meter_serial = (
                row.get("meter_serial_number") or row.get("Meter Serial") or ""
            ).strip()
            creator = (
                row.get("creator_name") or row.get("User") or ""
            ).strip()

            key = f"{site}|{ym}"
            if key not in buckets:
                buckets[key] = []
            buckets[key].append({
                "customer_code": customer_code,
                "meter_serial": meter_serial,
                "amount_lsl": amount,
                "type": txn_type,
                "status": status,
                "creator": creator,
            })

    return buckets


def import_portfolio_csvs(
    conn: pyodbc.Connection,
    csv_files: Optional[List[str]] = None,
    start_ym: Optional[str] = None,
    end_ym: Optional[str] = None,
) -> int:
    """Import transaction data from SparkMeter portfolio CSVs into ACCDB.

    Aggregates per customer per month:
      - Sum of payment amounts (LSL)
      - Count of transactions
      - Reversals are netted out (subtracted from amounts)

    Idempotent: deletes existing rows for each community/yearmonth/source
    before inserting.
    """
    if csv_files is None:
        csv_files = find_portfolio_csvs()

    if not csv_files:
        logger.warning("No portfolio CSVs found")
        return 0

    logger.info("Found %d portfolio CSV files", len(csv_files))

    # Parse all CSVs into a merged bucket map
    all_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for filepath in csv_files:
        try:
            buckets = parse_portfolio_csv(filepath)
            for key, records in buckets.items():
                if key not in all_buckets:
                    all_buckets[key] = []
                all_buckets[key].extend(records)
        except Exception as e:
            logger.error("Error parsing %s: %s", os.path.basename(filepath), e)

    logger.info(
        "Parsed %d site-month buckets from portfolio CSVs",
        len(all_buckets),
    )

    # Aggregate per customer per month and insert
    cursor = conn.cursor()
    total_inserted = 0
    months_processed = 0

    for key in sorted(all_buckets.keys()):
        site, ym = key.split("|", 1)

        # Date range filter
        if start_ym and ym < start_ym:
            continue
        if end_ym and ym > end_ym:
            continue

        records = all_buckets[key]

        # Aggregate: per customer_code, sum net amounts, count transactions
        # Reversals reduce the net amount.
        customer_agg: Dict[str, Dict[str, Any]] = {}

        for rec in records:
            code = rec["customer_code"]
            if not code:
                code = rec["meter_serial"] or "UNKNOWN"

            if code not in customer_agg:
                customer_agg[code] = {
                    "meter_serial": rec["meter_serial"],
                    "net_amount": 0.0,
                    "txn_count": 0,
                }

            amount = rec["amount_lsl"]
            if rec["type"] == "reversal" or rec["status"] == "reversed":
                customer_agg[code]["net_amount"] -= amount
            else:
                customer_agg[code]["net_amount"] += amount
            customer_agg[code]["txn_count"] += 1

            # Keep the meter serial if we have one
            if rec["meter_serial"] and not customer_agg[code]["meter_serial"]:
                customer_agg[code]["meter_serial"] = rec["meter_serial"]

        # Delete existing rows for this site/month from portfolio source
        cursor.execute(
            f"DELETE FROM [{TXN_TABLE_NAME}] "
            "WHERE community = ? AND yearmonth = ? AND source = ?",
            (site, ym, "sparkmeter"),
        )

        inserted = 0
        for code, agg in customer_agg.items():
            if agg["net_amount"] <= 0:
                continue

            cursor.execute(
                f"INSERT INTO [{TXN_TABLE_NAME}] "
                "(accountnumber, meterid, yearmonth, kwh_vended, amount_lsl, "
                "txn_count, community, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code,
                    agg["meter_serial"],
                    ym,
                    0.0,  # kWh not available in payment data
                    agg["net_amount"],
                    agg["txn_count"],
                    site,
                    "sparkmeter",
                ),
            )
            inserted += 1

        if inserted > 0:
            months_processed += 1
            total_inserted += inserted
            logger.debug(
                "Portfolio %s %s: %d customers, %d raw records",
                site, ym, inserted, len(records),
            )

    logger.info(
        "Portfolio CSV import: %d customer-months across %d site-months",
        total_inserted, months_processed,
    )
    return total_inserted


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

            # Access doesn't support COUNT(DISTINCT), so use a subquery
            cursor.execute(
                f"SELECT COUNT(*) FROM "
                f"(SELECT DISTINCT [meterid] FROM [{table}] "
                f"WHERE [powerkW] IS NOT NULL)"
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

    # Check tblmonthlytransactions
    if TXN_TABLE_NAME.lower() in existing:
        cursor.execute(f"SELECT COUNT(*) FROM [{TXN_TABLE_NAME}]")
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT [source], COUNT(*) AS cnt FROM [{TXN_TABLE_NAME}] GROUP BY [source]"
        )
        by_source = {str(r[0]): r[1] for r in cursor.fetchall()}
        cursor.execute(
            f"SELECT SUM([kwh_vended]), SUM([amount_lsl]) FROM [{TXN_TABLE_NAME}]"
        )
        agg = cursor.fetchone()
        logger.info("=" * 50)
        logger.info("TARGET TABLE: %s", TXN_TABLE_NAME)
        logger.info("  Total rows: %d", total)
        logger.info("  By source: %s", by_source)
        logger.info(
            "  Total kWh vended: %.1f, Total LSL: %.2f",
            float(agg[0] or 0), float(agg[1] or 0),
        )
    else:
        logger.info("TARGET TABLE: %s — does not exist yet", TXN_TABLE_NAME)


def import_accdb_local(conn: pyodbc.Connection) -> int:
    """Aggregate existing ACCDB meter data tables into tblmonthlyconsumption.

    Streams raw (meterid, whdatetime, powerkW) rows through Python,
    bins to hourly average kW, then sums hours to monthly kWh.
    Processing is done in Python to avoid overloading the Jet engine
    with nested GROUP BY on 1M+ rows.

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

    # ── Pass 1: Stream rows, bin to hourly ──
    # Key: (meterid, "yyyy-mm-dd-HH") → [sum_kw, count]
    # We accumulate sum and count to compute average later.
    hourly: Dict[Tuple[str, str], List[float]] = {}
    tables_stats: Dict[str, Any] = {}

    for table in ACCDB_METER_TABLES:
        try:
            logger.info("Reading %s ...", table)
            cursor.execute(
                f"SELECT [meterid], [whdatetime], [powerkW] "
                f"FROM [{table}] WHERE [powerkW] IS NOT NULL"
            )
            row_count = 0
            table_meters: set = set()
            batch = cursor.fetchmany(5000)
            while batch:
                for row in batch:
                    mid = str(row[0] or "").strip()
                    dt = row[1]
                    kw = row[2]
                    if not mid or dt is None or kw is None:
                        continue
                    try:
                        kw = float(kw)
                    except (ValueError, TypeError):
                        continue
                    if kw <= 0:
                        continue

                    # Extract hour bin: "yyyy-mm-dd-HH"
                    try:
                        if isinstance(dt, str):
                            dt = datetime.strptime(dt[:16], "%Y-%m-%d %H:%M")
                        hour_key = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}-{dt.hour:02d}"
                    except (ValueError, AttributeError):
                        continue

                    table_meters.add(mid)
                    hk = (mid, hour_key)
                    if hk in hourly:
                        hourly[hk][0] += kw
                        hourly[hk][1] += 1
                    else:
                        hourly[hk] = [kw, 1]

                row_count += len(batch)
                if row_count % 100000 == 0:
                    logger.info("  %s: %d rows processed...", table, row_count)
                batch = cursor.fetchmany(5000)

            tables_stats[table] = {
                "rows": row_count,
                "meters": len(table_meters),
            }
            logger.info(
                "  %s: %d rows, %d unique meters", table, row_count, len(table_meters),
            )
        except Exception as e:
            logger.error("  %s: read failed — %s", table, e)
            tables_stats[table] = {"error": str(e)}

    if not hourly:
        logger.warning("No meter readings found in any ACCDB table")
        return 0

    logger.info("Hourly bins: %d (aggregating to monthly...)", len(hourly))

    # ── Pass 2: Collapse hourly → monthly ──
    # avg kW for each hour × 1 h = kWh; sum across hours in month.
    # Key: (meterid, "yyyy-mm") → kwh
    monthly: Dict[Tuple[str, str], float] = {}
    for (mid, hour_key), (sum_kw, count) in hourly.items():
        avg_kw = sum_kw / count
        ym = hour_key[:7]  # "yyyy-mm"
        mk = (mid, ym)
        monthly[mk] = monthly.get(mk, 0.0) + avg_kw

    # Free memory
    del hourly

    logger.info("Monthly records: %d", len(monthly))

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


def fetch_koios_historical_month(
    session: requests.Session,
    site_id: str,
    yearmonth: str,
) -> List[Dict[str, str]]:
    """Fetch historical readings from Koios v2 API for one site+month.

    Falls back to this when the CSV report endpoint returns 404.
    Returns list of dicts with keys: customer_code, meter_serial, total_energy.
    """
    parts = yearmonth.split("-")
    year, month = int(parts[0]), int(parts[1])
    last_day = calendar.monthrange(year, month)[1]
    date_from = f"{yearmonth}-01"
    date_to = f"{yearmonth}-{last_day:02d}"

    all_data: List[Dict[str, Any]] = []
    cursor_token: Optional[str] = None

    while True:
        body: Dict[str, Any] = {
            "filters": {
                "sites": [site_id],
                "date_range": {"from": date_from, "to": date_to},
            },
            "per_page": 5000,
        }
        if cursor_token:
            body["cursor"] = cursor_token

        r = session.post(
            f"{KOIOS_BASE_URL}/api/v2/organizations/{KOIOS_ORG_ID}/data/historical",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=90,
        )
        r.raise_for_status()
        resp = r.json()
        batch = resp.get("data", [])
        all_data.extend(batch)

        pagination = resp.get("pagination", {})
        cursor_token = pagination.get("cursor")
        has_more = pagination.get("has_more", False)

        if not has_more or not cursor_token or not batch:
            break
        time.sleep(0.2)

    if not all_data:
        return []

    # Log field names from the first record (diagnostic, first call only)
    if all_data:
        record = all_data[0]
        logger.debug(
            "Koios historical record keys: %s", list(record.keys())
        )

    # Aggregate kWh per meter.
    # Try multiple field names for meter ID and customer code.
    meter_kwh: Dict[str, float] = {}
    meter_customer: Dict[str, str] = {}

    for rec in all_data:
        # Meter identifier
        meter = str(
            rec.get("meter", rec.get("meter_serial", rec.get("meter_id", "")))
        ).strip()
        if not meter:
            continue

        # Energy
        kwh = 0.0
        for field in ("kilowatt_hours", "kwh", "energy", "total_energy"):
            if field in rec:
                try:
                    kwh = float(rec[field])
                except (ValueError, TypeError):
                    pass
                break
        meter_kwh[meter] = meter_kwh.get(meter, 0.0) + kwh

        # Customer code (may or may not be in the response)
        if meter not in meter_customer:
            for field in ("customer_code", "code", "customer_id", "customer"):
                val = rec.get(field)
                if val:
                    meter_customer[meter] = str(val).strip()
                    break

    # Convert to the same format as parse_koios_meter_details
    results: List[Dict[str, str]] = []
    for meter, kwh in meter_kwh.items():
        if kwh <= 0:
            continue
        results.append({
            "customer_code": meter_customer.get(meter, ""),
            "meter_serial": meter,
            "total_energy": str(kwh),
        })

    return results


def fetch_koios_customer_map(
    session: requests.Session,
) -> Dict[str, str]:
    """Build meter_serial → customer_code mapping from Koios v1 customers API.

    Called once at the start of Koios import to supplement historical records
    that may not include customer_code.
    """
    meter_to_code: Dict[str, str] = {}
    cursor_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {"per_page": 50}
        if cursor_token:
            params["cursor"] = cursor_token

        try:
            r = session.get(
                f"{KOIOS_BASE_URL}/api/v1/customers",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Koios customer list failed: %s", e)
            break

        data = r.json()
        batch = data.get("data", [])
        for c in batch:
            code = str(c.get("code", "")).strip()
            # The customer may have a meter field or meters list
            meter = str(c.get("meter", c.get("meter_serial", ""))).strip()
            if code and meter:
                meter_to_code[meter] = code

        cursor_token = data.get("cursor")
        if not cursor_token or not batch:
            break
        time.sleep(0.2)

    logger.info("Koios customer map: %d meter→code entries", len(meter_to_code))
    return meter_to_code


def import_koios_month(
    session: requests.Session,
    conn: pyodbc.Connection,
    site_code: str,
    site_id: str,
    yearmonth: str,
    koios_customer_map: Optional[Dict[str, str]] = None,
) -> int:
    """Import one month of Koios data for one site.

    Tries CSV report first; falls back to historical API if 404.
    Returns rows inserted.
    """
    date_str = f"{yearmonth}-01"
    meters: List[Dict[str, str]] = []
    source_method = "csv"

    # Try CSV report first (fast, pre-aggregated)
    try:
        csv_text = fetch_koios_monthly_csv(session, site_id, date_str)
        meters = parse_koios_meter_details(csv_text)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # CSV not available — fall back to historical API
            try:
                meters = fetch_koios_historical_month(session, site_id, yearmonth)
                source_method = "historical"
            except requests.HTTPError as e2:
                logger.warning("Koios %s %s: historical API also failed: %s", site_code, yearmonth, e2)
                return 0
            except Exception as e2:
                logger.warning("Koios %s %s: historical API error: %s", site_code, yearmonth, e2)
                return 0
        else:
            logger.warning("Koios %s %s: HTTP %s", site_code, yearmonth, e)
            return 0

    if not meters:
        return 0

    cursor = conn.cursor()
    cursor.execute(
        f"DELETE FROM [{TABLE_NAME}] WHERE community = ? AND yearmonth = ? AND source = ?",
        (site_code, yearmonth, "koios"),
    )

    inserted = 0
    for m in meters:
        acct = m.get("customer_code", "").strip()
        meter_serial = m.get("meter_serial", "").strip()

        # If customer_code missing, try the customer map
        if not acct and meter_serial and koios_customer_map:
            acct = koios_customer_map.get(meter_serial, "")

        try:
            kwh = float(m.get("total_energy", "0") or "0")
        except (ValueError, TypeError):
            continue

        if kwh <= 0:
            continue

        # Use meter_serial as accountnumber fallback if no customer code
        if not acct:
            acct = meter_serial

        cursor.execute(
            f"INSERT INTO [{TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh, community, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (acct, meter_serial, yearmonth, kwh, site_code, "koios"),
        )
        inserted += 1

    if inserted > 0 and source_method == "historical":
        logger.info(
            "Koios %s %s: %d meters imported (via historical API, %d readings)",
            site_code, yearmonth, inserted, len(meters),
        )

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
# Step 4: Transaction / payment import (Koios + ThunderCloud)
# ---------------------------------------------------------------------------
# Koios provides:
#   - Readings CSV  → per-meter kWh vended (METER DETAILS section, total_energy field)
#   - Payments CSV  → site-level aggregate LSL (no per-customer breakdown)
#   - /api/v1/payments → individual payment records per customer
# ThunderCloud provides:
#   - Parquet files → kWh + cost per reading per meter
# ---------------------------------------------------------------------------


def fetch_koios_payments_v1(
    session: requests.Session,
    site_code: str,
    yearmonth: str,
    koios_customer_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch individual payment records from Koios /api/v1/payments.

    Returns list of dicts: {customer_code, meter_serial, kwh, amount_lsl}
    aggregated per meter for the given month.
    """
    service_area_id = KOIOS_SERVICE_AREAS.get(site_code, "")
    if not service_area_id:
        logger.debug("No service area mapping for %s, skipping v1 payments", site_code)
        return []

    parts = yearmonth.split("-")
    year, month_num = int(parts[0]), int(parts[1])
    last_day = calendar.monthrange(year, month_num)[1]
    date_from = f"{yearmonth}-01T00:00:00"
    date_to = f"{yearmonth}-{last_day:02d}T23:59:59"

    all_payments: List[Dict[str, Any]] = []
    cursor_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {
            "per_page": 50,
            "service_area_id": service_area_id,
            "from": date_from,
            "to": date_to,
        }
        if cursor_token:
            params["cursor"] = cursor_token

        try:
            r = session.get(
                f"{KOIOS_BASE_URL}/api/v1/payments",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.debug("Koios v1 payments 404 for %s %s", site_code, yearmonth)
                return []
            logger.warning("Koios v1 payments %s %s: %s", site_code, yearmonth, e)
            return []
        except requests.RequestException as e:
            logger.warning("Koios v1 payments %s %s: %s", site_code, yearmonth, e)
            return []

        data = r.json()
        batch = data.get("data", [])

        # Log field names from first record (once)
        if batch and not all_payments:
            logger.debug(
                "Koios v1 payment record keys: %s", list(batch[0].keys())
            )

        all_payments.extend(batch)

        cursor_token = data.get("cursor")
        if not cursor_token or not batch:
            break
        time.sleep(0.2)

    if not all_payments:
        return []

    # Aggregate per meter: sum kWh and amount
    meter_kwh: Dict[str, float] = {}
    meter_amount: Dict[str, float] = {}
    meter_count: Dict[str, int] = {}
    meter_customer: Dict[str, str] = {}

    for p in all_payments:
        # Try common field names for meter serial
        meter = ""
        for field in ("meter_serial", "meter", "meter_id"):
            val = p.get(field)
            if val:
                meter = str(val).strip()
                break
        if not meter:
            # Some payment records link to customer instead of meter
            code = ""
            for field in ("customer_code", "code", "customer_id"):
                val = p.get(field)
                if val:
                    code = str(val).strip()
                    break
            if code:
                meter = code  # Use customer code as the key
            else:
                continue

        # kWh vended
        kwh = 0.0
        for field in ("energy", "kilowatt_hours", "kwh", "total_energy"):
            if field in p:
                try:
                    kwh = float(p[field])
                except (ValueError, TypeError):
                    pass
                break

        # Amount paid
        amount = 0.0
        for field in ("amount", "total_amount", "payment_amount", "cost"):
            if field in p:
                try:
                    amount = float(p[field])
                except (ValueError, TypeError):
                    pass
                break

        # Skip reversed transactions
        status = str(p.get("status", "")).lower()
        if status in ("reversed", "failed", "cancelled"):
            continue

        meter_kwh[meter] = meter_kwh.get(meter, 0.0) + kwh
        meter_amount[meter] = meter_amount.get(meter, 0.0) + amount
        meter_count[meter] = meter_count.get(meter, 0) + 1

        if meter not in meter_customer:
            for field in ("customer_code", "code", "customer_id"):
                val = p.get(field)
                if val:
                    meter_customer[meter] = str(val).strip()
                    break

    # Supplement customer codes from the customer map
    if koios_customer_map:
        for meter in meter_kwh:
            if meter not in meter_customer:
                if meter in koios_customer_map:
                    meter_customer[meter] = koios_customer_map[meter]

    results: List[Dict[str, Any]] = []
    for meter in meter_kwh:
        results.append({
            "customer_code": meter_customer.get(meter, ""),
            "meter_serial": meter,
            "kwh": meter_kwh[meter],
            "amount_lsl": meter_amount[meter],
            "txn_count": meter_count[meter],
        })

    return results


# Koios service area IDs (for /api/v1 endpoints that filter by service_area_id)
KOIOS_SERVICE_AREAS = {
    "KET": "e1ef0c38-298d-4fef-bc7d-78a645fe325d",
    "LSB": "328ceae8-8b57-4173-b54b-82481d833d6a",
    "MAS": "e6efc982-91ea-4721-92ee-97e68dd761bb",
    "MAT": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "SEH": "402e4b83-45bb-4dea-a276-ac99927514cb",
    "SHG": "f54a1658-1763-4ba7-8bf3-fbf71bed97fe",
    "TLH": "f8b5d05e-3a29-4e65-a0ad-6e60c0f2d85b",
    "RIB": "8b574fc5-8f59-4bd8-b1d4-2882a0747abb",
    "TOS": "6cbc921c-62e2-49d2-8b20-0b0ab38b2005",
}


def fetch_koios_payments_csv_amount(
    session: requests.Session,
    site_id: str,
    date_str: str,
) -> Optional[float]:
    """Fetch site-level total payments LSL from Koios payments CSV.

    Returns the total LSL amount, or None if unavailable.
    """
    try:
        r = session.get(
            f"{KOIOS_BASE_URL}/api/v2/report",
            params={
                "granularity": "monthly",
                "type": "payments",
                "site_id": site_id,
                "date": date_str,
            },
            timeout=60,
        )
        r.raise_for_status()
    except requests.HTTPError:
        return None

    for line in r.text.strip().split("\n"):
        stripped = line.strip()
        if "," in stripped:
            key, val = stripped.split(",", 1)
            if key.strip() == "Total Payments":
                try:
                    return float(val.strip())
                except ValueError:
                    return None
    return None


def import_koios_transactions_month(
    session: requests.Session,
    conn: pyodbc.Connection,
    site_code: str,
    site_id: str,
    yearmonth: str,
    koios_customer_map: Optional[Dict[str, str]] = None,
) -> int:
    """Import one month of Koios transaction/payment data for one site.

    Strategy:
      1. Try /api/v1/payments for per-customer payment records (kWh + LSL)
      2. If v1 payments unavailable, fall back to readings CSV for per-meter
         kWh vended (no LSL breakdown available per customer)

    Returns rows inserted into tblmonthlytransactions.
    """
    # Try v1 payments first (has per-customer amounts)
    txn_records = fetch_koios_payments_v1(
        session, site_code, yearmonth, koios_customer_map
    )

    if txn_records:
        # Insert payment records
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM [{TXN_TABLE_NAME}] "
            "WHERE community = ? AND yearmonth = ? AND source = ?",
            (site_code, yearmonth, "koios"),
        )

        inserted = 0
        for rec in txn_records:
            acct = rec.get("customer_code", "").strip()
            meter = rec.get("meter_serial", "").strip()
            kwh = rec.get("kwh", 0.0)
            amount = rec.get("amount_lsl", 0.0)
            txn_count = rec.get("txn_count", 0)

            if not acct:
                acct = meter or "UNKNOWN"

            cursor.execute(
                f"INSERT INTO [{TXN_TABLE_NAME}] "
                "(accountnumber, meterid, yearmonth, kwh_vended, amount_lsl, "
                "txn_count, community, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (acct, meter, yearmonth, kwh, amount, txn_count, site_code, "koios"),
            )
            inserted += 1

        return inserted

    # Fallback: use readings CSV for per-meter kWh (no LSL breakdown)
    date_str = f"{yearmonth}-01"
    meters: List[Dict[str, str]] = []

    try:
        csv_text = fetch_koios_monthly_csv(session, site_id, date_str)
        meters = parse_koios_meter_details(csv_text)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            try:
                meters = fetch_koios_historical_month(session, site_id, yearmonth)
            except Exception:
                return 0
        else:
            return 0

    if not meters:
        return 0

    # Get site-level total payments to distribute proportionally
    total_site_lsl = fetch_koios_payments_csv_amount(session, site_id, date_str)

    # Calculate total kWh across all meters for proportional allocation
    total_kwh_all = 0.0
    for m in meters:
        try:
            total_kwh_all += float(m.get("total_energy", "0") or "0")
        except (ValueError, TypeError):
            pass

    cursor = conn.cursor()
    cursor.execute(
        f"DELETE FROM [{TXN_TABLE_NAME}] "
        "WHERE community = ? AND yearmonth = ? AND source = ?",
        (site_code, yearmonth, "koios"),
    )

    inserted = 0
    for m in meters:
        acct = m.get("customer_code", "").strip()
        meter_serial = m.get("meter_serial", "").strip()

        if not acct and meter_serial and koios_customer_map:
            acct = koios_customer_map.get(meter_serial, "")
        if not acct:
            acct = meter_serial or "UNKNOWN"

        try:
            kwh = float(m.get("total_energy", "0") or "0")
        except (ValueError, TypeError):
            continue

        if kwh <= 0:
            continue

        # Estimate LSL from proportional share of site total
        amount_lsl = 0.0
        if total_site_lsl and total_kwh_all > 0:
            amount_lsl = total_site_lsl * (kwh / total_kwh_all)

        cursor.execute(
            f"INSERT INTO [{TXN_TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh_vended, amount_lsl, "
            "txn_count, community, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (acct, meter_serial, yearmonth, kwh, amount_lsl, 0, site_code, "koios"),
        )
        inserted += 1

    return inserted


def import_thundercloud_transactions_month(
    tc: "ThunderCloudClient",
    conn: pyodbc.Connection,
    yearmonth: str,
) -> int:
    """Import one month of ThunderCloud (MAK) transaction data.

    Uses the same Parquet files as consumption import, but extracts
    cost data (if available) alongside kWh.
    """
    parts = yearmonth.split("-")
    year, month_num = int(parts[0]), int(parts[1])

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required for ThunderCloud")
        return 0

    last_day = calendar.monthrange(year, month_num)[1]
    all_frames = []

    for day in range(1, last_day + 1):
        raw = tc.download_day_raw(year, month_num, day)
        if raw is None:
            continue
        try:
            df = pd.read_parquet(io.BytesIO(raw))
            all_frames.append(df)
        except Exception:
            continue

    if not all_frames:
        return 0

    combined = pd.concat(all_frames, ignore_index=True)

    # Check for cost column
    has_cost = "cost" in combined.columns

    # Customer code column
    code_col = None
    for col in ("snapshot_customer_code", "snapshot_customer_id"):
        if col in combined.columns:
            code_col = col
            break

    # Aggregate per meter
    agg_dict: Dict[str, Any] = {"kilowatt_hours": "sum"}
    if has_cost:
        agg_dict["cost"] = "sum"

    grouped = combined.groupby("meter").agg(**agg_dict).reset_index()

    cursor = conn.cursor()
    cursor.execute(
        f"DELETE FROM [{TXN_TABLE_NAME}] "
        "WHERE community = ? AND yearmonth = ? AND source = ?",
        ("MAK", yearmonth, "thundercloud"),
    )

    inserted = 0
    for _, row in grouped.iterrows():
        meter_id = str(row["meter"])
        kwh = float(row["kilowatt_hours"])
        amount_lsl = float(row["cost"]) if has_cost else 0.0

        # Look up customer code
        cust_code = ""
        if code_col:
            mask = combined["meter"] == row["meter"]
            codes = combined.loc[mask, code_col].dropna().unique()
            if len(codes) > 0:
                cust_code = str(codes[0])

        if not cust_code or kwh <= 0:
            continue

        cursor.execute(
            f"INSERT INTO [{TXN_TABLE_NAME}] "
            "(accountnumber, meterid, yearmonth, kwh_vended, amount_lsl, "
            "txn_count, community, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cust_code, meter_id, yearmonth, kwh, amount_lsl, 0, "MAK", "thundercloud"),
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
    parser.add_argument(
        "--transactions-only", action="store_true",
        help="Only import transaction/payment data (skip consumption readings)",
    )
    parser.add_argument(
        "--portfolio-only", action="store_true",
        help="Only import SparkMeter portfolio CSVs from Dropbox (fastest)",
    )
    parser.add_argument(
        "--no-transactions", action="store_true",
        help="Skip transaction import (consumption only, original behavior)",
    )
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
    ensure_txn_table(conn)

    # Determine date range for external APIs
    if args.end is None:
        now = datetime.now()
        args.end = f"{now.year:04d}-{now.month:02d}"

    months = month_range(args.start, args.end)

    do_consumption = not args.transactions_only and not args.portfolio_only
    do_transactions = not args.no_transactions

    # --portfolio-only is a shortcut: skip consumption, skip API-based txn, only do CSVs
    portfolio_only = args.portfolio_only

    if args.dry_run:
        logger.info("Import range: %s to %s (%d months)", args.start, args.end, len(months))
        if do_consumption and not args.remote_only:
            logger.info("Step 1: ACCDB local aggregation (%s)", ", ".join(ACCDB_METER_TABLES))
        if do_consumption and not args.local_only and not args.thundercloud_only:
            logger.info("Step 2: Koios readings (%s)", ", ".join(KOIOS_SITES.keys()))
        if do_consumption and not args.local_only and not args.koios_only:
            logger.info("Step 3: ThunderCloud readings (MAK)")
        if do_transactions:
            csv_count = len(find_portfolio_csvs())
            logger.info("Step 4: SparkMeter portfolio CSVs (%d files found)", csv_count)
        if do_transactions and not args.local_only and not args.thundercloud_only:
            logger.info("Step 5: Koios API transactions — gap-fill (%s)", ", ".join(KOIOS_SITES.keys()))
        if do_transactions and not args.local_only and not args.koios_only:
            logger.info("Step 6: ThunderCloud transactions — gap-fill (MAK)")
        return

    total_inserted = 0
    total_errors = 0
    txn_inserted = 0

    # ── Step 1: Aggregate existing ACCDB meter data tables ──
    if do_consumption and not args.remote_only and not args.koios_only and not args.thundercloud_only:
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
        logger.info("  Consumption rows inserted: %d", total_inserted)
        logger.info("=" * 60)
        return

    # Short-circuit: portfolio-only mode skips all API calls
    if portfolio_only:
        # Jump directly to Step 4 (portfolio CSVs are local files, no API needed)
        pass
    else:
        logger.info(
            "External API range: %s to %s (%d months)", args.start, args.end, len(months)
        )

    # Build customer map once (shared between consumption + transaction imports)
    koios_session = None
    koios_cust_map = None
    if not portfolio_only and not args.thundercloud_only:
        koios_session = _koios_session()
        logger.info("Building Koios customer map (meter → account)...")
        koios_cust_map = fetch_koios_customer_map(koios_session)

    # ── Step 2: Koios consumption import ──
    if do_consumption and not args.thundercloud_only and koios_session:
        logger.info("=" * 60)
        logger.info("STEP 2: Koios consumption import")
        logger.info("=" * 60)
        for site_code, site_id in KOIOS_SITES.items():
            consecutive_empty = 0
            site_started = False
            site_total = 0
            for ym in months:
                try:
                    n = import_koios_month(
                        koios_session, conn, site_code, site_id, ym,
                        koios_customer_map=koios_cust_map,
                    )
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

    # ── Step 3: ThunderCloud consumption import ──
    tc = None
    if (do_consumption or do_transactions) and not args.koios_only:
        tc = ThunderCloudClient()

    if do_consumption and tc and not args.koios_only:
        logger.info("=" * 60)
        logger.info("STEP 3: ThunderCloud consumption import (MAK)")
        logger.info("=" * 60)
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

    # ── Step 4: Portfolio CSV transaction import (primary, authoritative) ──
    # SparkMeter portfolio exports include gateway + manual corrections.
    portfolio_months_covered: set = set()
    if do_transactions:
        logger.info("=" * 60)
        logger.info("STEP 4: SparkMeter portfolio CSV transaction import")
        logger.info("=" * 60)
        csv_files = find_portfolio_csvs()
        if csv_files:
            try:
                n = import_portfolio_csvs(
                    conn, csv_files,
                    start_ym=args.start, end_ym=args.end,
                )
                txn_inserted += n

                # Track which site-months are covered so we can skip them
                # in the API-based import (avoid duplicates)
                for fp in csv_files:
                    try:
                        buckets = parse_portfolio_csv(fp)
                        for key in buckets:
                            portfolio_months_covered.add(key)
                    except Exception:
                        pass
                logger.info(
                    "Portfolio CSVs cover %d site-months",
                    len(portfolio_months_covered),
                )
            except Exception as e:
                logger.error("Portfolio CSV import failed: %s", e)
                total_errors += 1
        else:
            logger.info("No portfolio CSVs found (skipping)")

    # ── Step 5: Koios API transaction import (for months not in CSVs) ──
    if do_transactions and not portfolio_only and not args.thundercloud_only and koios_session:
        logger.info("=" * 60)
        logger.info("STEP 5: Koios API transaction import (gap-fill)")
        logger.info("=" * 60)
        for site_code, site_id in KOIOS_SITES.items():
            consecutive_empty = 0
            site_started = False
            site_total = 0
            for ym in months:
                key = f"{site_code}|{ym}"
                if key in portfolio_months_covered:
                    continue  # Already have authoritative data from CSV

                try:
                    n = import_koios_transactions_month(
                        koios_session, conn, site_code, site_id, ym,
                        koios_customer_map=koios_cust_map,
                    )
                    if n > 0:
                        logger.info(
                            "Koios txn %s %s: %d records imported",
                            site_code, ym, n,
                        )
                        site_started = True
                        consecutive_empty = 0
                        site_total += n
                    else:
                        consecutive_empty += 1
                    txn_inserted += n
                except Exception as e:
                    logger.error("Koios txn %s %s failed: %s", site_code, ym, e)
                    total_errors += 1
                    consecutive_empty += 1

                if not site_started and consecutive_empty >= 6:
                    logger.info(
                        "Koios txn %s: no data in first %d months, fast-forwarding",
                        site_code, consecutive_empty,
                    )
                    consecutive_empty = 0
                time.sleep(0.3)
            logger.info("Koios txn %s: %d total rows imported", site_code, site_total)

    # ── Step 6: ThunderCloud transaction import (for MAK, months not in CSVs) ──
    if do_transactions and not portfolio_only and tc and not args.koios_only:
        logger.info("=" * 60)
        logger.info("STEP 6: ThunderCloud transaction import (MAK, gap-fill)")
        logger.info("=" * 60)
        consecutive_empty = 0
        tc_started = False
        for ym in months:
            key = f"MAK|{ym}"
            if key in portfolio_months_covered:
                continue  # Already have authoritative data from CSV

            try:
                n = import_thundercloud_transactions_month(tc, conn, ym)
                if n > 0:
                    logger.info(
                        "ThunderCloud MAK txn %s: %d records imported", ym, n,
                    )
                    tc_started = True
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                txn_inserted += n
            except Exception as e:
                logger.error("ThunderCloud MAK txn %s failed: %s", ym, e)
                total_errors += 1
                consecutive_empty += 1
            if not tc_started and consecutive_empty >= 6:
                consecutive_empty = 0

    conn.close()

    logger.info("=" * 60)
    logger.info("IMPORT COMPLETE")
    logger.info("  Consumption rows inserted: %d", total_inserted)
    logger.info("  Transaction rows inserted: %d", txn_inserted)
    logger.info("  Errors: %d", total_errors)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
