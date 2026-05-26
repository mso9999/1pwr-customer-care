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

        ac_kw = _num("Ac/Out/L1/P") or _num("acOutput")
        pv_kw = _num("Dc/Pv/Power") or _num("pvPower")
        battery_kw = _num("Dc/Battery/Power") or _num("batteryPower")
        battery_soc_pct = _num("Dc/Battery/Soc") or _num("soc")
        grid_kw = _num("Ac/Grid/L1/P") or _num("gridPower")
        ac_freq_hz = _num("Ac/Out/L1/F")
        ac_v_avg = _num("Ac/Out/L1/V")
        status_code = str(records.get("state", "")) or None
        source_payload: Dict[str, Any] = {"status": records}

        # Newer VRM Status widget payloads can be metadata-only. If we still
        # have no normalized channels, fall back to /diagnostics (code/value list).
        if all(v is None for v in (ac_kw, pv_kw, battery_kw, battery_soc_pct, grid_kw)):
            try:
                rd = session.get(
                    f"{base}/installations/{cred.site_id_on_vendor}/diagnostics",
                    timeout=HTTP_TIMEOUT,
                )
                if rd.status_code < 400:
                    diag_payload = rd.json() or {}
                    diag_records = diag_payload.get("records") or []
                    code_vals: Dict[str, List[float]] = {}
                    for item in diag_records:
                        if not isinstance(item, dict):
                            continue
                        code = str(item.get("code") or "")
                        if not code:
                            continue
                        raw = item.get("rawValue")
                        try:
                            val = float(raw)
                        except (TypeError, ValueError):
                            continue
                        code_vals.setdefault(code, []).append(val)

                    def _sum_codes(*codes: str) -> Optional[float]:
                        vals: List[float] = []
                        for c in codes:
                            vals.extend(code_vals.get(c, []))
                        if not vals:
                            return None
                        return float(sum(vals))

                    def _first_code(*codes: str) -> Optional[float]:
                        for c in codes:
                            vals = code_vals.get(c, [])
                            if vals:
                                return float(vals[0])
                        return None

                    def _avg_codes(*codes: str) -> Optional[float]:
                        vals: List[float] = []
                        for c in codes:
                            vals.extend(code_vals.get(c, []))
                        if not vals:
                            return None
                        return float(sum(vals) / len(vals))

                    # Diagnostics values are mostly W for power channels.
                    pv_w = _sum_codes("P", "P2", "P3", "Pdc")
                    if pv_w is None:
                        pv_w = _sum_codes("pP1", "pP2", "pP3", "PVP")
                    load_w = _sum_codes("a1", "a2", "a3")
                    if load_w is None:
                        load_w = _sum_codes("o1", "o2", "o3")
                    batt_w = _first_code("bp", "ScW")
                    grid_w = _sum_codes("IP1", "IP2", "IP3")

                    ac_kw = (load_w / 1000.0) if load_w is not None else ac_kw
                    pv_kw = (pv_w / 1000.0) if pv_w is not None else pv_kw
                    battery_kw = (batt_w / 1000.0) if batt_w is not None else battery_kw
                    battery_soc_pct = _first_code("bs", "SOC", "VSH") or battery_soc_pct
                    grid_kw = (grid_w / 1000.0) if grid_w is not None else grid_kw
                    ac_freq_hz = _first_code("OF", "IF1") or ac_freq_hz
                    ac_v_avg = _avg_codes("OV1", "OV2", "OV3") or ac_v_avg
                    status_code = str(int(_first_code("S") or 0)) if status_code is None else status_code
                    source_payload["diagnostics"] = diag_records
            except requests.RequestException:
                pass

        target = next(
            (e for e in equipment if e.kind == "inverter"),
            equipment[0],
        )

        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts,
                ac_kw=ac_kw,
                pv_kw=pv_kw,
                battery_kw=battery_kw,
                battery_soc_pct=battery_soc_pct,
                grid_kw=grid_kw,
                ac_freq_hz=ac_freq_hz,
                ac_v_avg=ac_v_avg,
                status_code=status_code,
                raw_json=source_payload,
            )
        ]

    # -------------------------------------------------------------------
    # fetch_day — hourly stats for 24 h / 30 d charts
    # -------------------------------------------------------------------

    def fetch_day(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        day: date,
    ) -> List[IntervalReading]:
        """Return hourly interval readings for the UTC day ``day``.

        VRM exposes `/installations/{idSite}/stats` with epoch-second start/end
        and configurable interval. We ask for hourly buckets and attach each
        to the first inverter-kind equipment row.
        """
        if not cred.site_id_on_vendor or not equipment:
            return []
        start_dt = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)
        return self._fetch_stats_range(cred, equipment, start_dt, end_dt, interval="hours")

    def _fetch_stats_range(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        start_dt: datetime,
        end_dt: datetime,
        interval: str = "hours",
    ) -> List[IntervalReading]:
        session = self._session(cred)
        self._auth_headers(cred, session)
        base = (cred.base_url or VRM_BASE).rstrip("/")

        params = {
            "interval": interval,
            "start": int(start_dt.timestamp()),
            "end": int(end_dt.timestamp()),
            "type": "live_feed",
        }
        try:
            r = session.get(
                f"{base}/installations/{cred.site_id_on_vendor}/stats",
                params=params,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AdapterError(f"VRM /stats request failed: {exc}") from exc
        if r.status_code >= 400:
            raise AdapterError(
                f"VRM /stats returned {r.status_code}: {r.text[:200]}",
                status=r.status_code,
            )
        payload = r.json() or {}
        records = payload.get("records") or {}
        if not isinstance(records, dict):
            return []

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        # VRM returns `{"metric": [[ts_ms, value], [ts_ms, value], ...]}`.
        # Collapse into one IntervalReading per bucket by merging metrics by timestamp.
        by_ts: Dict[int, Dict[str, float]] = {}
        for metric_key, series in records.items():
            if not isinstance(series, list):
                continue
            for point in series:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    ts_ms = int(point[0])
                    val = float(point[1]) if point[1] is not None else None
                except (TypeError, ValueError):
                    continue
                if val is None:
                    continue
                by_ts.setdefault(ts_ms, {})[metric_key] = val

        out: List[IntervalReading] = []
        for ts_ms in sorted(by_ts):
            m = by_ts[ts_ms]
            out.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                    pv_kw=m.get("Pg") or m.get("pvPower"),
                    battery_kw=m.get("Pb") or m.get("batteryPower"),
                    battery_soc_pct=m.get("bs") or m.get("soc"),
                    grid_kw=m.get("Pgr") or m.get("gridPower"),
                    ac_kw=m.get("Pc") or m.get("acLoad"),
                    ac_v_avg=m.get("vAc"),
                    ac_freq_hz=m.get("fAc"),
                    raw_json=m,
                )
            )
        return out

    # -------------------------------------------------------------------
    # fetch_alarms
    # -------------------------------------------------------------------

    def fetch_alarms(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        since: datetime,
    ) -> List[AlarmEvent]:
        """Fetch alarms raised after `since` for the installation.

        Uses `/installations/{idSite}/alarms`. VRM classifies by device; we
        attach each alarm to the inverter equipment row for now and record
        device identity in `event_json` for future per-device routing.
        """
        if not cred.site_id_on_vendor or not equipment:
            return []
        session = self._session(cred)
        self._auth_headers(cred, session)
        base = (cred.base_url or VRM_BASE).rstrip("/")

        try:
            r = session.get(
                f"{base}/installations/{cred.site_id_on_vendor}/alarms",
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AdapterError(f"VRM /alarms request failed: {exc}") from exc
        if r.status_code >= 400:
            # VRM returns 401 if token expired — let caller decide to retry.
            raise AdapterError(
                f"VRM /alarms returned {r.status_code}: {r.text[:200]}",
                status=r.status_code,
            )
        payload = r.json() or {}
        records = payload.get("records") or []
        if not isinstance(records, list):
            return []

        since_ts = int(since.timestamp()) if since else 0
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        out: List[AlarmEvent] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            # VRM uses `started` as epoch seconds and `ended` as epoch seconds or null.
            started = item.get("started") or item.get("startedAt") or item.get("startTime")
            ended = item.get("ended") or item.get("endedAt")
            try:
                started_epoch = int(started) if started is not None else 0
            except (TypeError, ValueError):
                continue
            if started_epoch < since_ts:
                continue
            try:
                ended_dt = (
                    datetime.fromtimestamp(int(ended), tz=timezone.utc)
                    if ended not in (None, "", 0)
                    else None
                )
            except (TypeError, ValueError):
                ended_dt = None

            severity_raw = str(item.get("severity") or item.get("level") or "warning").lower()
            severity = "critical" if severity_raw in ("critical", "alarm", "severe") else (
                "info" if severity_raw in ("info", "notice") else "warning"
            )
            out.append(
                AlarmEvent(
                    equipment_id=target.id,
                    site_code=cred.site_code.upper(),
                    vendor_code=str(item.get("code") or item.get("id") or ""),
                    vendor_msg=str(item.get("description") or item.get("message") or ""),
                    severity=severity,
                    raised_at=datetime.fromtimestamp(started_epoch, tz=timezone.utc),
                    cleared_at=ended_dt,
                    event_json=item,
                )
            )
        return out
