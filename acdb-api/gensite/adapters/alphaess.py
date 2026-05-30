"""
AlphaESS Cloud API adapter — new architecture (2026).

Portal:  https://cloud.alphaess.com
API:     https://sgcloud.alphaess.com/api (Singapore DC; varies by JWT sdc claim)

Auth: JWT Bearer token in ``authorization`` header.
Token is obtained by logging into cloud.alphaess.com (MFA required) and
extracting it from browser DevTools. Token is stored in the credential
secret field; the adapter does not perform login itself.

Required headers: authorization, platform (AK9D8H), system (alphacloud),
origin (cloud.alphaess.com).

Data:
    GET /api/report/energyStorage/getLastPowerData?sysSn=...
        -> ppv, pload, pgrid, pbat (all Watts), soc (percent)
    GET /api/report/energy/getEnergyStatistics?sysSn=...&beginDate=...&endDate=...
        -> epvT, eload, echarge, edischarge, einput (all kWh)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import requests

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

logger = logging.getLogger("cc-api.gensite.adapter.alphaess")

API_BASE = "https://sgcloud.alphaess.com/api"
HTTP_TIMEOUT = 20
_PLATFORM = "AK9D8H"


def _make_headers(token: str) -> Dict[str, str]:
    return {
        "authorization": token,
        "platform": _PLATFORM,
        "system": "alphacloud",
        "origin": "https://cloud.alphaess.com",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US",
        "user-agent": "1PWR-CC-gensite/1.0",
    }


class AlphaESSAdapter(InverterAdapter):
    vendor = "alphaess"
    display_name = "AlphaESS Cloud"
    implementation_status = "ready"

    # ------------------------------------------------------------------
    # credential_specs
    # ------------------------------------------------------------------

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="alphaess",
                backend="alphaess_cloud",
                label="AlphaESS Cloud (sgcloud.alphaess.com)",
                plain_fields=["site_id_on_vendor"],
                secret_fields=["secret"],
                extra_fields=[],
                docs_url="https://cloud.alphaess.com",
                notes=(
                    "'secret' is a JWT token from logging into cloud.alphaess.com. "
                    "Open DevTools → Network → copy the 'authorization' header from "
                    "any /api/ request. "
                    "'site_id_on_vendor' is the inverter serial number (sys_sn), "
                    "e.g. AE6010520060002."
                ),
            ),
        ]

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _api_get(self, path: str, params: dict = None) -> Dict[str, Any]:
        """Authenticated GET to sgcloud.alphaess.com/api."""
        token = self._token
        if not token:
            raise AdapterError("AlphaESS: no token available", retryable=False)
        resp = requests.get(
            f"{API_BASE}/{path}",
            params=params or {},
            headers=_make_headers(token),
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            raise AdapterError("AlphaESS token expired", retryable=True, status=401)
        if resp.status_code >= 400:
            raise AdapterError(
                f"AlphaESS {path} returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        body = resp.json() or {}
        if body.get("code") != 200:
            raise AdapterError(
                f"AlphaESS {path}: {body.get('msg', 'unknown error')}",
            )
        return body.get("data") or {}

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.secret:
            return VerifyResult(ok=False, message="AlphaESS requires a JWT token in 'secret'. See credential notes.")

        if not cred.site_id_on_vendor:
            return VerifyResult(
                ok=False,
                message="AlphaESS requires 'site_id_on_vendor' (inverter serial number, e.g. AE6010520060002).",
            )

        self._token = cred.secret
        try:
            data = self._api_get(
                "report/energyStorage/getLastPowerData",
                params={"sysSn": cred.site_id_on_vendor},
            )
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

        if not data:
            return VerifyResult(ok=False, message="Token accepted but no data returned for this sysSn.")

        return VerifyResult(
            ok=True,
            message=f"Connected to AlphaESS system '{cred.site_id_on_vendor}'. SoC={data.get('soc')}%",
            discovered_site_id=cred.site_id_on_vendor,
            discovered_equipment=[
                {"id": cred.site_id_on_vendor, "name": cred.site_id_on_vendor}
            ],
        )

    # ------------------------------------------------------------------
    # fetch_live
    # ------------------------------------------------------------------

    def fetch_live(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
    ) -> List[LiveReading]:
        if not cred.site_id_on_vendor or not equipment:
            return []

        self._token = cred.secret
        data = self._api_get(
            "report/energyStorage/getLastPowerData",
            params={"sysSn": cred.site_id_on_vendor},
        )

        ts = datetime.now(timezone.utc)
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        def _kw(key: str) -> Optional[float]:
            val = data.get(key)
            if val is None:
                return None
            try:
                return float(val) / 1000.0  # Watts -> kW
            except (TypeError, ValueError):
                return None

        def _pct(key: str) -> Optional[float]:
            val = data.get(key)
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts,
                pv_kw=_kw("ppv"),
                battery_kw=_kw("pbat"),
                ac_kw=_kw("pload"),
                grid_kw=_kw("pgrid"),
                battery_soc_pct=_pct("soc"),
                raw_json=data,
            )
        ]

    # ------------------------------------------------------------------
    # fetch_day
    # ------------------------------------------------------------------

    def fetch_day(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        day: date,
    ) -> List[IntervalReading]:
        if not cred.site_id_on_vendor or not equipment:
            return []

        self._token = cred.secret
        day_str = day.isoformat()
        data = self._api_get(
            "report/energy/getEnergyStatistics",
            params={
                "sysSn": cred.site_id_on_vendor,
                "beginDate": day_str,
                "endDate": day_str,
            },
        )

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        def _kw(key: str) -> Optional[float]:
            val = data.get(key)
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        return [
            IntervalReading(
                equipment_id=target.id,
                ts_utc=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                pv_kw=_kw("epvT"),
                ac_kw=_kw("eload"),
                battery_kw=_kw("edischarge"),
                grid_kw=_kw("einput"),
                battery_soc_pct=_kw("soc"),
                raw_json=data,
            )
        ]

    # ------------------------------------------------------------------
    # fetch_alarms
    # ------------------------------------------------------------------

    def fetch_alarms(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        since: datetime,
    ) -> List[AlarmEvent]:
        return []
