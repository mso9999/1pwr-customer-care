"""
Sinosoar Cloud adapter — covers the ~14 Lesotho Sinosoar sites.

Portal: https://www.sinosoarcloud.com/energystorage/es  (SPA; JSON XHR backend)

Status: stub. Sinosoar runs its own branded cloud rather than a white-labeled
Solarman/Shinemonitor/Solis tenant. Until we capture the exact login + data
endpoints from a browser DevTools session, the adapter is a placeholder that:

  * exposes the credential schema (username + password + optional station id)
    so credentials can still be stored encrypted during a commissioning run,
  * reports honestly that live fetch is not yet wired.

Next steps for promoting this to 'ready':
  1. Log into https://www.sinosoarcloud.com/energystorage/es with the CC creds.
  2. Open DevTools → Network, capture:
        - the login XHR (URL, payload, response token shape),
        - the dashboard XHR that paints the current-state tiles,
        - the "plant list" XHR,
        - any alarm / event XHR.
  3. Replace verify() and fetch_live() below with direct JSON calls mirroring
     the captured URLs.
  4. If the portal only renders via heavy client-side JS and resists JSON
     scraping, fall back to playwright-headless as a last resort or pivot to
     site-local Modbus via an edge agent.
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

logger = logging.getLogger("cc-api.gensite.adapter.sinosoar")

DEFAULT_BASE = "https://www.sinosoarcloud.com"


class SinosoarAdapter(InverterAdapter):
    vendor = "sinosoar"
    display_name = "Sinosoar Cloud"
    implementation_status = "stub"

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="sinosoar",
                backend="sinosoarcloud",
                label="Sinosoar Cloud (www.sinosoarcloud.com)",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret"],
                extra_fields=["org_id"],
                docs_url="https://www.sinosoarcloud.com/energystorage/es",
                notes=(
                    "Stub pending DevTools capture of the Sinosoar Cloud XHR "
                    "endpoints. Credentials you enter here are still stored "
                    "encrypted; live polling will light up after the adapter "
                    "is implemented."
                ),
            ),
        ]

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.username or not cred.secret:
            return VerifyResult(
                ok=False,
                message="Sinosoar requires username + password.",
            )
        return VerifyResult(
            ok=False,
            message=(
                "Sinosoar adapter is a stub. Credentials accepted and will be "
                "encrypted at rest, but live fetch is not wired yet. "
                "See gensite/adapters/sinosoar.py for the implementation plan."
            ),
        )

    def fetch_live(self, cred, equipment):
        raise AdapterError("Sinosoar fetch_live not yet implemented", retryable=False)

    def fetch_day(self, cred, equipment, day):
        return []

    def fetch_alarms(self, cred, equipment, since):
        return []
