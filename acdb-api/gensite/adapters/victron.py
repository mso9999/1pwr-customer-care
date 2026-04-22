"""
Victron VRM Portal adapter — covers GBO (Benin).

Docs: https://vrm-api-docs.victronenergy.com/
Base: https://vrmapi.victronenergy.com/v2/

Auth flow (password login):
    POST /auth/login  {username, password}
        -> {token, idUser}
    subsequent requests: Header  X-Authorization: Bearer {token}

Token-auth variant (preferred once ops creates a VRM service token):
    POST /auth/loginAsDemo is NOT the path — for tokens set X-Authorization
    directly and skip /auth/login.

Key endpoints:
    GET  /users/{idUser}/installations
    GET  /installations/{idSite}/diagnostics        (current-state summary)
    GET  /installations/{idSite}/widgets/Status     (dashboard data, includes AC load / PV / battery)
    GET  /installations/{idSite}/stats?interval=...&start=...&end=...

Phase 1 scope: verify() + minimal fetch_live(). fetch_day / fetch_alarms
are stubbed for now — they plug in with one or two more endpoint calls.
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

logger = logging.getLogger("cc-api.gensite.adapter.victron")

VRM_BASE = "https://vrmapi.victronenergy.com/v2"
HTTP_TIMEOUT = 20


class VictronAdapter(InverterAdapter):
    vendor = "victron"
    display_name = "Victron Energy (VRM)"
    implementation_status = "ready"

    # -------------------------------------------------------------------
    # Commission wizard schema
    # -------------------------------------------------------------------

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="victron",
                backend="vrm",
                label="Victron VRM Portal",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret", "api_key"],
                extra_fields=[],
                docs_url="https://vrm-api-docs.victronenergy.com/",
                notes=(
                    "Provide either an API token (paste into 'api_key') OR a "
                    "username + password. 'site_id_on_vendor' is the VRM "
                    "installation ID (idSite) — leave blank during verify and "
                    "we'll discover the list of installations under this user."
                ),
            ),
        ]

    # -------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------

    def _session(self, cred: SiteCredential) -> requests.Session:
        s = requests.Session()
        s.headers["Accept"] = "application/json"
        s.headers["User-Agent"] = "1PWR-CC-gensite/1.0"
        return s

    def _auth_headers(self, cred: SiteCredential, session: requests.Session) -> Dict[str, str]:
        """Obtain a bearer header. Caches the token on the session for reuse."""
        token = session.headers.get("X-Authorization", "").replace("Bearer ", "").strip()
        if token:
            return {"X-Authorization": f"Bearer {token}"}

        # Token auth
        if cred.api_key:
            session.headers["X-Authorization"] = f"Token {cred.api_key}"
            return {"X-Authorization": f"Token {cred.api_key}"}

        # Username/password → token
        if not cred.username or not cred.secret:
            raise AdapterError(
                "Victron VRM requires either an api_key (token) or username+password",
                retryable=False,
            )
        base = (cred.base_url or VRM_BASE).rstrip("/")
        resp = session.post(
            f"{base}/auth/login",
            json={"username": cred.username, "password": cred.secret},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 401:
            raise AdapterError("Victron VRM rejected credentials", retryable=False, status=401)
        if resp.status_code >= 400:
            raise AdapterError(
                f"Victron VRM auth returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        body = resp.json() or {}
        token = body.get("token")
        if not token:
            raise AdapterError(
                f"Victron VRM auth: no token in response: {body}", retryable=False
            )
        id_user = body.get("idUser") or body.get("user", {}).get("idUser")
        session.headers["X-Authorization"] = f"Bearer {token}"
        if id_user:
            session.headers["X-VRM-IdUser"] = str(id_user)
        return {"X-Authorization": f"Bearer {token}"}

    def _user_id(self, session: requests.Session) -> Optional[str]:
        return session.headers.get("X-VRM-IdUser")

    # -------------------------------------------------------------------
    # verify()
    # -------------------------------------------------------------------

    def verify(self, cred: SiteCredential) -> VerifyResult:
        try:
            session = self._session(cred)
            self._auth_headers(cred, session)
        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

        base = (cred.base_url or VRM_BASE).rstrip("/")
        id_user = self._user_id(session)

        # Discover installations for this user
        installations: List[Dict[str, Any]] = []
        if id_user:
            try:
                r = session.get(
                    f"{base}/users/{id_user}/installations",
                    timeout=HTTP_TIMEOUT,
                )
                if r.status_code == 200:
                    installations = (r.json() or {}).get("records", [])
            except requests.RequestException as exc:
                return VerifyResult(ok=False, message=f"VRM installations fetch failed: {exc}")

        summary = [
            {
                "id": str(inst.get("idSite") or ""),
                "name": inst.get("name") or "",
                "identifier": inst.get("identifier") or "",
                "last_timestamp": inst.get("last_timestamp") or None,
            }
            for inst in installations
        ]

        if cred.site_id_on_vendor:
            target = str(cred.site_id_on_vendor)
            match = next((i for i in summary if i["id"] == target), None)
            if match is None:
                return VerifyResult(
                    ok=False,
                    message=(
                        f"Authenticated, but idSite={target} not found under this user. "
                        f"Available installations: {[i['id'] for i in summary]}"
                    ),
                    discovered_equipment=summary,
                )
            return VerifyResult(
                ok=True,
                message=f"Connected to VRM installation '{match['name']}' ({target}).",
                discovered_site_id=target,
                discovered_equipment=summary,
            )

        return VerifyResult(
            ok=True,
            message=(
                f"Authenticated to VRM. {len(summary)} installation(s) visible. "
                "Pick one and re-save with `site_id_on_vendor` set."
            ),
            discovered_equipment=summary,
        )

    # -------------------------------------------------------------------
    # fetch_live()
    # -------------------------------------------------------------------

    def fetch_live(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
    ) -> List[LiveReading]:
        if not cred.site_id_on_vendor:
            raise AdapterError(
                "Victron fetch_live requires site_id_on_vendor (idSite) to be set",
                retryable=False,
            )
        if not equipment:
            return []

        session = self._session(cred)
        self._auth_headers(cred, session)
        base = (cred.base_url or VRM_BASE).rstrip("/")

        # Widgets/Status gives a concise current-state snapshot per installation.
        # For Phase 1 we assume one reading per installation and tag it onto the
        # first active inverter-kind equipment row. Per-device breakdown comes
        # from Widgets/InverterChargerOverview in a later pass.
        try:
            r = session.get(
                f"{base}/installations/{cred.site_id_on_vendor}/widgets/Status",
                timeout=HTTP_TIMEOUT,
            )
            if r.status_code >= 400:
                raise AdapterError(
                    f"VRM /widgets/Status returned {r.status_code}: {r.text[:200]}",
                    status=r.status_code,
                )
            payload = r.json() or {}
        except requests.RequestException as exc:
            raise AdapterError(f"VRM /widgets/Status request failed: {exc}") from exc

        records = payload.get("records") or {}
        ts = datetime.now(timezone.utc)

        # VRM widget payloads vary by installation — we pull the commonly
        # populated fields and leave raw_json for forensic inspection.
        def _num(key: str) -> Optional[float]:
            val = records.get(key)
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        target = next(
            (e for e in equipment if e.kind == "inverter"),
            equipment[0],
        )

        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts,
                ac_kw=_num("Ac/Out/L1/P") or _num("acOutput"),
                pv_kw=_num("Dc/Pv/Power") or _num("pvPower"),
                battery_kw=_num("Dc/Battery/Power") or _num("batteryPower"),
                battery_soc_pct=_num("Dc/Battery/Soc") or _num("soc"),
                grid_kw=_num("Ac/Grid/L1/P") or _num("gridPower"),
                ac_freq_hz=_num("Ac/Out/L1/F"),
                ac_v_avg=_num("Ac/Out/L1/V"),
                status_code=str(records.get("state", "")) or None,
                raw_json=records,
            )
        ]

    # -------------------------------------------------------------------
    # fetch_day / fetch_alarms — deferred (Phase 2)
    # -------------------------------------------------------------------

    def fetch_day(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        day: date,
    ) -> List[IntervalReading]:
        # /installations/{idSite}/stats?interval=hours&start=...&end=...
        # Implemented in Phase 2.
        return []

    def fetch_alarms(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        since: datetime,
    ) -> List[AlarmEvent]:
        # /installations/{idSite}/alarms — implemented in Phase 2.
        return []
