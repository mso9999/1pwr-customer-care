"""
AlphaESS Cloud API adapter.

Backend: https://cloud.alphaess.com/api
Docs:    https://open.alphaess.com/developmentManagement/apiList (OpenAPI, separate)

Auth flow (session-based, not API key):
    Every request carries two custom headers computed from a static key:
        authtimestamp = str(int(now_epoch))
        authsignature = "al8e4s" + SHA512(AUTH_CONSTANT + authtimestamp) + "ui893ed"

    POST /Account/Login  {username, password}
        -> {info: "Success", data: {AccessToken, ExpiresIn, RefreshTokenKey}}
    Subsequent calls: Authorization: Bearer {AccessToken} + auth headers

Key endpoints:
    GET  /Account/GetCustomMenuESSList              — list registered systems
    GET  /ESS/GetLastPowerDataBySN?sys_sn=...       — real-time power snapshot
    GET  /Power/SticsByPeriod?beginDay=&endDay=...   — daily energy data
    POST /Statistic/SystemStatistic                 — monthly stats

No alarm/fault endpoint exists in the Cloud API; fetch_alarms() returns [].
"""

from __future__ import annotations

import hashlib
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

ALPHAESS_BASE = "https://cloud.alphaess.com/api"
HTTP_TIMEOUT = 20

# Hardcoded static key used by the AlphaESS web portal for auth signatures.
# Extracted from the MIT-licensed CharlesGillanders/alphaess library.
_AUTH_CONSTANT = "LS885ZYDA95JVFQKUIUUUV7PQNODZRDZIS4ERREDS0EED8BCWSS"


def _make_auth_headers() -> Dict[str, str]:
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    digest = hashlib.sha512((_AUTH_CONSTANT + ts).encode()).hexdigest()
    return {
        "authtimestamp": ts,
        "authsignature": f"al8e4s{digest}ui893ed",
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
                label="AlphaESS Cloud",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret"],
                extra_fields=[],
                docs_url="https://open.alphaess.com/developmentManagement/apiList",
                notes=(
                    "'username' is the AlphaESS portal account name. "
                    "'secret' is the portal password. "
                    "'site_id_on_vendor' is the inverter serial number (sys_sn); "
                    "leave blank during verify to list available systems."
                ),
            ),
        ]

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers["Accept"] = "application/json"
        s.headers["User-Agent"] = "1PWR-CC-gensite/1.0"
        return s

    def _login(self, cred: SiteCredential, session: requests.Session) -> None:
        """Authenticate and store Bearer token on the session."""
        if not cred.username or not cred.secret:
            raise AdapterError(
                "AlphaESS requires username and password", retryable=False
            )

        base = (cred.base_url or ALPHAESS_BASE).rstrip("/")
        resp = session.post(
            f"{base}/Account/Login",
            json={"username": cred.username, "password": cred.secret},
            headers=_make_auth_headers(),
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            raise AdapterError(
                "AlphaESS rejected credentials", retryable=False, status=401
            )
        if resp.status_code >= 400:
            raise AdapterError(
                f"AlphaESS auth returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )

        body = resp.json() or {}
        info = body.get("info", "")
        if info != "Success":
            raise AdapterError(
                f"AlphaESS auth failed: {info or body}", retryable=False
            )

        data = body.get("data") or {}
        token = data.get("AccessToken")
        if not token:
            raise AdapterError(
                f"AlphaESS auth: no AccessToken in response", retryable=False
            )
        session.headers["Authorization"] = f"Bearer {token}"

    def _api_get(self, session: requests.Session, base: str, path: str) -> Dict[str, Any]:
        """Authenticated GET with per-request auth signature headers."""
        resp = session.get(
            f"{base}/{path}",
            headers=_make_auth_headers(),
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            raise AdapterError(
                "AlphaESS token expired", retryable=True, status=401
            )
        if resp.status_code >= 400:
            raise AdapterError(
                f"AlphaESS {path} returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        body = resp.json() or {}
        info = body.get("info", "")
        if info != "Success":
            raise AdapterError(
                f"AlphaESS {path}: {info or 'unknown error'}",
            )
        return body.get("data") or {}

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def verify(self, cred: SiteCredential) -> VerifyResult:
        try:
            session = self._session()
            self._login(cred, session)
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

        base = (cred.base_url or ALPHAESS_BASE).rstrip("/")
        try:
            data = self._api_get(session, base, "Account/GetCustomMenuESSList")
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

        systems = data if isinstance(data, list) else []
        summary = [
            {
                "id": str(s.get("sys_sn", "")),
                "name": str(s.get("sys_sn", "")),
            }
            for s in systems
        ]

        if cred.site_id_on_vendor:
            target = str(cred.site_id_on_vendor)
            match = next((s for s in summary if s["id"] == target), None)
            if match is None:
                return VerifyResult(
                    ok=False,
                    message=(
                        f"Authenticated, but sys_sn={target} not found. "
                        f"Available: {[s['id'] for s in summary]}"
                    ),
                    discovered_equipment=summary,
                )
            return VerifyResult(
                ok=True,
                message=f"Connected to AlphaESS system '{target}'.",
                discovered_site_id=target,
                discovered_equipment=summary,
            )

        return VerifyResult(
            ok=True,
            message=(
                f"Authenticated. {len(summary)} system(s) visible. "
                "Pick one and set site_id_on_vendor to its sys_sn."
            ),
            discovered_equipment=summary,
        )

    # ------------------------------------------------------------------
    # fetch_live
    # ------------------------------------------------------------------

    def fetch_live(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
    ) -> List[LiveReading]:
        if not cred.site_id_on_vendor:
            raise AdapterError(
                "AlphaESS fetch_live requires site_id_on_vendor (sys_sn)",
                retryable=False,
            )
        if not equipment:
            return []

        session = self._session()
        self._login(cred, session)
        base = (cred.base_url or ALPHAESS_BASE).rstrip("/")

        data = self._api_get(
            session,
            base,
            f"ESS/GetLastPowerDataBySN?noLoading=true&sys_sn={cred.site_id_on_vendor}",
        )

        ts = datetime.now(timezone.utc)
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        # AlphaESS returns power in Watts; convert to kW for LiveReading
        def _kw(key: str) -> Optional[float]:
            val = data.get(key)
            if val is None:
                return None
            try:
                return float(val) / 1000.0
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

        session = self._session()
        self._login(cred, session)
        base = (cred.base_url or ALPHAESS_BASE).rstrip("/")

        day_str = day.isoformat()
        data = self._api_get(
            session,
            base,
            f"Power/SticsByPeriod?beginDay={day_str}&endDay={day_str}"
            f"&tDay={day_str}&isOEM=0&SN={cred.site_id_on_vendor}&noLoading=true",
        )

        records = data if isinstance(data, list) else []
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        out: List[IntervalReading] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            ts_str = rec.get("tDay") or rec.get("date") or day_str
            try:
                ts = datetime.strptime(
                    ts_str, "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

            def _kw_day(key: str) -> Optional[float]:
                val = rec.get(key)
                if val is None:
                    return None
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None

            out.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=ts,
                    pv_kw=_kw_day("epv"),
                    ac_kw=_kw_day("eOutput"),
                    battery_kw=_kw_day("eCharge"),
                    grid_kw=_kw_day("eGridCharge"),
                    raw_json=rec,
                )
            )
        return out

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
