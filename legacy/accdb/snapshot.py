"""
ACCDB Snapshot + Tiered Retention Script
==========================================
Copies the 1PWR ACCDB file to a Dropbox-synced backup folder
and prunes old snapshots according to a tiered retention policy:

  - Daily   snapshots: keep for 7 days
  - Weekly  snapshots: keep Sundays for 8 weeks (56 days)
  - Monthly snapshots: keep 1st of month for 12 months (365 days)
  - Annual  snapshots: keep Jan 1st forever

Designed to run daily via Windows Task Scheduler.

Usage:
    python snapshot.py

Environment variables (all optional, defaults shown):
    ACDB_PATH           - Source .accdb file  (default: auto-detect)
    SNAPSHOT_DIR        - Destination folder   (default: C:\\Users\\Administrator\\Dropbox\\ACCDB_Backups)
    SNAPSHOT_LOG_DIR    - Log folder           (default: C:\\acdb-customer-api\\logs)
"""

import os
import sys
import glob
import shutil
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ACDB_PATHS = [
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\0112023_1PWRKMETER.accdb",
    r"C:\Users\Administrator\Desktop\AccessDB_Clone\*.accdb",
]

SNAPSHOT_DIR = os.environ.get(
    "SNAPSHOT_DIR",
    r"C:\acdb-customer-api\backups",
)

LOG_DIR = os.environ.get(
    "SNAPSHOT_LOG_DIR",
    r"C:\acdb-customer-api\logs",
)

SNAPSHOT_PREFIX = "1PWRKMETER_"
SNAPSHOT_SUFFIX = ".accdb"
TIMESTAMP_FMT = "%Y%m%d_%H%M"
DATE_PARSE_FMT = "%Y%m%d"  # We only need the date portion for retention


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, "snapshot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("snapshot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_acdb() -> str:
    """Locate the ACCDB file."""
    env = os.environ.get("ACDB_PATH", "")
    if env and os.path.isfile(env):
        return env
    for pattern in DEFAULT_ACDB_PATHS:
        if "*" in pattern:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        elif os.path.isfile(pattern):
            return pattern
    return ""


def parse_snapshot_date(filename: str):
    """
    Extract the date from a snapshot filename like 1PWRKMETER_20260205_0000.accdb.
    Returns a datetime.date or None.
    """
    match = re.search(r"(\d{8})_\d{4}", filename)
    if match:
        try:
            return datetime.strptime(match.group(1), DATE_PARSE_FMT).date()
        except ValueError:
            return None
    return None


def should_keep(snapshot_date, today) -> bool:
    """
    Apply tiered retention policy.
    Returns True if the snapshot should be kept.
    """
    age = (today - snapshot_date).days

    # Daily tier: keep everything <= 7 days old
    if age <= 7:
        return True

    # Weekly tier: keep Sundays for up to 56 days (8 weeks)
    if age <= 56 and snapshot_date.weekday() == 6:  # 6 = Sunday
        return True

    # Monthly tier: keep 1st of month for up to 365 days
    if age <= 365 and snapshot_date.day == 1:
        return True

    # Annual tier: keep Jan 1st forever
    if snapshot_date.month == 1 and snapshot_date.day == 1:
        return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    logger.info("=" * 50)
    logger.info("ACCDB Snapshot Script starting")

    # 1. Find the source ACCDB
    acdb_path = find_acdb()
    if not acdb_path:
        logger.error("No ACCDB file found. Set ACDB_PATH environment variable.")
        sys.exit(1)
    logger.info("Source: %s", acdb_path)

    # 2. Ensure snapshot directory exists
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    logger.info("Snapshot dir: %s", SNAPSHOT_DIR)

    # 3. Create the snapshot
    now = datetime.now()
    snapshot_name = f"{SNAPSHOT_PREFIX}{now.strftime(TIMESTAMP_FMT)}{SNAPSHOT_SUFFIX}"
    dest = os.path.join(SNAPSHOT_DIR, snapshot_name)

    if os.path.exists(dest):
        logger.info("Snapshot already exists for this timestamp, skipping copy: %s", snapshot_name)
    else:
        try:
            shutil.copy2(acdb_path, dest)
            size_mb = os.path.getsize(dest) / (1024 * 1024)
            logger.info("Created snapshot: %s (%.1f MB)", snapshot_name, size_mb)
        except Exception as e:
            logger.error("Failed to create snapshot: %s", e)
            sys.exit(1)

    # 4. Apply tiered retention (prune old snapshots)
    today = now.date()
    kept = 0
    deleted = 0

    for entry in os.listdir(SNAPSHOT_DIR):
        if not entry.startswith(SNAPSHOT_PREFIX) or not entry.endswith(SNAPSHOT_SUFFIX):
            continue

        snap_date = parse_snapshot_date(entry)
        if snap_date is None:
            logger.warning("Cannot parse date from '%s', keeping it", entry)
            kept += 1
            continue

        if should_keep(snap_date, today):
            kept += 1
        else:
            path = os.path.join(SNAPSHOT_DIR, entry)
            try:
                os.remove(path)
                logger.info("Pruned: %s (age: %d days)", entry, (today - snap_date).days)
                deleted += 1
            except Exception as e:
                logger.warning("Failed to delete %s: %s", entry, e)
                kept += 1

    logger.info("Retention complete: %d kept, %d pruned", kept, deleted)
    logger.info("Done.")


if __name__ == "__main__":
    run()
