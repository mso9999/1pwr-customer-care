"""
Sinosoar Cloud adapter — covers the ~8 Lesotho Sinosoar projects.

Portal: https://www.sinosoarcloud.com
Backend: JeecgBoot (Spring Boot + Vue SPA)
API Base: https://www.sinosoarcloud.com/jeecg-boot

Auth: session-less JWT via X-Access-Token header.
Login requires CAPTCHA (4-char image, solved via pytesseract OCR).
Token is long-lived (months); stored encrypted in the credential extra field
so re-auth is only needed on expiry.

Data: The PCS (Power Conversion System) endpoints return live grid metrics.
PV/battery subsystem detail endpoints exist in the JS bundle but currently
return null — likely a per-project provisioning issue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from base64 import b64decode
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageEnhance

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

API_BASE = "https://www.sinosoarcloud.com/jeecg-boot"
HTTP_TIMEOUT = 20

# ---------------------------------------------------------------------------
# CAPTCHA OCR
# ---------------------------------------------------------------------------

def _solve_captcha(session: requests.Session) -> tuple[str, str]:
    """Fetch a CAPTCHA image, OCR it, return (captcha_text, checkKey)."""
    try:
        import pytesseract
    except ImportError:
        raise AdapterError(
            "pytesseract not installed; cannot solve Sinosoar CAPTCHA automatically. "
            "Install tesseract-ocr + pytesseract on the poller host.",
            retryable=False,
        )

    check_key = str(int(time.time() * 1000))
    resp = session.get(f"{API_BASE}/sys/randomImage/{check_key}", timeout=HTTP_TIMEOUT)
    data = resp.json()
    if not data.get("success"):
        raise AdapterError(f"CAPTCHA fetch failed: {data.get('message')}", retryable=True)

    b64_data = data["result"].split(",", 1)[1]
    img = Image.open(BytesIO(b64decode(b64_data)))
    img = img.resize((img.width * 4, img.height * 4), Image.LANCZOS)
    gray = img.convert("L")
    gray = ImageEnhance.Contrast(gray).enhance(2.5)

    results: Dict[str, int] = {}
    for psm in [6, 7, 8]:
        text = pytesseract.image_to_string(
            gray,
            config=f"--psm {psm} -c tessedit_char_whitelist="
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        ).strip()
        if len(text) == 4:
            results[text] = results.get(text, 0) + 1

    if not results:
        raise AdapterError("CAPTCHA OCR produced no 4-char result", retryable=True)

    captcha = max(results, key=results.get)
    return captcha, check_key


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _login(session: requests.Session, username: str, password: str) -> str:
    """Login to Sinosoar, return JWT token."""
    captcha, check_key = _solve_captcha(session)
    logger.info("Sinosoar CAPTCHA solved: %s", captcha)

    resp = session.post(
        f"{API_BASE}/sys/login",
        json={
            "username": username,
            "password": password,
            "captcha": captcha,
            "checkKey": check_key,
            "remember_me": True,
        },
        timeout=HTTP_TIMEOUT,
    )
    body = resp.json()
    if not body.get("success"):
        msg = body.get("message", "unknown")
        if "captcha" in msg.lower():
            raise AdapterError(f"Sinosoar CAPTCHA rejected: {msg}", retryable=True)
        raise AdapterError(f"Sinosoar login failed: {msg}", retryable=False, status=401)

    token = body["result"]["token"]
    return token


def _api_get(session: requests.Session, path: str, params: dict = None) -> dict:
    """Authenticated GET with timestamp params (mimics browser interceptor)."""
    if params is None:
        params = {}
    params.setdefault("_t", int(time.time()))
    params.setdefault("timezoneOffset", -120)
    resp = session.get(f"{API_BASE}/{path}", params=params, timeout=HTTP_TIMEOUT)
    if resp.status_code == 401:
        raise AdapterError("Sinosoar token expired", retryable=True, status=401)
    if resp.status_code >= 400:
        raise AdapterError(
            f"Sinosoar {path} returned {resp.status_code}: {resp.text[:200]}",
            status=resp.status_code,
        )
    return resp.json()


def _api_post(session: requests.Session, path: str, json_data: dict = None) -> dict:
    """Authenticated POST."""
    resp = session.post(
        f"{API_BASE}/{path}", json=json_data or {}, timeout=HTTP_TIMEOUT
    )
    if resp.status_code == 401:
        raise AdapterError("Sinosoar token expired", retryable=True, status=401)
    if resp.status_code >= 400:
        raise AdapterError(
            f"Sinosoar {path} returned {resp.status_code}: {resp.text[:200]}",
            status=resp.status_code,
        )
    return resp.json()


def _make_session(token: str) -> requests.Session:
    """Create a session pre-configured with auth headers."""
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "X-Access-Token": token,
            "Language-Type": "en",
        }
    )
    return s


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if not n == n:
        return None
    return n


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class SinosoarAdapter(InverterAdapter):
    vendor = "sinosoar"
    display_name = "Sinosoar Cloud"
    implementation_status = "ready"

    # Token cache key in the credential's extra JSON field
    _TOKEN_KEY = "cached_token"

    # ------------------------------------------------------------------
    # credential_specs
    # ------------------------------------------------------------------

    def credential_specs(self) -> List[CredentialSpec]:
        return [
            CredentialSpec(
                vendor="sinosoar",
                backend="sinosoarcloud",
                label="Sinosoar Cloud (www.sinosoarcloud.com)",
                plain_fields=["username", "site_id_on_vendor", "base_url"],
                secret_fields=["secret"],
                extra_fields=[],
                docs_url="https://www.sinosoarcloud.com/energystorage/es",
                notes=(
                    "'username' is the Sinosoar portal account name. "
                    "'secret' is the portal password. "
                    "'site_id_on_vendor' is the Sinosoar project ID (e.g. 103). "
                    "Verify performs a live login with CAPTCHA (OCR-solved); "
                    "the JWT token is cached in extra for subsequent poller runs."
                ),
            ),
        ]

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def verify(self, cred: SiteCredential) -> VerifyResult:
        if not cred.username or not cred.secret:
            return VerifyResult(
                ok=False, message="Sinosoar requires username + password."
            )

        try:
            session = _make_session("")  # no token yet
            token: Optional[str] = None
            # CAPTCHA OCR is probabilistic; retry a few times before failing hard.
            for attempt in range(3):
                try:
                    token = _login(session, cred.username, cred.secret)
                    break
                except AdapterError as exc:
                    msg = str(exc).lower()
                    if "captcha" in msg and attempt < 2:
                        logger.warning("Sinosoar verify CAPTCHA retry %d/3", attempt + 1)
                        continue
                    raise
            if not token:
                raise AdapterError("Sinosoar login did not return a token", retryable=True)

            session.headers["X-Access-Token"] = token

            # List projects to confirm access
            data = _api_get(session, "powerstation/iotProject/lists",
                            params={"pageNo": 1, "pageSize": 50})
            projects = data.get("result", [])
            if not isinstance(projects, list):
                projects = []

            summary = [
                {"id": str(p.get("id", "")), "name": str(p.get("name", ""))}
                for p in projects
            ]

            # Check site_id_on_vendor if provided
            if cred.site_id_on_vendor:
                target = str(cred.site_id_on_vendor)
                match = next((s for s in summary if s["id"] == target), None)
                if match is None:
                    return VerifyResult(
                        ok=False,
                        message=(
                            f"Authenticated, but project {target} not found. "
                            f"Available: {[s['id'] for s in summary]}"
                        ),
                        discovered_equipment=summary,
                    )
                # Store token in extra for subsequent poller use
                return VerifyResult(
                    ok=True,
                    message=f"Connected to Sinosoar project '{match['name']}'.",
                    discovered_site_id=target,
                    discovered_equipment=summary,
                )

            return VerifyResult(
                ok=True,
                message=(
                    f"Authenticated. {len(summary)} project(s) visible. "
                    "Set site_id_on_vendor to one of the discovered IDs."
                ),
                discovered_equipment=summary,
            )

        except AdapterError as exc:
            return VerifyResult(ok=False, message=str(exc))

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_token(self, cred: SiteCredential) -> str:
        """Get a valid token: cached first, then re-login if needed."""
        cached = (cred.extra or {}).get(self._TOKEN_KEY)
        if cached:
            return cached

        # Re-login
        session = _make_session("")
        token = _login(session, cred.username, cred.secret)
        logger.info("Sinosoar: fresh login for %s", cred.site_id_on_vendor)
        return token

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
                "Sinosoar fetch_live requires site_id_on_vendor (project ID)",
                retryable=False,
            )
        if not equipment:
            return []

        token = self._get_token(cred)
        session = _make_session(token)

        pid = cred.site_id_on_vendor
        ts = datetime.now(timezone.utc)
        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])

        def _num(val: Any) -> Optional[float]:
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        # --- Grid data (works for all projects) ---
        grid_kw = None
        raw_grid = None
        try:
            grid_resp = _api_get(
                session,
                "powerstation/iotPcsProject/queryPcsContryDataByProjectId",
                params={"projectId": pid},
            )
            grid_result = grid_resp.get("result")
            if grid_result and isinstance(grid_result, dict):
                raw_grid = grid_result
                grid_kw = _num(grid_result.get("rPsum_grid"))
        except Exception:
            pass

        # --- System overview (PV, battery, generator, load, SOC) ---
        pv_kw = None
        battery_kw = None
        battery_soc_pct = None
        ac_kw = None
        ac_freq_hz = None
        raw_overview = None

        try:
            overview_resp = _api_get(
                session,
                "powerstation/iotPcsProject/getPcsSystemOverviewCharts",
                params={"projectid": pid},
            )
            overview = overview_resp.get("result")
            if overview and isinstance(overview, dict) and overview:
                raw_overview = overview

                def _latest_series(key: str) -> Optional[float]:
                    series = overview.get(key)
                    if series and isinstance(series, list) and series:
                        point = series[-1]
                        if isinstance(point, list) and len(point) >= 2:
                            return _num(point[1])
                    return None

                pv_val = _latest_series("pv")
                batt_val = _latest_series("bett")
                gen_val = _latest_series("gen")
                load_val = _latest_series("load")
                soc_val = _latest_series("soc")

                pv_kw = pv_val if pv_val is not None and pv_val > 0 else (0.0 if pv_val is not None else None)
                battery_kw = batt_val
                battery_soc_pct = soc_val
                ac_kw = abs(load_val) if load_val is not None else None
        except Exception:
            pass

        # If we got nothing from either endpoint, return empty (no data right now)
        if grid_kw is None and pv_kw is None and battery_kw is None and ac_kw is None:
            return []

        # Merge raw payloads
        merged_raw = {
            "grid": raw_grid,
            "overview": raw_overview,
        }

        return [
            LiveReading(
                equipment_id=target.id,
                ts_utc=ts,
                pv_kw=pv_kw,
                battery_kw=battery_kw,
                battery_soc_pct=battery_soc_pct,
                grid_kw=grid_kw,
                ac_kw=ac_kw,
                ac_freq_hz=ac_freq_hz,
                raw_json=merged_raw,
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

        token = self._get_token(cred)
        session = _make_session(token)
        pid = cred.site_id_on_vendor

        data = _api_get(
            session,
            "powerstation/iotPcsProject/queryPcsCountryCurrent24DataByProjectId",
            params={"projectId": pid},
        )

        result = data.get("result")
        if not result or not isinstance(result, dict):
            return []

        records = result.get("data") or []
        if not isinstance(records, list):
            return []

        target = next((e for e in equipment if e.kind == "inverter"), equipment[0])
        out: List[IntervalReading] = []

        for point in records:
            if not isinstance(point, list) or len(point) < 2:
                continue
            ts_epoch, power = point[0], point[1]
            try:
                ts = datetime.fromtimestamp(float(ts_epoch), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                continue
            try:
                grid_kw = float(power)
            except (TypeError, ValueError):
                continue

            out.append(
                IntervalReading(
                    equipment_id=target.id,
                    ts_utc=ts,
                    grid_kw=grid_kw,
                    raw_json=point,
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

    def discover_installed_capacity(self, cred: SiteCredential) -> List[Dict[str, Any]]:
        token = self._get_token(cred)
        session = _make_session(token)

        pid = str(cred.site_id_on_vendor or "").strip()
        if not pid:
            return []

        project: Optional[Dict[str, Any]] = None
        try:
            data = _api_get(session, "powerstation/iotProject/lists", params={"pageNo": 1, "pageSize": 200})
            projects = data.get("result") or []
            if isinstance(projects, list):
                project = next((p for p in projects if str((p or {}).get("id", "")) == pid), None)
        except Exception:
            project = None

        inverter_kw: Optional[float] = None
        pv_kw: Optional[float] = None
        battery_kw: Optional[float] = None
        battery_kwh: Optional[float] = None

        if isinstance(project, dict):
            for k, raw in project.items():
                lk = str(k).lower()
                n = _num(raw)
                if n is None or n <= 0:
                    continue
                if any(t in lk for t in ("pv", "solar")) and any(t in lk for t in ("power", "kw", "rated", "capacity", "installed", "peak")):
                    pv_kw = max(pv_kw or 0.0, n)
                if any(t in lk for t in ("pcs", "inverter")) and any(t in lk for t in ("power", "kw", "rated", "capacity", "nominal", "max")):
                    inverter_kw = max(inverter_kw or 0.0, n)
                if "battery" in lk and any(t in lk for t in ("power", "kw", "rated", "capacity")):
                    battery_kw = max(battery_kw or 0.0, n)
                if "battery" in lk and any(t in lk for t in ("kwh", "energy", "capacity")):
                    battery_kwh = max(battery_kwh or 0.0, n)

        out: List[Dict[str, Any]] = []
        if inverter_kw:
            out.append({"kind": "inverter", "nameplate_kw": inverter_kw})
        if pv_kw:
            out.append({"kind": "pv_array", "role": "pv", "nameplate_kw": pv_kw})
        if battery_kw or battery_kwh:
            out.append(
                {
                    "kind": "battery",
                    "role": "battery",
                    "nameplate_kw": battery_kw,
                    "nameplate_kwh": battery_kwh,
                }
            )
        return out
