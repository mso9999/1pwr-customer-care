"""
SMA Sunny Portal adapter — covers the 7 PIH health centres in Lesotho.

Backend: https://www.sunnyportal.com  (web UI, no public REST API)

Plan: session-based HTML/XHR scrape. Sunny Portal logs in via form POST, then
renders plant dashboards that call an internal JSON backend for tile data.
Pattern matches the existing Benin Koios scraper (`sync_bn_customer_types.py`
+ `audit_bn_balances.py`), which already does session-cookie-based JSON
retrieval against a similar style portal.

Alternative: SMA Data Manager / Cluster Controller local Modbus-TCP + REST,
which yields richer per-string telemetry but requires site-local networking
or VPN. Deferred to Phase 3.

Phase 1 scope: credential schema + stub verify().
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List

from .base import (
    AdapterError,
    AlarmEvent,
    CredentialSpec,
    InverterAdapter,
    IntervalReading,
    LiveReading,
    SiteCredential,
    SiteEquipment,
    VerifyResult,
)

logger = logging.getLogger("cc-api.gensite.adapter.sma")


class SMAAdapter(InverterAdapter):
    vendor = "sma"
    display_name = "SMA Sunny Portal"
    implementation_status = "scrape"

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="sma",
                backend="sunny_portal",
                label="SMA Sunny Portal",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret"],
                extra_fields=[],
                docs_url="https://www.sunnyportal.com",
                notes=(
                    "Session-scrape adapter. 'username' is the Sunny Portal "
                    "email, 'secret' the portal password. 'site_id_on_vendor' "
                    "is the Sunny Portal plant OID. Implementation pending "
                    "Phase 2."
                ),
            ),
        ]

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.username or not cred.secret:
            return VerifyResult(
                ok=False,
                message="SMA Sunny Portal requires username + password.",
            )
        return VerifyResult(
            ok=False,
            message=(
                "SMA Sunny Portal adapter is scheduled for Phase 2 (PIH rollout). "
                "Credentials are stored encrypted; live scrape is not wired yet."
            ),
        )

    def fetch_live(self, cred, equipment):
        raise AdapterError("SMA fetch_live not yet implemented", retryable=False)

    def fetch_day(self, cred, equipment, day):
        return []

    def fetch_alarms(self, cred, equipment, since):
        return []
