"""Shared helpers for Lesotho SMP 1PDB–SparkMeter cutover ops."""

from __future__ import annotations

import re
from datetime import datetime, timezone

VALID_ACCOUNT_RE = re.compile(r"^\d{4}[A-Z]{2,4}$")
SITE_CODE_RE = re.compile(r"([A-Z]{2,4})$")

BULK_EXCLUDED_ACCOUNTS = frozenset({"0500MAK"})
BULK_EXCLUDED_SITE_SUFFIXES = frozenset({"BVW", "LAB"})
DEFAULT_CUTOVER_TAG = "smp_cutover_2026-05-12"
DEFAULT_DRIFT_THRESHOLD_KWH = 0.5


def site_code(account_number: str) -> str:
    match = SITE_CODE_RE.search((account_number or "").upper())
    return match.group(1) if match else ""


def is_bulk_excluded_account(account_number: str) -> bool:
    account = (account_number or "").strip().upper()
    if not account:
        return True
    if account in BULK_EXCLUDED_ACCOUNTS:
        return True
    if account.endswith("BVW"):
        return True
    if site_code(account) in BULK_EXCLUDED_SITE_SUFFIXES:
        return True
    if account.startswith("FAULTY"):
        return True
    if not VALID_ACCOUNT_RE.match(account):
        return True
    return False


def cutover_tag_for(ts: datetime | None = None) -> str:
    when = ts or datetime.now(timezone.utc)
    return f"smp_cutover_{when.strftime('%Y-%m-%d')}"
