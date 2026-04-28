"""
Solarman OpenAPI v2 adapter — covers Deye LSB (LS) and Deye SAM (BN).

Backend: https://globalapi.solarmanpv.com  (aka Solarman Business Cloud OpenAPI)
Docs:    https://doc.solarmanpv.com/web/#/118?page_id=258

Auth flow:
    POST /account/v1.0/token?appId=...&language=en
         body: {"appSecret": "...", "email": "...", "password": "sha256(password)"}
        -> {access_token, expires_in, uid}
    Bearer in subsequent calls.

Key endpoints (Phase 2 wiring):
    POST /station/v1.0/list
    POST /station/v1.0/realTime?stationId=...
    POST /device/v1.0/inverter/realTime?deviceSn=...
    POST /station/v1.0/history?stationId=...&timeType=HOUR&...

Phase 1 scope: credential schema + stub verify() that returns an honest
"stub" message. Replaced by full implementation once we have Solarman
appId/appSecret in hand for 1PWR's Solarman org.
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

logger = logging.getLogger("cc-api.gensite.adapter.solarman")


class SolarmanAdapter(InverterAdapter):
    vendor = "deye"  # canonical brand; REGISTRY also aliases under "solarman"
    display_name = "Deye / Solarman Business Cloud"
    implementation_status = "stub"

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="deye",
                backend="solarman",
                label="Solarman Business Cloud (Deye)",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret", "api_key"],
                extra_fields=["appid"],
                docs_url="https://doc.solarmanpv.com/web/#/118?page_id=258",
                notes=(
                    "'username' is the Solarman portal email. 'secret' is the "
                    "portal password (the adapter sha256s it before calling). "
                    "'api_key' is the Solarman appSecret (Business account). "
                    "'extra.appid' is the Solarman Business appId. "
                    "'site_id_on_vendor' is the Solarman stationId; leave blank "
                    "during verify to list available stations."
                ),
            ),
        ]

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.api_key or not cred.secret or not cred.username:
            return VerifyResult(
                ok=False,
                message=(
                    "Solarman adapter is a stub pending first commissioning. "
                    "Supply username + password + appSecret (and extra.appid) "
                    "to exercise it."
                ),
            )
        return VerifyResult(
            ok=False,
            message=(
                "Solarman adapter implementation is scheduled for Phase 1 "
                "step 4 (after Victron is proven end-to-end on GBO). "
                "Schema accepted — credentials will be stored encrypted."
            ),
        )

    def fetch_live(self, cred, equipment):
        raise AdapterError("Solarman fetch_live not yet implemented", retryable=False)

    def fetch_day(self, cred, equipment, day):
        return []

    def fetch_alarms(self, cred, equipment, since):
        return []
