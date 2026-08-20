"""Cost-allocation usage signals for the Nexus AWS cost allocator.

Nexus runs a nightly job that allocates the shared AWS bill across countries
using causal drivers. This endpoint is the CC driver source: it reports this
country lane's active customer count (drives CC/1PDB hosting + backup costs),
active meter count (drives IoT/DynamoDB/OTA costs), and database size (drives
backup storage). It is keyed with a shared server API key — the numbers are
commercially sensitive but not personal data.
"""
from __future__ import annotations

import hmac as _hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from country_config import get_country, live_known_sites
from customer_api import get_connection

logger = logging.getLogger("cc-api.cost-signals")

router = APIRouter(prefix="/api/admin/cost-signals", tags=["cost-signals"])

_COST_SIGNAL_KEYS = [
    k.strip() for k in os.environ.get("COST_SIGNALS_API_KEYS", "").split(",") if k.strip()
]


def _require_cost_signal_key(request: Request) -> None:
    presented = request.headers.get("X-API-Key", "")
    if not _COST_SIGNAL_KEYS:
        raise HTTPException(status_code=503, detail="Cost signals are not configured on this lane.")
    if not presented or not any(_hmac.compare_digest(k, presented) for k in _COST_SIGNAL_KEYS):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("")
def cost_signals(request: Request):
    """Per-country driver values for the Nexus cost allocation rubric."""
    _require_cost_signal_key(request)
    country = get_country()
    known_sites = live_known_sites(country.code)

    with get_connection() as conn:
        cursor = conn.cursor()

        # Active customers: on a known site, service not terminated (same
        # definition as the O&M report overview).
        cursor.execute(
            "SELECT community, date_service_terminated FROM customers "
            "WHERE community IS NOT NULL AND community <> ''"
        )
        total_customers = 0
        terminated = 0
        for community, terminated_at in cursor.fetchall():
            if str(community or "").strip().upper() not in known_sites:
                continue
            total_customers += 1
            if terminated_at is not None:
                terminated += 1
        active_customers = total_customers - terminated

        # Meters attached to non-terminated customers drive telemetry volume.
        # Meters join to customers through accounts (meters.account_number →
        # accounts.account_number → accounts.customer_id).
        cursor.execute(
            "SELECT COUNT(DISTINCT m.meter_id) FROM meters m "
            "JOIN accounts a ON a.account_number = m.account_number "
            "JOIN customers c ON c.id = a.customer_id "
            "WHERE c.date_service_terminated IS NULL"
        )
        active_meters = int(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT pg_database_size(current_database())")
        db_size_bytes = int(cursor.fetchone()[0] or 0)

    return {
        "country_code": country.code,
        "active_customers": active_customers,
        "active_meters": active_meters,
        "db_size_bytes": db_size_bytes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
