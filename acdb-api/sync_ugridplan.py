"""
uGridPLAN <-> PostgreSQL bidirectional customer data sync.

- Fetches connection elements from uGridPLAN (customer_type, meter_serial, GPS)
- Matches to customers by Customer_Code or GPS proximity
- Stores customer_type/meter_serial/GPS in SQLite cc_customer_metadata
- Pushes customer demographics (name, phone, address) to uGridPLAN connections

Endpoints:
  GET  /api/sync/sites          - list configured sites with project IDs
  POST /api/sync/sites          - add/update a site-to-projectId mapping
  GET  /api/sync/preview?site=X - dry-run match preview
  POST /api/sync/execute        - apply sync changes
  GET  /api/sync/status         - last sync info per site
"""

import json
import logging
import math
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from models import CCRole, CurrentUser
from middleware import require_employee
from db_auth import get_auth_db

logger = logging.getLogger("acdb-api.sync")

router = APIRouter(prefix="/api/sync", tags=["sync"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UGP_BASE_URL = os.environ.get("UGP_BASE_URL", "https://dev.ugp.1pwrafrica.com/api")
UGP_SERVICE_USER = os.environ.get("UGP_SERVICE_USER", "whatsapp-cc")

# Nominal LV voltage for Load_A calculation (Watts = kWh_day/24*1000, Amps = W/V)
SYNC_LV_VOLTAGE = float(os.environ.get("SYNC_LV_VOLTAGE", "230"))


# ---------------------------------------------------------------------------
# uGridPLAN API Client
# ---------------------------------------------------------------------------

def _generate_ugp_password() -> str:
    """Date-based password for uGridPLAN: YYYYMM / reverse(YYYYMM), first 4 digits."""
    now = datetime.utcnow()
    yyyymm = now.strftime("%Y%m")
    reversed_str = yyyymm[::-1]
    numerator = int(yyyymm)
    denominator = int(reversed_str)
    if denominator == 0:
        return "0000"
    result = numerator / denominator
    result_str = f"{result:.10f}".replace(".", "").lstrip("0")
    return result_str[:4] if len(result_str) >= 4 else result_str.ljust(4, "0")


class UGPClient:
    """Lightweight HTTP client for the uGridPLAN API."""

    def __init__(self):
        self.base = UGP_BASE_URL.rstrip("/")
        self.token: Optional[str] = None
        self.session = requests.Session()
        self.session.verify = True
        self.session.timeout = 30

    def authenticate(self):
        """Login and capture the access_token cookie."""
        password = _generate_ugp_password()
        resp = self.session.post(
            f"{self.base}/auth/login",
            json={"employeeNumber": UGP_SERVICE_USER, "password": password},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"uGridPLAN auth failed ({resp.status_code}): {resp.text[:200]}")
        self.token = self.session.cookies.get("access_token")
        if not self.token:
            # Try extracting from response body
            try:
                body = resp.json()
                self.token = body.get("access_token", body.get("token", ""))
            except Exception:
                pass
        logger.info("Authenticated with uGridPLAN as %s", UGP_SERVICE_USER)

    def _ensure_auth(self):
        if not self.token:
            self.authenticate()

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all projects from uGridPLAN."""
        self._ensure_auth()
        resp = self.session.get(f"{self.base}/projects")
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.get(f"{self.base}/projects")
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list projects ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        return data.get("projects", [])

    def _resolve_project_key(self, name_or_code: str) -> str:
        """
        Resolve a project name or code to the registry key (code) used by
        the /projects/{key}/load endpoint.

        The uGridPLAN project registry is keyed by project *code* (e.g. "MAK"),
        but the /projects list returns both ``name`` ("Ha Makebe") and ``code``
        ("MAK").  If ``name_or_code`` is already a valid registry key it is
        returned as-is; otherwise we look it up via the project list.
        """
        projects = self.list_projects()
        # Check if it's already a code (registry key)
        for p in projects:
            if p.get("code", "") == name_or_code:
                return name_or_code
        # Fall back: match by display name and return the code
        for p in projects:
            if p.get("name", "") == name_or_code:
                code = p.get("code", "")
                if code:
                    logger.info(
                        "Resolved project name '%s' -> code '%s'",
                        name_or_code, code,
                    )
                    return code
        # Nothing matched – return as-is and let the caller handle the 404
        return name_or_code

    # uGridPLAN registry uses composite keys: CODE_type (e.g. "MAK_minigrid").
    # Bare codes ("MAK") and display names ("Ha Makebe") are resolved automatically.
    REGISTRY_SUFFIXES = ("_minigrid", "_ci", "_ipp")

    def load_project(self, project_name: str) -> str:
        """Load a project by registry key (code) and return the session projectId (UUID).

        The uGridPLAN project registry uses composite keys of the form
        ``CODE_type`` (e.g. ``MAK_minigrid``).  This method tries, in order:

        1. The literal ``project_name`` as given.
        2. Composite keys ``{project_name}_{type}`` for each known type.
        3. Resolution via the project list (display-name → code lookup).
        4. Composite keys of the resolved code.
        """
        self._ensure_auth()

        def _try_load(key: str) -> Optional[requests.Response]:
            r = self.session.post(
                f"{self.base}/projects/{key}/load",
                json={},
            )
            if r.status_code == 401:
                self.authenticate()
                r = self.session.post(
                    f"{self.base}/projects/{key}/load",
                    json={},
                )
            return r if r.status_code == 200 else None

        # 1. Try as-is
        resp = _try_load(project_name)

        # 2. Try composite keys (CODE_minigrid, CODE_ci, CODE_ipp)
        if resp is None:
            for suffix in self.REGISTRY_SUFFIXES:
                if not project_name.endswith(suffix):
                    resp = _try_load(f"{project_name}{suffix}")
                    if resp is not None:
                        break

        # 3. Resolve via project list (display name → code)
        if resp is None:
            resolved = self._resolve_project_key(project_name)
            if resolved != project_name:
                resp = _try_load(resolved)
                # 4. Try composite keys of the resolved code
                if resp is None:
                    for suffix in self.REGISTRY_SUFFIXES:
                        if not resolved.endswith(suffix):
                            resp = _try_load(f"{resolved}{suffix}")
                            if resp is not None:
                                break

        if resp is None:
            raise RuntimeError(
                f"Failed to load project '{project_name}': not found under any registry key variant"
            )
        data = resp.json()
        pid = data.get("projectId") or data.get("project_id") or data.get("id", "")
        if not pid:
            raise RuntimeError(f"No projectId returned when loading '{project_name}': {data}")
        logger.info("Loaded uGridPLAN project '%s' -> session ID %s", project_name, pid)
        return pid

    def get_connections(self, project_id: str, page_size: int = 1000) -> List[Dict[str, Any]]:
        """Fetch all connection elements for a project."""
        self._ensure_auth()
        all_rows = []
        page = 1
        while True:
            resp = self.session.get(
                f"{self.base}/project/table-data",
                params={
                    "projectId": project_id,
                    "elementType": "connection",
                    "page": page,
                    "pageSize": page_size,
                },
            )
            if resp.status_code == 401:
                self.authenticate()
                resp = self.session.get(
                    f"{self.base}/project/table-data",
                    params={
                        "projectId": project_id,
                        "elementType": "connection",
                        "page": page,
                        "pageSize": page_size,
                    },
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch connections ({resp.status_code}): {resp.text[:200]}")

            data = resp.json()
            rows = data.get("rows", [])
            all_rows.extend(rows)

            total = data.get("total", len(rows))
            if len(all_rows) >= total or not rows:
                break
            page += 1

        return all_rows

    def get_lines(self, project_id: str, page_size: int = 1000) -> List[Dict[str, Any]]:
        """Fetch all line elements (MV, LV, Drop) for a project."""
        self._ensure_auth()
        all_rows: List[Dict[str, Any]] = []
        page = 1
        while True:
            resp = self.session.get(
                f"{self.base}/project/table-data",
                params={
                    "projectId": project_id,
                    "elementType": "line",
                    "page": page,
                    "pageSize": page_size,
                },
            )
            if resp.status_code == 401:
                self.authenticate()
                resp = self.session.get(
                    f"{self.base}/project/table-data",
                    params={
                        "projectId": project_id,
                        "elementType": "line",
                        "page": page,
                        "pageSize": page_size,
                    },
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch lines ({resp.status_code}): {resp.text[:200]}")

            data = resp.json()
            rows = data.get("rows", [])
            all_rows.extend(rows)

            total = data.get("total", len(rows))
            if len(all_rows) >= total or not rows:
                break
            page += 1

        return all_rows

    def update_line(self, project_id: str, node1: str, node2: str, updates: Dict[str, Any]) -> bool:
        """Update a line element's properties.

        The uGridPLAN API identifies lines by their ``node1`` (from-pole) and
        ``node2`` (to-pole/connection) endpoints, passed as separate fields.
        """
        self._ensure_auth()
        payload = {
            "projectId": project_id,
            "elementType": "line",
            "node1": node1,
            "node2": node2,
            "updates": updates,
        }
        resp = self.session.post(
            f"{self.base}/project/update",
            json=payload,
        )
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.post(
                f"{self.base}/project/update",
                json=payload,
            )
        if resp.status_code != 200:
            logger.warning("Line update failed for %s|%s: %s", node1, node2, resp.text[:200])
            return False
        body = resp.json()
        if body.get("status") == "error":
            logger.warning("Line update error for %s|%s: %s", node1, node2, body.get("message", ""))
            return False
        return True

    # Voltage sanity bounds — only LV pole voltages are meaningful for
    # the Amps calculation.  MV poles (11kV), negative artifacts, and
    # zero/near-zero values are excluded.
    _VDROP_LV_MIN = 100.0   # V — below this is not a valid LV reading
    _VDROP_LV_MAX = 400.0   # V — above this is MV or transformer

    def get_vdrop_voltages(self, project_id: str) -> Dict[str, float]:
        """Fetch voltage-drop data and return a mapping of Survey_ID -> pole voltage.

        Calls the ``/visualization`` endpoint with ``viewMode=vdrop``.  Drop
        lines in the payload connect a pole (``node1``) to a customer
        (``node2`` = Survey_ID).  We look up the pole voltage from the
        ``poleVoltages`` dict and return ``{survey_id: voltage}``.

        Only voltages in the LV range (100–400 V) are included.  MV poles,
        negative artifacts, and zero/near-zero readings are silently skipped
        so the caller falls back to the nominal default for those connections.

        Returns an empty dict if the call fails for any reason.
        """
        self._ensure_auth()
        try:
            resp = self.session.get(
                f"{self.base}/visualization",
                params={
                    "projectId": project_id,
                    "viewMode": "vdrop",
                    "width": 800,
                    "height": 600,
                },
                timeout=60,
            )
            if resp.status_code == 401:
                self.authenticate()
                resp = self.session.get(
                    f"{self.base}/visualization",
                    params={
                        "projectId": project_id,
                        "viewMode": "vdrop",
                        "width": 800,
                        "height": 600,
                    },
                    timeout=60,
                )
            if resp.status_code != 200:
                logger.warning(
                    "Vdrop fetch failed (%s): %s",
                    resp.status_code, resp.text[:200],
                )
                return {}

            data = resp.json()
            pole_voltages: Dict[str, float] = data.get("poleVoltages", {})
            if not pole_voltages:
                logger.info("Vdrop returned no pole voltages")
                return {}

            # Build survey_id -> voltage from Drop lines, filtering to LV range
            conn_voltage: Dict[str, float] = {}
            skipped = 0
            lines = data.get("lines", [])
            for line in lines:
                props = line.get("props", {})
                line_type = (props.get("Type") or props.get("type", "")).strip()
                if line_type != "Drop":
                    continue
                pole_id = props.get("node1", "")   # pole end
                survey_id = props.get("node2", "")  # customer end
                if not pole_id or not survey_id:
                    continue
                # Try exact match, then normalized (underscores <-> spaces)
                voltage = pole_voltages.get(pole_id)
                if voltage is None:
                    voltage = pole_voltages.get(pole_id.replace(" ", "_"))
                if voltage is None:
                    voltage = pole_voltages.get(pole_id.replace("_", " "))
                if voltage is not None:
                    v = float(voltage)
                    if self._VDROP_LV_MIN <= v <= self._VDROP_LV_MAX:
                        conn_voltage[survey_id] = v
                    else:
                        skipped += 1
                        logger.debug(
                            "Vdrop: skipping %s (pole %s) — %.1fV outside LV range",
                            survey_id, pole_id, v,
                        )

            logger.info(
                "Vdrop: %d pole voltages, %d connections mapped, %d skipped (outside LV range)",
                len(pole_voltages), len(conn_voltage), skipped,
            )
            return conn_voltage

        except Exception as e:
            logger.warning("Vdrop fetch error: %s", e)
            return {}

    def update_connection(self, project_id: str, survey_id: str, updates: Dict[str, Any]) -> bool:
        """Update a connection element's properties."""
        self._ensure_auth()
        resp = self.session.post(
            f"{self.base}/project/update",
            json={
                "projectId": project_id,
                "elementType": "connection",
                "id": survey_id,
                "updates": updates,
            },
        )
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.post(
                f"{self.base}/project/update",
                json={
                    "projectId": project_id,
                    "elementType": "connection",
                    "id": survey_id,
                    "updates": updates,
                },
            )
        if resp.status_code != 200:
            logger.warning("Update failed for %s: %s", survey_id, resp.text[:200])
            return False
        return True

    def batch_update_lines(
        self, project_id: str, updates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Batch-update line properties in a single HTTP call (no viz recompute).

        Args:
            project_id: The loaded project session ID.
            updates: List of dicts, each with ``node1``, ``node2``, plus
                     property keys to update (e.g. ``{"node1": "P1", "node2": "P2", "St_code_4": 5}``).

        Returns:
            Server response dict (``updated``, ``not_found``, ``total_requested``).
        """
        self._ensure_auth()
        payload = {"projectId": project_id, "updates": updates}
        resp = self.session.post(
            f"{self.base}/project/batch-line-update",
            json=payload,
            timeout=180,
        )
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.post(
                f"{self.base}/project/batch-line-update",
                json=payload,
                timeout=180,
            )
        if resp.status_code != 200:
            logger.warning("Batch line update failed: %s", resp.text[:300])
            return {"updated": 0, "error": resp.text[:300]}
        body = resp.json()
        if body.get("status") == "error":
            logger.warning("Batch line update error: %s", body.get("message", ""))
            return {"updated": 0, "error": body.get("message", "")}
        return body

    def batch_update_connections(
        self, project_id: str, updates: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Batch-update connection properties in a single HTTP call (no viz recompute).

        Args:
            project_id: The loaded project session ID.
            updates: ``{survey_id: {prop: value, …}, …}``

        Returns:
            Server response dict (``updated``, ``not_found``, ``total_requested``).
        """
        self._ensure_auth()
        payload = {"projectId": project_id, "updates": updates}
        resp = self.session.post(
            f"{self.base}/project/batch-connection-update",
            json=payload,
            timeout=120,
        )
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.post(
                f"{self.base}/project/batch-connection-update",
                json=payload,
                timeout=120,
            )
        if resp.status_code != 200:
            logger.warning("Batch update failed: %s", resp.text[:300])
            return {"updated": 0, "error": resp.text[:300]}
        return resp.json()


# Singleton client
_ugp_client: Optional[UGPClient] = None


def _get_ugp_client() -> UGPClient:
    global _ugp_client
    if _ugp_client is None:
        _ugp_client = UGPClient()
    return _ugp_client


# ---------------------------------------------------------------------------
# Commission → uGridPLAN sync helpers
# ---------------------------------------------------------------------------

# Status code field names vary across projects (canonical vs legacy).
# We check multiple aliases to find the actual field in the data.
_LINE_STATUS_ALIASES = ("St_code_4", "Status C04", "Status_Code_4", "status_code_4")
_CONN_STATUS_ALIASES = ("St_code_3", "Status C03", "Status_Code_3", "status_code_3")

# Energized threshold: line St_code_4 value 5 = "Line energized"
_LINE_ENERGIZED_VALUE = 5
# Commissioned threshold: connection St_code_3 value 9 = "Connection commissioned"
_CONN_COMMISSIONED_VALUE = 9


def _parse_status_int(raw_value: Any) -> int:
    """Extract integer status from a value that may be int, float, or label string.

    Handles formats like: 5, 5.0, "5 - Line energized", "0 - uGridNET output".
    """
    if raw_value is None:
        return 0
    if isinstance(raw_value, (int, float)):
        return int(raw_value)
    s = str(raw_value).strip()
    if not s:
        return 0
    parts = s.split("-", 1)
    try:
        return int(parts[0].strip())
    except (ValueError, TypeError):
        return 0


def _get_line_status(line: Dict[str, Any]) -> Tuple[str, int]:
    """Return (field_name, integer_value) for the line status field."""
    for alias in _LINE_STATUS_ALIASES:
        if alias in line:
            return alias, _parse_status_int(line[alias])
    return "", 0


def _get_conn_status(conn: Dict[str, Any]) -> Tuple[str, int]:
    """Return (field_name, integer_value) for the connection status field."""
    for alias in _CONN_STATUS_ALIASES:
        if alias in conn:
            return alias, _parse_status_int(conn[alias])
    return "", 0


def trace_upstream_conductors(
    survey_id: str,
    lines: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Trace upstream conductors from a connection and return non-energized ones.

    Chain: Connection (Survey_ID) ← Drop line (Node 2=Survey_ID, Node 1=Pole)
           ← LV/MV lines touching that Pole.

    Returns list of dicts with line identification and current status for any
    upstream conductor that is NOT yet energized (St_code_4 < 5).
    """
    # Step 1: Find drop line(s) whose Node 2 = this connection's Survey_ID
    drop_poles: set = set()
    for line in lines:
        if line.get("Node 2", "") == survey_id:
            pole = line.get("Node 1", "")
            if pole:
                drop_poles.add(pole)

    if not drop_poles:
        return []

    # Step 2: Find all LV/MV lines touching any of those poles
    non_energized: List[Dict[str, Any]] = []
    for line in lines:
        node1 = line.get("Node 1", "")
        node2 = line.get("Node 2", "")
        line_type = str(line.get("Type", "")).upper()

        # Skip drop lines themselves (they connect pole→connection)
        if node2 == survey_id:
            continue

        # Check if this line touches one of our drop poles
        if node1 not in drop_poles and node2 not in drop_poles:
            continue

        status_field, status_val = _get_line_status(line)
        if status_val < _LINE_ENERGIZED_VALUE:
            non_energized.append({
                "node_1": node1,
                "node_2": node2,
                "type": line_type,
                "status_field": status_field,
                "status_value": status_val,
                "status_raw": line.get(status_field, ""),
                "cable_size": line.get("Cable_size", ""),
                "length": line.get("Length", 0),
                "subnet": line.get("SubNetwork", ""),
            })

    return non_energized


def sync_commission_to_ugp(
    site_code: str,
    survey_id: str,
    connection_date: str,
    account_number: str = "",
    meter_serial: str = "",
) -> Dict[str, Any]:
    """Push commissioning status to uGridPLAN for a connection element.

    Updates the connection's Commissioning_Date and optionally Customer_Code
    and Meter_Serial.  Also traces upstream conductors and returns any that
    are not yet energized so the caller can prompt the user.

    Returns:
        {
            "ugp_updated": bool,
            "upstream_warnings": [...],  # non-energized upstream lines
            "project_id": str,           # session ID for follow-up updates
            "error": str | None,
        }
    """
    result: Dict[str, Any] = {
        "ugp_updated": False,
        "upstream_warnings": [],
        "project_id": "",
        "error": None,
    }

    try:
        client = _get_ugp_client()
        session_id = client.load_project(site_code)
        result["project_id"] = session_id
    except Exception as e:
        result["error"] = f"Could not load uGridPLAN project for {site_code}: {e}"
        logger.warning(result["error"])
        return result

    # Build connection update payload
    conn_updates: Dict[str, Any] = {
        "Commissioning_Date": connection_date,
    }
    if account_number:
        conn_updates["Customer_Code"] = account_number
    if meter_serial:
        conn_updates["Meter_Serial"] = meter_serial

    try:
        ok = client.update_connection(session_id, survey_id, conn_updates)
        result["ugp_updated"] = ok
        if not ok:
            result["error"] = f"uGridPLAN update_connection returned failure for {survey_id}"
    except Exception as e:
        result["error"] = f"uGridPLAN update failed for {survey_id}: {e}"
        logger.warning(result["error"])

    # Trace upstream conductors
    try:
        all_lines = client.get_lines(session_id)
        warnings = trace_upstream_conductors(survey_id, all_lines)
        result["upstream_warnings"] = warnings
    except Exception as e:
        logger.warning("Could not trace upstream conductors for %s: %s", survey_id, e)

    return result


def energize_upstream_lines(
    site_code: str,
    line_ids: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Set upstream lines to energized status (St_code_4 = 5).

    Uses the batch-line-update endpoint for efficiency (single HTTP call,
    no per-line visualization recompute).  Falls back to individual
    ``update_line`` calls if the batch endpoint is unavailable.

    Args:
        site_code: Site code (e.g. "MAK").
        line_ids: List of {"node_1": ..., "node_2": ..., "status_field": ...}
                  identifying lines to update.

    Returns:
        {"updated": int, "failed": int, "errors": [...]}
    """
    result: Dict[str, Any] = {"updated": 0, "failed": 0, "errors": []}

    try:
        client = _get_ugp_client()
        session_id = client.load_project(site_code)
    except Exception as e:
        result["errors"].append(f"Could not load project: {e}")
        return result

    # Build batch payload
    batch_updates: List[Dict[str, Any]] = []
    for lid in line_ids:
        status_field = lid.get("status_field", "") or "St_code_4"
        node1 = lid.get("node_1", "")
        node2 = lid.get("node_2", "")
        if node1 and node2:
            batch_updates.append({
                "node1": node1,
                "node2": node2,
                status_field: _LINE_ENERGIZED_VALUE,
            })

    if not batch_updates:
        return result

    # Try batch first
    try:
        batch_result = client.batch_update_lines(session_id, batch_updates)
        batch_ok = batch_result.get("updated", 0)
        if batch_ok > 0:
            result["updated"] = batch_ok
            result["failed"] = len(batch_updates) - batch_ok
            not_found = batch_result.get("not_found", [])
            if not_found:
                result["errors"].extend(
                    [f"Not found: {nf}" for nf in not_found[:10]]
                )
            logger.info(
                "Batch line update for %s: %d updated, %d not found",
                site_code, batch_ok, len(not_found),
            )
            return result
        if "error" in batch_result:
            logger.warning(
                "Batch line update failed for %s, falling back to individual: %s",
                site_code, batch_result["error"][:200],
            )
    except Exception as e:
        logger.warning(
            "Batch line update unavailable for %s, falling back to individual: %s",
            site_code, e,
        )

    # Fallback: individual updates
    for lid in line_ids:
        status_field = lid.get("status_field", "") or "St_code_4"
        node1 = lid.get("node_1", "")
        node2 = lid.get("node_2", "")
        line_key = f"{node1}|{node2}"
        try:
            ok = client.update_line(
                session_id,
                node1,
                node2,
                {status_field: _LINE_ENERGIZED_VALUE},
            )
            if ok:
                result["updated"] += 1
            else:
                result["failed"] += 1
                result["errors"].append(f"Update failed for line {line_key}")
        except Exception as e:
            result["failed"] += 1
            result["errors"].append(f"Error updating {line_key}: {e}")

    return result


# ---------------------------------------------------------------------------
# Haversine distance (meters)
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two WGS84 points in meters."""
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Survey ID <-> Account Number linkage
# ---------------------------------------------------------------------------

def _survey_id_to_account_number(survey_id: str) -> Optional[str]:
    """
    Parse a uGridPLAN Survey_ID into the account number format.

    Survey_ID format:  "MAK 0047 HH"  (site_code  number  type)
    Account number:    "0047MAK"       (number + site_code)

    Returns None if the survey_id doesn't match the expected pattern.
    """
    parts = survey_id.strip().split()
    if len(parts) < 2:
        return None
    site_code = parts[0].upper()
    number = parts[1]
    # Validate that the number part is numeric
    if not number.isdigit():
        return None
    return f"{number}{site_code}"


# ---------------------------------------------------------------------------
# Consumption-based Load_A calculation
# ---------------------------------------------------------------------------

def _compute_avg_kwh_per_day(cursor, account_number: str) -> Optional[float]:
    """
    Compute average daily kWh consumption for an account from transaction history.

    Queries the transactions table, sums all ``kwh_value`` entries, and divides
    by the day-span between the earliest and latest ``transaction_date``.

    Returns None if there is insufficient data (< 2 transactions or < 1 day span).
    """
    try:
        cursor.execute(
            "SELECT kwh_value, transaction_date "
            "FROM transactions "
            "WHERE account_number = %s "
            "  AND kwh_value IS NOT NULL "
            "  AND transaction_date IS NOT NULL",
            (account_number,),
        )
        rows = cursor.fetchall()
        if len(rows) < 2:
            return None

        total_kwh = 0.0
        min_date = None
        max_date = None
        for kwh_val, tx_date in rows:
            try:
                kwh = float(kwh_val)
            except (ValueError, TypeError):
                continue
            if kwh <= 0:
                continue
            total_kwh += kwh

            # Parse date — psycopg2 returns datetime objects for PostgreSQL timestamps
            if tx_date is not None:
                if isinstance(tx_date, str):
                    try:
                        tx_date = datetime.fromisoformat(tx_date.replace(" ", "T"))
                    except ValueError:
                        continue
                if min_date is None or tx_date < min_date:
                    min_date = tx_date
                if max_date is None or tx_date > max_date:
                    max_date = tx_date

        if min_date is None or max_date is None:
            return None
        day_span = (max_date - min_date).total_seconds() / 86400.0
        if day_span < 1.0:
            return None

        avg = total_kwh / day_span
        return avg

    except Exception as e:
        logger.debug("Could not query transactions for account %s: %s", account_number, e)
        return None


def _kwh_per_day_to_amps(kwh_per_day: float, voltage: float = SYNC_LV_VOLTAGE) -> float:
    """
    Convert average daily consumption to average current draw in Amps.

    Formula:  Amps = (kWh/day * 1000) / (24 * voltage)
    """
    if voltage <= 0:
        return 0.0
    return (kwh_per_day * 1000.0) / (24.0 * voltage)


# ---------------------------------------------------------------------------
# Meter data loader
# ---------------------------------------------------------------------------

def _load_meter_data(cursor, site_code: str) -> List[Dict[str, Any]]:
    """
    Load meter records from PostgreSQL for a given site (community).
    Returns list of dicts with: meterid, customer_id, accountnumber,
    customer_type, latitude, longitude, community.
    """
    meters = []
    try:
        cursor.execute(
            "SELECT meter_id, customer_id_legacy, account_number, customer_type, "
            "latitude, longitude, community "
            "FROM meters WHERE community = %s",
            (site_code,),
        )
        for row in cursor.fetchall():
            meters.append({
                "meterid": str(row[0] or "").strip(),
                "customer_id": str(row[1] or "").strip() if row[1] else "",
                "accountnumber": str(row[2] or "").strip(),
                "customer_type": str(row[3] or "").strip(),
                "latitude": row[4],
                "longitude": row[5],
                "community": str(row[6] or "").strip(),
            })
    except Exception as e:
        logger.warning("Could not read meter data from meters table: %s", e)
    return meters


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

GPS_MATCH_RADIUS_M = float(os.environ.get("SYNC_GPS_RADIUS_M", "50"))


def _plot_prefix(plot_id: str) -> str:
    """Extract the site+number prefix from a plot/survey ID, stripping the type suffix.

    Examples:
        "MAK 0029 HH"  -> "MAK 0029"
        "MAK 0134A SCP" -> "MAK 0134A"
        "MAK 0071 HH"   -> "MAK 0071"
    """
    parts = plot_id.strip().rsplit(None, 1)
    return parts[0].upper() if len(parts) >= 2 else plot_id.strip().upper()


def _match_customers(
    ugp_connections: List[Dict[str, Any]],
    accdb_customers: List[Dict[str, Any]],
    accdb_meters: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Match uGridPLAN connections to customers.

    Matching strategies (tried in order):
      1. Customer_Code (uGP) == customer_id (DB)
      2. Survey_ID (uGP) == plot_number (DB)  — exact match
      3. Plot prefix (uGP) == plot prefix (DB) — ignoring type suffix
         e.g. "MAK 0029 HH" matches "MAK 0029 SME" (DB type wins)
      4. GPS proximity via meter table (within GPS_MATCH_RADIUS_M)

    Returns match results with proposed sync actions.
    """
    # Index customers by customer_id
    accdb_by_id: Dict[str, Dict] = {}
    for c in accdb_customers:
        cid = str(c.get("customer_id", "")).strip()
        if cid:
            accdb_by_id[cid] = c

    # Index customers by plot_number (normalized: uppercased, stripped)
    accdb_by_plot: Dict[str, Dict] = {}
    for c in accdb_customers:
        plot = str(c.get("plot_number", "")).strip().upper()
        if plot and plot != "NONE":
            accdb_by_plot[plot] = c

    # Index customers by plot *prefix* (site + number, ignoring type suffix)
    accdb_by_prefix: Dict[str, Optional[Dict]] = {}
    for c in accdb_customers:
        plot = str(c.get("plot_number", "")).strip().upper()
        if plot and plot != "NONE":
            pfx = _plot_prefix(plot)
            if pfx in accdb_by_prefix:
                accdb_by_prefix[pfx] = None  # ambiguous — skip
            else:
                accdb_by_prefix[pfx] = c

    # Index meters by customer_id and by accountnumber
    meters_by_cust: Dict[str, Dict] = {}
    meters_by_acct: Dict[str, Dict] = {}
    meters_by_meterid: Dict[str, Dict] = {}
    if accdb_meters:
        for m in accdb_meters:
            cid = m.get("customer_id", "")
            if cid:
                meters_by_cust[cid] = m
            acct = m.get("accountnumber", "")
            if acct:
                meters_by_acct[acct] = m
            mid = m.get("meterid", "")
            if mid:
                meters_by_meterid[mid] = m

    matched = []
    unmatched_ugp = []
    unmatched_accdb = set(accdb_by_id.keys())

    for conn_el in ugp_connections:
        survey_id = conn_el.get("Survey_ID") or conn_el.get("survey_id") or conn_el.get("Name", "")
        customer_code = str(conn_el.get("Customer_Code") or conn_el.get("customer_code") or "").strip()
        ugp_customer_type = str(conn_el.get("Customer_Type") or conn_el.get("customer_type") or "").strip()
        ugp_meter_serial = str(conn_el.get("Meter_Serial") or conn_el.get("meter_serial") or "").strip()
        gps_x = conn_el.get("GPS_X") or conn_el.get("gps_x") or conn_el.get("longitude")
        gps_y = conn_el.get("GPS_Y") or conn_el.get("gps_y") or conn_el.get("latitude")

        try:
            gps_x = float(gps_x) if gps_x else None
            gps_y = float(gps_y) if gps_y else None
        except (ValueError, TypeError):
            gps_x = gps_y = None

        match_method = None
        accdb_match = None

        # Strategy 1: Match by Customer_Code == CUSTOMER ID
        if customer_code and customer_code in accdb_by_id:
            accdb_match = accdb_by_id[customer_code]
            match_method = "customer_code"
            unmatched_accdb.discard(customer_code)

        # Strategy 2: Match by Survey_ID == PLOT NUMBER (exact)
        if not accdb_match and survey_id:
            normalized_sid = survey_id.strip().upper()
            if normalized_sid in accdb_by_plot:
                accdb_match = accdb_by_plot[normalized_sid]
                match_method = "plot_number"
                cid = str(accdb_match.get("customer_id", "")).strip()
                unmatched_accdb.discard(cid)

        # Strategy 3: Match by plot prefix (ignoring type suffix)
        if not accdb_match and survey_id:
            sid_prefix = _plot_prefix(survey_id)
            candidate = accdb_by_prefix.get(sid_prefix)
            if candidate is not None:
                accdb_match = candidate
                accdb_plot = str(candidate.get("plot_number", "")).strip()
                match_method = f"plot_prefix"
                cid = str(accdb_match.get("customer_id", "")).strip()
                unmatched_accdb.discard(cid)

        # Strategy 4: GPS proximity (using meter table GPS as source)
        if not accdb_match and gps_x is not None and gps_y is not None and accdb_meters:
            best_dist = GPS_MATCH_RADIUS_M
            best_meter = None
            for m in accdb_meters:
                try:
                    m_lat = float(m.get("latitude") or 0)
                    m_lon = float(m.get("longitude") or 0)
                except (ValueError, TypeError):
                    continue
                if m_lat == 0 or m_lon == 0:
                    continue
                # Note: uGridPLAN GPS_X = longitude, GPS_Y = latitude
                dist = _haversine_m(gps_y, gps_x, m_lat, m_lon)
                if dist < best_dist:
                    best_dist = dist
                    best_meter = m
            if best_meter:
                cid = best_meter.get("customer_id", "")
                if cid and cid in accdb_by_id:
                    accdb_match = accdb_by_id[cid]
                    match_method = f"gps_meter_{int(best_dist)}m"
                    unmatched_accdb.discard(cid)

        if accdb_match:
            cid = str(accdb_match.get("customer_id", "")).strip()

            # Look up meter data (source of truth)
            meter = meters_by_cust.get(cid, {})
            accdb_meter_serial = meter.get("meterid", "")
            accdb_customer_type = meter.get("customer_type", "")
            accdb_lat = meter.get("latitude")
            accdb_lon = meter.get("longitude")

            try:
                accdb_lat = float(accdb_lat) if accdb_lat else None
                accdb_lon = float(accdb_lon) if accdb_lon else None
            except (ValueError, TypeError):
                accdb_lat = accdb_lon = None

            # Resolve best values: DB is source of truth for meter/type/GPS
            final_customer_type = accdb_customer_type or ugp_customer_type
            final_meter_serial = accdb_meter_serial or ugp_meter_serial
            final_gps_x = accdb_lon if accdb_lon else gps_x
            final_gps_y = accdb_lat if accdb_lat else gps_y

            # Data to store in SQLite metadata cache
            to_sqlite = {}
            if final_customer_type:
                to_sqlite["customer_type"] = final_customer_type
            if final_meter_serial:
                to_sqlite["meter_serial"] = final_meter_serial
            if final_gps_x is not None:
                to_sqlite["gps_x"] = final_gps_x
            if final_gps_y is not None:
                to_sqlite["gps_y"] = final_gps_y

            # Data to push to uGridPLAN (from DB)
            accdb_to_ugp = {}
            first = accdb_match.get("first_name", "")
            last = accdb_match.get("last_name", "")
            phone = accdb_match.get("cell_phone_1") or accdb_match.get("phone", "")
            plot = accdb_match.get("plot_number", "")

            if not customer_code and cid:
                accdb_to_ugp["Customer_Code"] = cid
            if accdb_customer_type and accdb_customer_type != ugp_customer_type:
                accdb_to_ugp["Customer_Type"] = accdb_customer_type
            if accdb_meter_serial and accdb_meter_serial != ugp_meter_serial:
                accdb_to_ugp["Meter_Serial"] = accdb_meter_serial
            if plot and not conn_el.get("notes"):
                accdb_to_ugp["notes"] = f"{first} {last} | {phone} | Plot {plot}"

            matched.append({
                "survey_id": survey_id,
                "customer_id": cid,
                "match_method": match_method,
                "customer_type": final_customer_type,
                "meter_serial": final_meter_serial,
                "gps_x": final_gps_x,
                "gps_y": final_gps_y,
                "accdb_name": f"{first} {last}".strip(),
                "accdb_phone": phone,
                "ugp_to_sqlite": to_sqlite,
                "accdb_to_ugp": accdb_to_ugp,
            })
        else:
            # Determine why no match was found
            normalized_sid = survey_id.strip().upper() if survey_id else ""
            if not normalized_sid:
                reason = "no_survey_id"
            elif customer_code and customer_code not in accdb_by_id:
                reason = "customer_code_not_in_accdb"
            else:
                sid_prefix = _plot_prefix(normalized_sid)
                if sid_prefix in accdb_by_prefix and accdb_by_prefix[sid_prefix] is None:
                    reason = "ambiguous_prefix"
                else:
                    reason = "no_accdb_customer"
            unmatched_ugp.append({
                "survey_id": survey_id,
                "customer_code": customer_code,
                "customer_type": ugp_customer_type,
                "gps_x": gps_x,
                "gps_y": gps_y,
                "reason": reason,
            })

    # Build detailed unmatched list with names/plot numbers
    unmatched_accdb_details = []
    for cid in unmatched_accdb:
        c = accdb_by_id.get(cid, {})
        first = c.get("first_name", "")
        last = c.get("last_name", "")
        plot = c.get("plot_number", "")
        unmatched_accdb_details.append({
            "customer_id": cid,
            "name": f"{first} {last}".strip(),
            "plot_number": plot,
            "reason": "plot_not_in_ugp" if plot else "no_plot_number",
        })

    return {
        "matched": matched,
        "unmatched_ugp": unmatched_ugp,
        "unmatched_accdb": unmatched_accdb_details,
        "matched_count": len(matched),
        "unmatched_ugp_count": len(unmatched_ugp),
        "unmatched_accdb_count": len(unmatched_accdb),
    }


# ---------------------------------------------------------------------------
# Site project mapping
# ---------------------------------------------------------------------------
# NOTE: cc_site_projects.project_id stores the uGridPLAN **project name**
# (not a UUID). UUIDs are session-specific and generated on load.

class SiteProjectMapping(BaseModel):
    site_code: str
    project_id: str   # actually project_name in uGridPLAN
    site_name: str = ""


def _load_project_for_site(client: UGPClient, project_name: str) -> str:
    """Load a uGridPLAN project by name and return the session UUID."""
    return client.load_project(project_name)


@router.get("/sites")
def list_site_projects(user: CurrentUser = Depends(require_employee)):
    """List all configured site-to-project mappings."""
    with get_auth_db() as conn:
        rows = conn.execute(
            "SELECT site_code, project_id, site_name, updated_at FROM cc_site_projects ORDER BY site_code"
        ).fetchall()

        # Sync info keyed by site_code (not session UUID)
        sync_info = {}
        for row in conn.execute(
            "SELECT ugp_project_id, MAX(synced_at) as last_sync, COUNT(*) as count "
            "FROM cc_customer_metadata WHERE ugp_project_id IS NOT NULL "
            "GROUP BY ugp_project_id"
        ).fetchall():
            sync_info[row["ugp_project_id"]] = {
                "last_sync": row["last_sync"],
                "synced_count": row["count"],
            }

        result = []
        for row in rows:
            info = sync_info.get(row["project_id"], {})
            result.append({
                "site_code": row["site_code"],
                "project_id": row["project_id"],
                "site_name": row["site_name"],
                "updated_at": row["updated_at"],
                "last_sync": info.get("last_sync"),
                "synced_count": info.get("synced_count", 0),
            })

        return {"sites": result}


@router.post("/sites")
def upsert_site_project(
    mapping: SiteProjectMapping,
    user: CurrentUser = Depends(require_employee),
):
    """Add or update a site-to-project mapping. Requires superadmin."""
    if user.role != CCRole.superadmin.value:
        raise HTTPException(status_code=403, detail="Superadmin only")

    now = datetime.utcnow().isoformat()
    with get_auth_db() as conn:
        conn.execute(
            """INSERT INTO cc_site_projects (site_code, project_id, site_name, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(site_code) DO UPDATE SET project_id = ?, site_name = ?, updated_at = ?""",
            (mapping.site_code, mapping.project_id, mapping.site_name, now,
             mapping.project_id, mapping.site_name, now),
        )

    return {"message": f"Site {mapping.site_code} mapped to uGridPLAN project '{mapping.project_id}'"}


# ---------------------------------------------------------------------------
# Auto-discover projects from uGridPLAN
# ---------------------------------------------------------------------------

@router.post("/discover")
def discover_projects(user: CurrentUser = Depends(require_employee)):
    """
    Auto-discover uGridPLAN projects and match them to known site codes.
    Automatically creates site-to-project mappings.
    Any employee can discover (non-destructive, creates mappings only).
    """

    from om_report import SITE_ABBREV

    try:
        client = _get_ugp_client()
        projects = client.list_projects()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"uGridPLAN unreachable: {e}")

    now = datetime.utcnow().isoformat()
    matched_sites = []
    unmatched_projects = []

    # Build reverse lookup: full name -> site code
    name_to_code = {}
    for code, full_name in SITE_ABBREV.items():
        name_to_code[full_name.lower()] = code
        name_to_code[code.lower()] = code

    for proj in projects:
        proj_name = proj.get("name", "")
        proj_code = proj.get("code", "")
        proj_portfolio = proj.get("portfolio", "")

        # Try to match project name/code to a site code
        site_code = None
        for candidate in [proj_code, proj_name]:
            candidate_lower = candidate.lower().strip()
            # Direct match on code (e.g. "MAK")
            if candidate_lower.upper() in SITE_ABBREV:
                site_code = candidate_lower.upper()
                break
            # Match on full name (e.g. "Ha Makebe")
            if candidate_lower in name_to_code:
                site_code = name_to_code[candidate_lower]
                break
            # Partial match: check if any known name is contained in the project name
            for known_name, code in name_to_code.items():
                if known_name in candidate_lower or candidate_lower in known_name:
                    site_code = code
                    break
            if site_code:
                break

        if site_code:
            site_name = SITE_ABBREV.get(site_code, proj_name)
            registry_key = proj_code or proj_name
            with get_auth_db() as conn:
                conn.execute(
                    """INSERT INTO cc_site_projects (site_code, project_id, site_name, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(site_code) DO UPDATE SET project_id = ?, site_name = ?, updated_at = ?""",
                    (site_code, registry_key, site_name, now, registry_key, site_name, now),
                )
            matched_sites.append({
                "site_code": site_code,
                "project_name": proj_name,
                "project_code": registry_key,
                "site_name": site_name,
            })
        else:
            unmatched_projects.append({
                "project_name": proj_name,
                "project_code": proj_code,
                "portfolio": proj_portfolio,
            })

    return {
        "discovered": len(projects),
        "matched": matched_sites,
        "matched_count": len(matched_sites),
        "unmatched_projects": unmatched_projects,
        "unmatched_count": len(unmatched_projects),
    }


# ---------------------------------------------------------------------------
# Preview (dry run)
# ---------------------------------------------------------------------------


@router.get("/connections")
def list_connections(
    site: str = Query(..., description="Site code (e.g. MAK)"),
    user: CurrentUser = Depends(require_employee),
):
    """Return connection elements from uGridPlan for a site.

    Used by the New Customer wizard to pick an existing connection and
    auto-fill Survey ID, GPS, and customer type.
    """
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT project_id FROM cc_site_projects WHERE site_code = ?",
            (site.upper(),),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No uGridPLAN project configured for site '{site}'.",
        )

    project_name = row["project_id"]

    try:
        client = _get_ugp_client()
        session_id = _load_project_for_site(client, project_name)
        raw = client.get_connections(session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"uGridPLAN fetch failed: {e}")

    connections = []
    for c in raw:
        sid = c.get("Survey_ID") or c.get("survey_id") or c.get("Name", "")
        gps_x = c.get("GPS_X") or c.get("gps_x") or c.get("longitude")
        gps_y = c.get("GPS_Y") or c.get("gps_y") or c.get("latitude")
        try:
            gps_x = float(gps_x) if gps_x else None
            gps_y = float(gps_y) if gps_y else None
        except (ValueError, TypeError):
            gps_x = gps_y = None

        connections.append({
            "survey_id": sid,
            "customer_type": (
                c.get("Customer_Type") or c.get("customer_type") or ""
            ).strip(),
            "customer_code": (
                c.get("Customer_Code") or c.get("customer_code") or ""
            ).strip(),
            "meter_serial": (
                c.get("Meter_Serial") or c.get("meter_serial") or ""
            ).strip(),
            "gps_lat": gps_y,
            "gps_lon": gps_x,
            "status": (
                c.get("Status") or c.get("status") or ""
            ).strip(),
        })

    return {
        "site": site.upper(),
        "count": len(connections),
        "connections": connections,
    }


# ---------------------------------------------------------------------------

@router.get("/preview")
def sync_preview(
    site: str = Query(..., description="Site code (e.g. MAK)"),
    user: CurrentUser = Depends(require_employee),
):
    """
    Preview sync results without making changes.
    Fetches connections from uGridPLAN and matches to customers.
    """
    # Look up project name
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT project_id FROM cc_site_projects WHERE site_code = ?",
            (site.upper(),),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No project configured for site '{site}'. Use the Discover button or add manually.",
        )

    project_name = row["project_id"]  # stored as project name

    # Load project to get session UUID, then fetch connections
    try:
        client = _get_ugp_client()
        session_id = _load_project_for_site(client, project_name)
        ugp_connections = client.get_connections(session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"uGridPLAN fetch failed: {e}")

    # Fetch customers + meter data for this site
    from customer_api import get_connection, _row_to_dict, _normalize_customer
    from om_report import SITE_ABBREV

    site_code = site.upper()
    concession_name = SITE_ABBREV.get(site_code, site)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM customers WHERE community = %s OR community LIKE %s",
            (site_code, f"%{concession_name}%"),
        )
        rows = cursor.fetchall()
        accdb_customers = [_normalize_customer(_row_to_dict(cursor, r)) for r in rows]

        # Also load meter data for this site (community = site code)
        accdb_meters = _load_meter_data(cursor, site_code)

    # Run matching
    results = _match_customers(ugp_connections, accdb_customers, accdb_meters)
    results["site"] = site_code
    results["project_name"] = project_name
    results["ugp_connection_count"] = len(ugp_connections)
    results["accdb_customer_count"] = len(accdb_customers)
    results["accdb_meter_count"] = len(accdb_meters)

    return results


# ---------------------------------------------------------------------------
# Execute sync
# ---------------------------------------------------------------------------

class SyncExecuteRequest(BaseModel):
    site: str
    push_to_ugp: bool = True
    pull_to_sqlite: bool = True


@router.post("/execute")
def sync_execute(
    req: SyncExecuteRequest,
    user: CurrentUser = Depends(require_employee),
):
    """
    Execute sync for a site.
    - pull_to_sqlite: store customer_type/meter_serial/GPS from uGridPLAN in SQLite
    - push_to_ugp: push customer data (Customer_Code, notes, Load_A) to uGridPLAN
    - Writes uGridPLAN GPS back to customers that have empty gps_lat/gps_lon
    - Computes Load_A from consumption history and pushes to uGridPLAN
    """
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT project_id FROM cc_site_projects WHERE site_code = ?",
            (req.site.upper(),),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"No project configured for site '{req.site}'")

    project_name = row["project_id"]  # stored as project name

    try:
        client = _get_ugp_client()
        session_id = _load_project_for_site(client, project_name)
        ugp_connections = client.get_connections(session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"uGridPLAN fetch failed: {e}")

    from customer_api import get_connection, _row_to_dict, _normalize_customer
    from om_report import SITE_ABBREV

    site_code = req.site.upper()
    concession_name = SITE_ABBREV.get(site_code, req.site)

    # Index uGridPLAN connections by Survey_ID for GPS lookup
    ugp_conn_by_sid: Dict[str, Dict] = {}
    for c in ugp_connections:
        sid = c.get("Survey_ID") or c.get("survey_id") or c.get("Name", "")
        if sid:
            ugp_conn_by_sid[sid] = c

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM customers WHERE community = %s OR community LIKE %s",
            (site_code, f"%{concession_name}%"),
        )
        rows = cursor.fetchall()
        accdb_customers = [_normalize_customer(_row_to_dict(cursor, r)) for r in rows]

        accdb_meters = _load_meter_data(cursor, site_code)

    match_results = _match_customers(ugp_connections, accdb_customers, accdb_meters)

    # Fetch per-connection voltages via voltage-drop analysis
    vdrop_voltages: Dict[str, float] = {}
    try:
        vdrop_voltages = client.get_vdrop_voltages(session_id)
    except Exception as e:
        logger.warning("Could not fetch vdrop voltages: %s", e)

    now = datetime.utcnow().isoformat()
    sqlite_written = 0
    ugp_updated = 0
    gps_written = 0
    load_a_computed = 0
    load_a_via_vdrop = 0
    load_a_via_default = 0

    # Pull: uGridPLAN -> SQLite (customer_type, meter_serial, GPS)
    if req.pull_to_sqlite:
        with get_auth_db() as conn:
            for m in match_results["matched"]:
                meta = m.get("ugp_to_sqlite", {})
                if not meta:
                    continue

                conn.execute(
                    """INSERT INTO cc_customer_metadata
                       (customer_id, customer_type, meter_serial, gps_x, gps_y,
                        ugp_survey_id, ugp_project_id, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(customer_id) DO UPDATE SET
                        customer_type = COALESCE(?, customer_type),
                        meter_serial = COALESCE(?, meter_serial),
                        gps_x = COALESCE(?, gps_x),
                        gps_y = COALESCE(?, gps_y),
                        ugp_survey_id = ?,
                        ugp_project_id = ?,
                        synced_at = ?""",
                    (
                        m["customer_id"],
                        meta.get("customer_type"),
                        meta.get("meter_serial"),
                        meta.get("gps_x"),
                        meta.get("gps_y"),
                        m["survey_id"],
                        project_name,  # store stable name, not session UUID
                        now,
                        # ON CONFLICT values
                        meta.get("customer_type"),
                        meta.get("meter_serial"),
                        meta.get("gps_x"),
                        meta.get("gps_y"),
                        m["survey_id"],
                        project_name,
                        now,
                    ),
                )
                sqlite_written += 1

    # ---------------------------------------------------------------
    # GPS write-back: uGridPLAN GPS -> customers gps_lat/gps_lon
    # ---------------------------------------------------------------
    with get_connection() as conn:
        cursor = conn.cursor()
        for m in match_results["matched"]:
            cid = m.get("customer_id")
            sid = m.get("survey_id")
            if not cid or not sid:
                continue

            # Get the uGridPLAN GPS for this connection
            ugp_conn = ugp_conn_by_sid.get(sid, {})
            ugp_gps_x = ugp_conn.get("GPS_X") or ugp_conn.get("gps_x")
            ugp_gps_y = ugp_conn.get("GPS_Y") or ugp_conn.get("gps_y")
            if ugp_gps_x is None or ugp_gps_y is None:
                continue

            try:
                ugp_gps_x = float(ugp_gps_x)
                ugp_gps_y = float(ugp_gps_y)
            except (ValueError, TypeError):
                continue

            # Check if the customer already has GPS
            try:
                cursor.execute(
                    "SELECT gps_lat, gps_lon FROM customers WHERE customer_id_legacy = %s",
                    (int(cid),),
                )
                row = cursor.fetchone()
                if not row:
                    continue

                existing_lat = row[0]
                existing_lon = row[1]
                has_gps = (
                    existing_lat is not None
                    and existing_lon is not None
                    and float(existing_lat) != 0.0
                    and float(existing_lon) != 0.0
                )
            except (ValueError, TypeError):
                has_gps = False

            if not has_gps:
                try:
                    cursor.execute(
                        "UPDATE customers SET gps_lat = %s, gps_lon = %s "
                        "WHERE customer_id_legacy = %s",
                        (ugp_gps_x, ugp_gps_y, int(cid)),
                    )
                    conn.commit()
                    gps_written += 1
                except Exception as e:
                    logger.warning("GPS write-back failed for customer %s: %s", cid, e)

    # ---------------------------------------------------------------
    # Compute Load_A from consumption history and push to uGridPLAN.
    # ---------------------------------------------------------------
    if req.push_to_ugp:
        batch_updates: Dict[str, Dict[str, Any]] = {}
        with get_connection() as conn:
            cursor = conn.cursor()

            for m in match_results["matched"]:
                sid = m.get("survey_id", "")
                updates = dict(m.get("accdb_to_ugp", {}))

                # Derive account number from Survey_ID for consumption lookup
                acct = _survey_id_to_account_number(sid)
                if acct:
                    avg_kwh = _compute_avg_kwh_per_day(cursor, acct)
                    if avg_kwh is not None and avg_kwh > 0:
                        vdrop_v = vdrop_voltages.get(sid)
                        if vdrop_v is not None and vdrop_v > 0:
                            voltage = vdrop_v
                            used_vdrop = True
                        else:
                            voltage = SYNC_LV_VOLTAGE
                            used_vdrop = False

                        load_a = _kwh_per_day_to_amps(avg_kwh, voltage)
                        load_a = round(load_a, 2)
                        if load_a > 0:
                            updates["Load_A"] = load_a
                            load_a_computed += 1

                            if used_vdrop:
                                note = f"Load_A={load_a}A (Vdrop {voltage:.1f}V)"
                                load_a_via_vdrop += 1
                            else:
                                note = f"Load_A={load_a}A (default {voltage:.0f}V, Vdrop unavailable)"
                                load_a_via_default += 1
                            updates["notes"] = note

                if updates:
                    batch_updates[sid] = updates

        # Push ALL updates to uGridPLAN in a single batch call
        if batch_updates:
            logger.info("Pushing %d updates to uGridPLAN via batch endpoint", len(batch_updates))
            try:
                batch_result = client.batch_update_connections(session_id, batch_updates)
                ugp_updated = batch_result.get("updated", 0)
                not_found = batch_result.get("not_found", [])
                if not_found:
                    logger.warning(
                        "Batch update: %d connections not found in uGridPLAN: %s",
                        len(not_found), not_found[:10],
                    )
            except Exception as e:
                logger.error("Batch update call failed: %s", e)
                ugp_updated = 0

    return {
        "site": req.site.upper(),
        "matched": match_results["matched_count"],
        "sqlite_written": sqlite_written,
        "ugp_updated": ugp_updated,
        "gps_written": gps_written,
        "load_a_computed": load_a_computed,
        "load_a_via_vdrop": load_a_via_vdrop,
        "load_a_via_default": load_a_via_default,
        "vdrop_voltages_available": len(vdrop_voltages),
        "default_voltage": SYNC_LV_VOLTAGE,
        "unmatched_ugp": match_results["unmatched_ugp_count"],
        "unmatched_accdb": match_results["unmatched_accdb_count"],
    }


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------

@router.get("/status")
def sync_status(user: CurrentUser = Depends(require_employee)):
    """Show last sync timestamp and counts per site."""
    with get_auth_db() as conn:
        # Get all configured sites
        sites = conn.execute(
            "SELECT site_code, project_id, site_name FROM cc_site_projects ORDER BY site_code"
        ).fetchall()

        # Get metadata counts and last sync per project
        meta_stats = {}
        for row in conn.execute(
            """SELECT ugp_project_id,
                      COUNT(*) as synced,
                      SUM(CASE WHEN customer_type IS NOT NULL AND customer_type != '' THEN 1 ELSE 0 END) as with_type,
                      MAX(synced_at) as last_sync
               FROM cc_customer_metadata
               WHERE ugp_project_id IS NOT NULL
               GROUP BY ugp_project_id"""
        ).fetchall():
            meta_stats[row["ugp_project_id"]] = dict(row)

        # Total metadata records
        total_row = conn.execute("SELECT COUNT(*) FROM cc_customer_metadata").fetchone()
        total_synced = total_row[0]

        # Type distribution
        type_dist = conn.execute(
            "SELECT customer_type, COUNT(*) as cnt FROM cc_customer_metadata "
            "WHERE customer_type IS NOT NULL AND customer_type != '' "
            "GROUP BY customer_type ORDER BY cnt DESC"
        ).fetchall()

        result = []
        for site in sites:
            stats = meta_stats.get(site["project_id"], {})
            result.append({
                "site_code": site["site_code"],
                "site_name": site["site_name"],
                "project_id": site["project_id"],
                "synced_customers": stats.get("synced", 0),
                "with_customer_type": stats.get("with_type", 0),
                "last_sync": stats.get("last_sync"),
            })

        return {
            "sites": result,
            "total_synced": total_synced,
            "type_distribution": [{"type": r["customer_type"], "count": r["cnt"]} for r in type_dist],
        }
