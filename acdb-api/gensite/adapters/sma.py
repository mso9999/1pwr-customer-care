"""
SMA Sunny Portal adapter — PIH health centres in Lesotho.

Auth and data path (validated 2026-05):
1) OAuth2 password grant against login.sma.energy Keycloak realm.
2) Bearer token against uiapi.sunnyportal.com:
   - /api/v1/navigation
   - /api/v1/plants/{plantId}
   - /api/v1/measurements/{plantId}/energybalance?dateBeginLocal=YYYY-MM-DD
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

logger = logging.getLogger("cc-api.gensite.adapter.sma")

AUTH_TOKEN_URL = "https://login.sma.energy/auth/realms/SMA/protocol/openid-connect/token"
API_BASE = "https://uiapi.sunnyportal.com"
DEFAULT_CLIENT_ID = "SPpbeOS"
HTTP_TIMEOUT = 25


def _num(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _today_local_iso(tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _parse_utc_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _map_energybalance_row(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Map one SMA energybalance detail point (W values) to normalized kW fields."""
    pv_w = _num(row.get("pvGeneration") or row.get("totalGeneration"))
    load_w = _num(row.get("totalConsumption"))
    charge_w = _num(row.get("batteryCharging"))
    discharge_w = _num(row.get("batteryDischarging"))
    soc = _num(row.get("batteryStateOfCharge"))
    grid_import_w = _num(row.get("externalConsumption"))
    grid_export_w = _num(row.get("feedIn"))

    battery_kw = None
    if discharge_w is not None or charge_w is not None:
        battery_kw = ((discharge_w or 0.0) - (charge_w or 0.0)) / 1000.0

    grid_kw = None
    if grid_import_w is not None or grid_export_w is not None:
        grid_kw = ((grid_import_w or 0.0) - (grid_export_w or 0.0)) / 1000.0

    return {
        "pv_kw": (pv_w / 1000.0) if pv_w is not None else None,
        "ac_kw": (load_w / 1000.0) if load_w is not None else None,
        "battery_kw": battery_kw,
        "battery_soc_pct": soc,
        "grid_kw": grid_kw,
    }


class SMAAdapter(InverterAdapter):
    vendor = "sma"
    display_name = "SMA Sunny Portal"
    implementation_status = "ready"

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="sma",
                backend="sunny_portal",
                label="SMA Sunny Portal",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret"],
                extra_fields=[],
                docs_url="https://ennexos.sunnyportal.com",
                notes=(
                    "'username' is the Sunny Portal email; 'secret' is the portal "
                    "password. Optional 'site_id_on_vendor' is Sunny Portal plantId "
                    "(componentId from /api/v1/navigation)."
                ),
            ),
        ]

    def _client_id(self, cred: SiteCredential) -> str:
        val = (cred.extra or {}).get("client_id")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return DEFAULT_CLIENT_ID

    def _api_base(self, cred: SiteCredential) -> str:
        if cred.base_url and "http" in cred.base_url:
            # Commissioners sometimes store portal URL as base_url.
            # API host is fixed for portal accounts.
            return API_BASE
        return API_BASE

    def _token(self, cred: SiteCredential) -> str:
        if not cred.username or not cred.secret:
            raise AdapterError("SMA Sunny Portal requires username + password.", retryable=False)
        body = {
            "grant_type": "password",
            "client_id": self._client_id(cred),
            "username": cred.username,
            "password": cred.secret,
        }
        resp = requests.post(AUTH_TOKEN_URL, data=body, timeout=HTTP_TIMEOUT)
        if resp.status_code >= 400:
            msg = resp.text[:200]
            raise AdapterError(f"SMA auth failed ({resp.status_code}): {msg}", retryable=False, status=resp.status_code)
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise AdapterError("SMA auth response missing access_token.", retryable=False, status=401)
        return token

    def _api_get(self, cred: SiteCredential, token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = requests.get(
            f"{self._api_base(cred)}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            raise AdapterError("SMA token rejected by API.", retryable=True, status=401)
        if resp.status_code >= 400:
            raise AdapterError(
                f"SMA API {path} returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp.json()

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.username or not cred.secret:
            return VerifyResult(
                ok=False,
                message="SMA Sunny Portal requires username + password.",
            )
        try:
            token = self._token(cred)
            nav = self._api_get(cred, token, "/api/v1/navigation")
            plants = [p for p in (nav or []) if isinstance(p, dict) and p.get("componentType") == "Plant"]
            discovered = [
                {
                    "id": str(p.get("componentId", "")),
                    "name": str(p.get("name", "")),
                }
                for p in plants
                if p.get("componentId")
            ]
            if cred.site_id_on_vendor:
                target = str(cred.site_id_on_vendor)
                match = next((p for p in discovered if p["id"] == target), None)
                if not match:
                    return VerifyResult(
                        ok=False,
                        message=(
                            f"Authenticated, but plant {target} is not visible. "
                            f"Available: {[p['id'] for p in discovered]}"
                        ),
                        discovered_equipment=discovered,
                    )
                return VerifyResult(
                    ok=True,
                    message=f"Connected to SMA plant '{match['name']}'.",
                    discovered_site_id=target,
                    discovered_equipment=discovered,
                )
            if not discovered:
                return VerifyResult(
                    ok=False,
                    message="Authenticated, but no plants visible in /api/v1/navigation.",
                )
            return VerifyResult(
                ok=True,
                message=(
                    f"Authenticated. {len(discovered)} plant(s) visible. "
                    "Set site_id_on_vendor to plantId/componentId."
                ),
                discovered_site_id=discovered[0]["id"],
                discovered_equipment=discovered,
            )
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

    def _plant_id(self, cred: SiteCredential, token: str) -> Optional[str]:
        if cred.site_id_on_vendor:
            return str(cred.site_id_on_vendor)
        nav = self._api_get(cred, token, "/api/v1/navigation")
        for item in nav or []:
            if isinstance(item, dict) and item.get("componentType") == "Plant" and item.get("componentId"):
                return str(item["componentId"])
        return None

    def fetch_live(self, cred: SiteCredential, equipment: List[SiteEquipment]) -> List[LiveReading]:
        if not equipment:
            return []
        token = self._token(cred)
        plant_id = self._plant_id(cred, token)
        if not plant_id:
            return []
        plant = self._api_get(cred, token, f"/api/v1/plants/{plant_id}")
        tz_name = str(plant.get("timezone") or "UTC")
        day_iso = _today_local_iso(tz_name)
        energy = self._api_get(
            cred,
            token,
            f"/api/v1/measurements/{plant_id}/energybalance",
            params={"dateBeginLocal": day_iso},
        )
        detail = energy.get("detail") if isinstance(energy, dict) else None
        if not detail or not isinstance(detail, list):
            return []

        latest = None
        for row in detail:
            if isinstance(row, dict):
                latest = row
        if not latest:
            return []

        ts = _parse_utc_ts(latest.get("timeUtc")) or datetime.now(timezone.utc)
        mapped = _map_energybalance_row(latest)

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])
        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts,
                pv_kw=mapped["pv_kw"],
                ac_kw=mapped["ac_kw"],
                battery_kw=mapped["battery_kw"],
                battery_soc_pct=mapped["battery_soc_pct"],
                grid_kw=mapped["grid_kw"],
                raw_json={
                    "plant": {
                        "id": plant_id,
                        "name": plant.get("name"),
                        "timezone": tz_name,
                    },
                    "energybalance_latest": latest,
                    "energybalance_total": energy.get("total"),
                },
            )
        ]

    def fetch_day(self, cred: SiteCredential, equipment: List[SiteEquipment], day: date) -> List[IntervalReading]:
        if not equipment:
            return []
        token = self._token(cred)
        plant_id = self._plant_id(cred, token)
        if not plant_id:
            return []
        energy = self._api_get(
            cred,
            token,
            f"/api/v1/measurements/{plant_id}/energybalance",
            params={"dateBeginLocal": day.isoformat()},
        )
        detail = energy.get("detail") if isinstance(energy, dict) else None
        if not detail or not isinstance(detail, list):
            return []

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])
        out: List[IntervalReading] = []
        for row in detail:
            if not isinstance(row, dict):
                continue
            ts = _parse_utc_ts(row.get("timeUtc"))
            if ts is None:
                continue
            mapped = _map_energybalance_row(row)
            out.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=ts,
                    pv_kw=mapped["pv_kw"],
                    ac_kw=mapped["ac_kw"],
                    battery_kw=mapped["battery_kw"],
                    battery_soc_pct=mapped["battery_soc_pct"],
                    grid_kw=mapped["grid_kw"],
                    raw_json=row,
                )
            )
        return out

    def fetch_alarms(self, cred: SiteCredential, equipment: List[SiteEquipment], since: datetime) -> List[AlarmEvent]:
        # Keep conservative no-op until we have a stable SMA event endpoint with
        # durable timestamps/codes suitable for deduplicated alarm ingestion.
        return []
