"""
DeyeCloud OpenAPI adapter — eu1-developer.deyecloud.com (2026-05).

Portal:  https://eu1-developer.deyecloud.com
API:     https://eu1-developer.deyecloud.com
Docs:    https://eu1-developer.deyecloud.com/v2/api-docs

Auth flow (3-step):
  1. POST /v1.0/account/token?appId={appId}
     body: {appSecret, email, password: SHA256(password)}
     -> personal access token (60-day expiry)
  2. POST /v1.0/account/info with personal token
     -> orgInfoList[{companyId, companyName, roleName}]
  3. POST /v1.0/account/token?appId={appId}
     body: {appSecret, email, password: SHA256(password), companyId}
     -> business access token (60-day expiry)

Data (device-level, generic key-value pairs):
    POST /v1.0/device/list        -> {deviceList: [{deviceSn, deviceType, ...}]}
    POST /v1.0/device/measurePoints -> {measurePoints: ["SOC", "TotalPV", ...]}
    POST /v1.0/device/latest      -> {deviceDataList: [{deviceSn, dataList: [{key, value, unit}]}]}
    POST /v1.0/device/history     -> history at day/month/year granularity
    POST /v1.0/device/alertList   -> alerts by time range
"""

from __future__ import annotations

import hashlib
import logging
import time
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

logger = logging.getLogger("cc-api.gensite.adapter.deye")

API_BASE = "https://eu1-developer.deyecloud.com"
HTTP_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Common DeyeCloud measure-point keys mapped to our reading fields.
# Keys are matched case-insensitively against dataList entries from /device/latest.
# Values are (field_name, conversion) where conversion is applied to the value.
# "identity" = pass through; "W_to_kW" = divide by 1000.
# ---------------------------------------------------------------------------

_KEY_MAP: Dict[str, tuple] = {
    # Battery SOC
    "soc": ("battery_soc_pct", "identity"),
    # PV power (Watts -> kW)
    "totalpv": ("pv_kw", "W_to_kW"),
    "pv1 power": ("pv_kw", "W_to_kW"),
    "pv2 power": ("pv_kw", "W_to_kW"),
    "pvpower": ("pv_kw", "W_to_kW"),
    "pv power": ("pv_kw", "W_to_kW"),
    "totalsolarpower": ("pv_kw", "W_to_kW"),
    "dcpowerpv1": ("pv_kw", "W_to_kW"),
    "dcpowerpv2": ("pv_kw", "W_to_kW"),
    "dcpowerpv3": ("pv_kw", "W_to_kW"),
    "dcpowerpv4": ("pv_kw", "W_to_kW"),
    # Load/consumption power (Watts -> kW)
    "totalload": ("ac_kw", "W_to_kW"),
    "loadpower": ("ac_kw", "W_to_kW"),
    "load power": ("ac_kw", "W_to_kW"),
    "totalconsumptionpower": ("ac_kw", "W_to_kW"),
    "totalinverteroutputpower": ("ac_kw", "W_to_kW"),
    "upsloadpower": ("ac_kw", "W_to_kW"),
    # Battery power (Watts -> kW; positive=discharge, negative=charge)
    "batterypower": ("battery_kw", "W_to_kW"),
    "battery power": ("battery_kw", "W_to_kW"),
    # Grid power (Watts -> kW; positive=import, negative=export)
    "gridpower": ("grid_kw", "W_to_kW"),
    "grid power": ("grid_kw", "W_to_kW"),
    "totalgridpower": ("grid_kw", "W_to_kW"),
    # AC voltage (average of phases)
    "mi voltage l1": ("ac_v_l1", "identity"),
    "mi voltage l2": ("ac_v_l2", "identity"),
    "mi voltage l3": ("ac_v_l3", "identity"),
    "acvoltagerua": ("ac_v_l1", "identity"),
    "acvoltagesvb": ("ac_v_l2", "identity"),
    "acvoltagetwc": ("ac_v_l3", "identity"),
    "loadvoltagel1": ("ac_v_l1", "identity"),
    "loadvoltagel2": ("ac_v_l2", "identity"),
    "loadvoltagel3": ("ac_v_l3", "identity"),
    # Frequency
    "mi frequency": ("ac_freq_hz", "identity"),
    "acoutputfrequencyr": ("ac_freq_hz", "identity"),
    "loadfrequency": ("ac_freq_hz", "identity"),
    # Total energy (kWh — kept as-is)
    "totalchargeenergy": ("battery_charge_kwh", "identity"),
    "totaldischargeenergy": ("battery_discharge_kwh", "identity"),
    "totalgeneration": ("pv_kwh_total", "identity"),
    "totalgridimport": ("grid_import_kwh", "identity"),
    "totalgridexport": ("grid_export_kwh", "identity"),
}


def _num(val: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _map_reading(data_list: List[dict]) -> Dict[str, float]:
    """Convert a device/latest dataList into a dict of our field names -> float values.

    Each entry in data_list is {"key": "...", "value": "...", "unit": "..."}.
    Unknown keys are silently skipped.
    """
    result: Dict[str, float] = {}
    ac_v_vals: List[float] = []

    for point in data_list:
        key = (point.get("key") or "").strip().lower()
        raw = point.get("value")
        val = _num(raw)
        if val is None:
            continue

        if key in _KEY_MAP:
            field, conv = _KEY_MAP[key]
            if conv == "W_to_kW":
                val = val / 1000.0
            if field.startswith("ac_v_l"):
                ac_v_vals.append(val)
            else:
                result[field] = val

    # Average phase voltages into ac_v_avg
    if ac_v_vals:
        result["ac_v_avg"] = sum(ac_v_vals) / len(ac_v_vals)

    return result


def _extract_device_capacity(device: Dict[str, Any]) -> Dict[str, Optional[float]]:
    inverter_kw: Optional[float] = None
    pv_kw: Optional[float] = None
    battery_kw: Optional[float] = None
    battery_kwh: Optional[float] = None

    for key, raw in (device or {}).items():
        lk = str(key).lower()
        n = _num(raw)
        if n is None or n <= 0:
            continue

        # Normalize likely W values when clearly too large for kW nameplate.
        if "kw" not in lk and "kwh" not in lk and n > 10000:
            n = n / 1000.0

        if "battery" in lk and any(x in lk for x in ("kwh", "energy", "capacity")):
            battery_kwh = max(battery_kwh or 0.0, n)
            continue
        if "battery" in lk and any(x in lk for x in ("power", "kw", "rated", "nominal", "max")):
            battery_kw = max(battery_kw or 0.0, n)
            continue
        if any(x in lk for x in ("pv", "solar")) and any(x in lk for x in ("power", "kw", "rated", "nominal", "capacity", "peak", "max")):
            pv_kw = max(pv_kw or 0.0, n)
            continue
        if any(x in lk for x in ("rated", "nominal", "max", "capacity", "nameplate")) and any(x in lk for x in ("power", "kw", "kva")):
            inverter_kw = max(inverter_kw or 0.0, n)
            continue

    return {
        "inverter_kw": inverter_kw,
        "pv_kw": pv_kw,
        "battery_kw": battery_kw,
        "battery_kwh": battery_kwh,
    }


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


class _TokenCache:
    """In-memory token cache. Tokens live 60 days, so cache across poll cycles."""

    def __init__(self):
        self.business_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0.0

    def get(self) -> Optional[str]:
        if self.business_token and time.time() < self.expires_at - 300:
            return self.business_token
        return None

    def set(self, access_token: str, refresh_token: str, expires_in: int):
        self.business_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = time.time() + expires_in

    def clear(self):
        self.business_token = None
        self.refresh_token = None
        self.expires_at = 0.0


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SolarmanAdapter(InverterAdapter):
    vendor = "deye"
    display_name = "DeyeCloud"
    implementation_status = "ready"

    def __init__(self):
        self._cache = _TokenCache()

    # ------------------------------------------------------------------
    # credential_specs
    # ------------------------------------------------------------------

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="deye",
                backend="deyecloud",
                label="DeyeCloud (eu1-developer.deyecloud.com)",
                plain_fields=["username", "site_id_on_vendor"],
                secret_fields=["secret", "api_key"],
                extra_fields=["appid", "companyid"],
                docs_url="https://eu1-developer.deyecloud.com/v2/api-docs",
                notes=(
                    "'username' is the DeyeCloud account email. "
                    "'secret' is the DeyeCloud account password (SHA256'd for auth). "
                    "'api_key' is the appSecret from creating a DeyeCloud application. "
                    "'extra.appid' is the appId from the DeyeCloud application. "
                    "'extra.companyid' is discovered during verify — set automatically. "
                    "'site_id_on_vendor' is the device serial number (deviceSn)."
                ),
            ),
        ]

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_token(self, cred: SiteCredential) -> str:
        """Return a valid business access token."""
        cached = self._cache.get()
        if cached:
            return cached

        # Try refresh
        if self._cache.refresh_token:
            try:
                return self._refresh_token()
            except AdapterError:
                self._cache.clear()

        return self._full_auth(cred)

    def _full_auth(self, cred: SiteCredential) -> str:
        """3-step auth: personal token -> companyId -> business token."""
        app_id = (cred.extra or {}).get("appid", "")
        app_secret = cred.api_key or ""
        email = cred.username or ""
        password = cred.secret or ""

        if not all([app_id, app_secret, email, password]):
            raise AdapterError(
                "DeyeCloud requires appid (extra), api_key (appSecret), "
                "username (email), and secret (password).",
                retryable=False,
            )

        pw_hash = hashlib.sha256(password.encode()).hexdigest()

        # Step 1: personal token
        resp = requests.post(
            f"{API_BASE}/v1.0/account/token?appId={app_id}",
            json={"appSecret": app_secret, "email": email, "password": pw_hash},
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json()
        if not data.get("success"):
            raise AdapterError(
                f"DeyeCloud auth failed: {data.get('msg', 'unknown')}",
                retryable=False,
                status=401,
            )
        personal_token = data["accessToken"]

        # Step 2: discover companyId
        company_id = (cred.extra or {}).get("companyid")
        if not company_id:
            company_id = self._discover_company_id(personal_token)

        # Step 3: business token
        resp = requests.post(
            f"{API_BASE}/v1.0/account/token?appId={app_id}",
            json={
                "appSecret": app_secret,
                "email": email,
                "password": pw_hash,
                "companyId": int(company_id),
            },
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json()
        if not data.get("success"):
            raise AdapterError(
                f"DeyeCloud business auth failed: {data.get('msg', 'unknown')}",
                retryable=False,
                status=401,
            )

        self._cache.set(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken", ""),
            expires_in=int(data.get("expiresIn", 5183999)),
        )
        logger.info("DeyeCloud: business token obtained (companyId=%s, expiresIn=%ss)",
                     company_id, data.get("expiresIn"))
        return data["accessToken"]

    def _refresh_token(self) -> str:
        """Not directly supported by this API — re-auth instead."""
        self._cache.clear()
        raise AdapterError("DeyeCloud token refresh needed", retryable=True)

    def _discover_company_id(self, personal_token: str) -> str:
        """Call /account/info to get the first companyId."""
        resp = requests.post(
            f"{API_BASE}/v1.0/account/info",
            headers={"Authorization": f"Bearer {personal_token}"},
            json={},
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json()
        orgs = (data.get("orgInfoList") or []) if data.get("success") else []
        if not orgs:
            raise AdapterError(
                "DeyeCloud account has no organizations. "
                "Create an organization in the DeyeCloud portal first.",
                retryable=False,
            )
        return str(orgs[0]["companyId"])

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _api_post(
        self,
        cred: SiteCredential,
        path: str,
        body: dict = None,
    ) -> Dict[str, Any]:
        """Authenticated POST. Returns the full JSON response (not just data)."""
        token = self._get_token(cred)
        resp = requests.post(
            f"{API_BASE}{path}",
            json=body or {},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json()
        if not data.get("success"):
            code = data.get("code", "")
            msg = data.get("msg", "unknown error")
            if code in ("2101017", "2101019", "2101018"):
                self._cache.clear()
                raise AdapterError(f"DeyeCloud auth: {msg}", retryable=True, status=401)
            raise AdapterError(f"DeyeCloud {path}: {msg}", status=resp.status_code)
        return data

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def verify(self, cred: SiteCredential) -> VerifyResult:
        extra = cred.extra or {}
        if not cred.username:
            return VerifyResult(ok=False, message="DeyeCloud requires 'username' (account email).")
        if not cred.secret:
            return VerifyResult(ok=False, message="DeyeCloud requires 'secret' (account password).")
        if not cred.api_key:
            return VerifyResult(ok=False, message="DeyeCloud requires 'api_key' (appSecret).")
        if not extra.get("appid"):
            return VerifyResult(ok=False, message="DeyeCloud requires 'extra.appid' (appId).")

        company_id = extra.get("companyid")
        if not company_id:
            try:
                # Just get personal token and discover companyId
                pw_hash = hashlib.sha256(cred.secret.encode()).hexdigest()
                r = requests.post(
                    f"{API_BASE}/v1.0/account/token?appId={extra['appid']}",
                    json={
                        "appSecret": cred.api_key,
                        "email": cred.username,
                        "password": pw_hash,
                    },
                    timeout=HTTP_TIMEOUT,
                )
                data = r.json()
                if not data.get("success"):
                    return VerifyResult(ok=False, message=f"Auth failed: {data.get('msg', 'unknown')}")

                company_id = self._discover_company_id(data["accessToken"])
            except AdapterError as exc:
                return VerifyResult(ok=False, message=str(exc))

        # Try listing devices with business token
        try:
            dev_data = self._api_post(cred, "/v1.0/device/list", {"page": 1, "size": 50})
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

        devices = dev_data.get("deviceList") or []
        equipment = [
            {
                "id": d.get("deviceSn", ""),
                "name": f"{d.get('deviceType', 'INVERTER')} {d.get('deviceSn', '')}",
            }
            for d in devices
        ]

        if cred.site_id_on_vendor:
            target = cred.site_id_on_vendor
            match = next((e for e in equipment if e["id"] == target), None)
            if match is None:
                return VerifyResult(
                    ok=False,
                    message=(
                        f"Authenticated, but device {target} not found. "
                        f"Available: {[e['id'] for e in equipment]}"
                    ),
                    discovered_equipment=equipment,
                )
            return VerifyResult(
                ok=True,
                message=f"Connected. Device '{target}' found.",
                discovered_site_id=target,
                discovered_equipment=equipment,
            )

        return VerifyResult(
            ok=True,
            message=(
                f"Authenticated. {dev_data.get('total', len(equipment))} device(s) found. "
                "Set site_id_on_vendor to a device serial (deviceSn)."
            ),
            discovered_equipment=equipment,
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

        data = self._api_post(
            cred,
            "/v1.0/device/latest",
            {"deviceList": [cred.site_id_on_vendor]},
        )

        device_list = data.get("deviceDataList") or []
        if not device_list:
            return []

        device = device_list[0]
        ts_utc = datetime.now(timezone.utc)
        collection = device.get("collectionTime")
        if collection:
            try:
                ts_utc = datetime.fromtimestamp(int(collection), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])
        mapped = _map_reading(device.get("dataList") or [])

        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts_utc,
                pv_kw=mapped.get("pv_kw"),
                battery_kw=mapped.get("battery_kw"),
                ac_kw=mapped.get("ac_kw"),
                grid_kw=mapped.get("grid_kw"),
                battery_soc_pct=mapped.get("battery_soc_pct"),
                ac_v_avg=mapped.get("ac_v_avg"),
                ac_freq_hz=mapped.get("ac_freq_hz"),
                raw_json=device,
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

        # First, discover available measure points for this device
        mp_data = self._api_post(
            cred,
            "/v1.0/device/measurePoints",
            {"deviceSn": cred.site_id_on_vendor, "deviceType": "INVERTER"},
        )
        all_points = mp_data.get("measurePoints") or []

        # Filter to energy-relevant points
        energy_points = [
            p for p in all_points
            if any(kw in (p or "").lower() for kw in
                   ["energy", "generation", "charge", "discharge", "consumption",
                    "gridimport", "gridexport", "soc", "totalpv", "totalload"])
        ]
        # Fall back to all points if nothing matched
        if not energy_points:
            energy_points = all_points

        # Fetch daily history
        data = self._api_post(
            cred,
            "/v1.0/device/history",
            {
                "deviceSn": cred.site_id_on_vendor,
                "granularity": 1,
                "startAt": day.isoformat(),
                "measurePoints": energy_points,
            },
        )

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])
        readings: List[IntervalReading] = []

        # Response structure: may contain daily stats or list of intervals
        # Try common response shapes
        history_list = data.get("historyList") or data.get("dataList") or []

        for point in history_list:
            ts = point.get("timestamp") or point.get("collectionTime") or point.get("time")
            if ts:
                try:
                    ts_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    ts_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            else:
                ts_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

            # Map data points in this interval
            child_list = point.get("dataList") or []
            mapped = _map_reading(child_list)

            readings.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=ts_utc,
                    pv_kw=mapped.get("pv_kw"),
                    battery_kw=mapped.get("battery_kw"),
                    ac_kw=mapped.get("ac_kw"),
                    grid_kw=mapped.get("grid_kw"),
                    battery_soc_pct=mapped.get("battery_soc_pct"),
                    raw_json=point,
                )
            )

        # If history returned nothing structured, return daily-summary row
        if not readings:
            mapped = _map_reading(history_list if isinstance(history_list, list) else [])
            readings.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                    battery_soc_pct=mapped.get("battery_soc_pct"),
                    raw_json=data,
                )
            )

        return readings

    # ------------------------------------------------------------------
    # fetch_alarms
    # ------------------------------------------------------------------

    def fetch_alarms(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        since: datetime,
    ) -> List[AlarmEvent]:
        if not cred.site_id_on_vendor:
            return []

        try:
            data = self._api_post(
                cred,
                "/v1.0/device/alertList",
                {
                    "deviceSn": cred.site_id_on_vendor,
                    "startTimestamp": int(since.timestamp()),
                    "endTimestamp": int(datetime.now(timezone.utc).timestamp()),
                },
            )
        except AdapterError:
            return []

        alerts = data.get("alertList") or []
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0]) if equipment else None

        severity_map = {"1": "critical", "2": "warning", "3": "info"}
        results: List[AlarmEvent] = []

        for a in alerts:
            start_ts = a.get("alertStartTime")
            end_ts = a.get("alertEndTime")

            results.append(
                AlarmEvent(
                    equipment_id=target.id if target else None,
                    site_code=cred.site_code,
                    vendor_code=a.get("alertCode"),
                    vendor_msg=a.get("alertName"),
                    severity=severity_map.get(str(a.get("alertCode", "")), "info"),
                    raised_at=datetime.fromtimestamp(int(start_ts), tz=timezone.utc) if start_ts else since,
                    cleared_at=datetime.fromtimestamp(int(end_ts), tz=timezone.utc) if end_ts else None,
                    event_json=a,
                )
            )
        return results

    def discover_installed_capacity(self, cred: SiteCredential) -> List[Dict[str, Any]]:
        dev_data = self._api_post(cred, "/v1.0/device/list", {"page": 1, "size": 200})
        devices = dev_data.get("deviceList") or []
        if not isinstance(devices, list):
            return []

        target_sn = str(cred.site_id_on_vendor or "").strip()
        if target_sn:
            devices = [d for d in devices if str(d.get("deviceSn") or "").strip() == target_sn]
            if not devices:
                return []

        inv_kw_vals: List[float] = []
        pv_kw_vals: List[float] = []
        batt_kw_vals: List[float] = []
        batt_kwh_vals: List[float] = []

        for d in devices:
            if not isinstance(d, dict):
                continue
            caps = _extract_device_capacity(d)
            if caps["inverter_kw"]:
                inv_kw_vals.append(caps["inverter_kw"])
            if caps["pv_kw"]:
                pv_kw_vals.append(caps["pv_kw"])
            if caps["battery_kw"]:
                batt_kw_vals.append(caps["battery_kw"])
            if caps["battery_kwh"]:
                batt_kwh_vals.append(caps["battery_kwh"])

        out: List[Dict[str, Any]] = []
        if inv_kw_vals:
            out.append({"kind": "inverter", "nameplate_kw": max(inv_kw_vals)})
        if pv_kw_vals:
            out.append({"kind": "pv_array", "role": "pv", "nameplate_kw": max(pv_kw_vals)})
        if batt_kw_vals or batt_kwh_vals:
            out.append(
                {
                    "kind": "battery",
                    "role": "battery",
                    "nameplate_kw": max(batt_kw_vals) if batt_kw_vals else None,
                    "nameplate_kwh": max(batt_kwh_vals) if batt_kwh_vals else None,
                }
            )
        return out
