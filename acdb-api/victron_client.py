"""
Victron VRM Client
==================

Fetches system availability data from Victron VRM REST API.

Environment variables:
  VRM_API_URL     — VRM API base URL (default: https://vrmapi.victronenergy.com)
  VRM_USERNAME    — VRM login email
  VRM_PASSWORD    — VRM password
  VRM_TOKEN       — Pre-obtained Bearer token (optional, skips login)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("cc-api.victron")

VRM_API_URL = os.environ.get("VRM_API_URL", "https://vrmapi.victronenergy.com")
VRM_USERNAME = os.environ.get("VRM_USERNAME", "")
VRM_PASSWORD = os.environ.get("VRM_PASSWORD", "")
VRM_TOKEN = os.environ.get("VRM_TOKEN", "")

API_TIMEOUT = 60


class VictronClient:
    """Victron VRM REST API client."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.base_url = (base_url or VRM_API_URL).rstrip("/")
        self.username = username or VRM_USERNAME
        self.password = password or VRM_PASSWORD
        self._token = token or VRM_TOKEN

    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        r = requests.post(
            f"{self.base_url}/v2/auth/login",
            json={"username": self.username, "password": self.password},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        self._token = body.get("token", "")
        if not self._token:
            raise RuntimeError("VRM login failed — no token in response")
        logger.info("Victron VRM: authenticated")
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._ensure_token()}", "Content-Type": "application/json"}

    def list_systems(self) -> List[Dict[str, Any]]:
        """List all VRM systems/sites."""
        r = requests.get(
            f"{self.base_url}/v1/users/self/systems",
            headers=self._headers(),
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        return body.get("records", body) if isinstance(body, dict) else body

    def get_system_stats(self, system_id: str, date_from: date, date_to: date) -> Dict[str, Any]:
        """Get system stats for a date range."""
        r = requests.get(
            f"{self.base_url}/v1/installations/{system_id}/stats",
            headers=self._headers(),
            params={
                "instance": "",
                "start": int(datetime.combine(date_from, datetime.min.time()).timestamp()),
                "end": int(datetime.combine(date_to, datetime.max.time()).timestamp()),
                "type": "daily",
            },
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def get_system_availability(self, system_id: str, date_from: date, date_to: date) -> Dict[str, Any]:
        """Compute availability percentage for a system.

        Uses daily stats — counts days with data vs total days in range.
        """
        try:
            stats = self.get_system_stats(system_id, date_from, date_to)
            records = stats.get("records", [])
            total_days = (date_to - date_from).days + 1
            days_with_data = len(records)
            availability_pct = round((days_with_data / total_days) * 100, 2) if total_days > 0 else 0.0
            downtime_hours = round((total_days - days_with_data) * 24, 2)

            return {
                "system_id": system_id,
                "availability_pct": availability_pct,
                "downtime_hours": downtime_hours,
            }
        except Exception as e:
            logger.error("VRM availability failed for system %s: %s", system_id, e)
            return {"system_id": system_id, "availability_pct": None, "downtime_hours": None}


def pull_victron_availability(
    system_site_map: Dict[str, str],
    date_from: date,
    date_to: date,
) -> List[Dict[str, Any]]:
    """Pull availability for multiple VRM systems.

    Args:
        system_site_map: {system_id: site_code} mapping
        date_from: start date
        date_to: end date
    """
    client = VictronClient()
    results: List[Dict[str, Any]] = []
    for system_id, site_code in system_site_map.items():
        avail = client.get_system_availability(system_id, date_from, date_to)
        avail["site_code"] = site_code
        avail["source"] = "victron"
        results.append(avail)
    return results
