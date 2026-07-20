"""
SMA Sunny Portal Client
=======================

Fetches plant availability data from SMA Sunny Portal via its API.

Environment variables:
  SMA_SUNNY_PORTAL_URL   — Sunny Portal base URL
  SMA_USERNAME            — Sunny Portal login
  SMA_PASSWORD            — Sunny Portal password
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("cc-api.sma")

SMA_URL = os.environ.get("SMA_SUNNY_PORTAL_URL", "https://www.sunnyportal.com")
SMA_USERNAME = os.environ.get("SMA_USERNAME", "")
SMA_PASSWORD = os.environ.get("SMA_PASSWORD", "")

API_TIMEOUT = 60


class SMAClient:
    """Minimal SMA Sunny Portal API client."""

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.url = (url or SMA_URL).rstrip("/")
        self.username = username or SMA_USERNAME
        self.password = password or SMA_PASSWORD
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        sess = requests.Session()
        if self.username and self.password:
            try:
                r = sess.post(
                    f"{self.url}/Templates/Start.aspx",
                    data={"txtUserName": self.username, "txtPassword": self.password},
                    timeout=API_TIMEOUT,
                )
                r.raise_for_status()
                logger.info("SMA Sunny Portal: authenticated")
            except Exception as e:
                logger.error("SMA login failed: %s", e)
        self._session = sess
        return sess

    def get_plant_availability(
        self,
        plant_id: str,
        date_from: date,
        date_to: date,
    ) -> Dict[str, Any]:
        """Get availability percentage for a plant in a date range.

        Returns dict with availability_pct and downtime_hours.
        """
        sess = self._get_session()
        try:
            r = sess.get(
                f"{self.url}/WebApi/Plant/GetPlantAvailability",
                params={
                    "plantId": plant_id,
                    "from": date_from.isoformat(),
                    "to": date_to.isoformat(),
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            body = r.json()
            return {
                "plant_id": plant_id,
                "availability_pct": float(body.get("availability", 0)),
                "downtime_hours": float(body.get("downtimeHours", 0)),
            }
        except Exception as e:
            logger.error("SMA availability failed for plant %s: %s", plant_id, e)
            return {"plant_id": plant_id, "availability_pct": None, "downtime_hours": None}


def pull_sma_availability(
    plant_site_map: Dict[str, str],
    date_from: date,
    date_to: date,
) -> List[Dict[str, Any]]:
    """Pull availability for multiple plants and return results.

    Args:
        plant_site_map: {plant_id: site_code} mapping
        date_from: start date
        date_to: end date
    """
    client = SMAClient()
    results: List[Dict[str, Any]] = []
    for plant_id, site_code in plant_site_map.items():
        avail = client.get_plant_availability(plant_id, date_from, date_to)
        avail["site_code"] = site_code
        avail["source"] = "sma"
        results.append(avail)
    return results
