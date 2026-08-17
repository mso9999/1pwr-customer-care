"""Machine-to-machine integration endpoints (read-only).

Exposes O&M aggregates and the Department of Energy site-status table to
internal services — initially the Nexus Reports builder (1pwr-reports) —
without requiring an employee JWT.

Auth: shared secret in the X-CC-Integration-Key header, compared against the
CC_INTEGRATION_KEY environment variable. If the env var is unset the router
responds 503 (integration disabled) rather than failing open.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import date
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from customer_api import get_connection

logger = logging.getLogger("acdb-api.integration")

router = APIRouter(prefix="/api/integration", tags=["integration"])

# DoE letter categories mapped from stored customer_type codes.
# SCP is the legacy code for (primarily primary) school connections; HHSME is
# a mixed household/business and is counted with businesses.
_HH_TYPES = ("HH", "HH1", "HH2", "HH3")
_BIZ_TYPES = ("SME", "HHSME")
_SCHOOL_TYPES = ("SCH", "SCP")
_CLINIC_TYPES = ("HC",)
_CHURCH_TYPES = ("CHU",)

# Non-site buckets present in the customers table (test rigs, bad imports).
_EXCLUDED_COMMUNITIES = ("LAB", "UNK")


def _require_key(x_cc_integration_key: Optional[str]) -> None:
    expected = os.environ.get("CC_INTEGRATION_KEY", "")
    if not expected:
        raise HTTPException(503, "integration disabled (CC_INTEGRATION_KEY unset)")
    if not x_cc_integration_key or not secrets.compare_digest(
        x_cc_integration_key, expected
    ):
        raise HTTPException(401, "invalid integration key")


@router.get("/om/overview")
def integration_overview(
    as_of: Optional[str] = Query(None),
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Portfolio snapshot: connections by type, sites, lifetime energy."""
    _require_key(x_cc_integration_key)

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM customers "
            "WHERE date_service_connected IS NOT NULL "
            "AND date_service_terminated IS NULL"
        )
        active = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COALESCE(NULLIF(TRIM(UPPER(customer_type)), ''), 'UNKNOWN'), "
            "COUNT(*) FROM customers "
            "WHERE date_service_connected IS NOT NULL "
            "AND date_service_terminated IS NULL "
            "GROUP BY 1"
        )
        by_type = {row[0]: int(row[1]) for row in cursor.fetchall()}

        residential = sum(by_type.get(t, 0) for t in _HH_TYPES)
        commercial = sum(v for k, v in by_type.items() if k not in _HH_TYPES)

        cursor.execute(
            "SELECT site_code, full_name, status FROM site_metadata "
            "WHERE country = 'LS' ORDER BY site_code"
        )
        sites = [
            {"code": r[0], "name": r[1], "status": r[2]}
            for r in cursor.fetchall()
        ]

        cursor.execute("SELECT COALESCE(SUM(kwh), 0) FROM monthly_consumption")
        total_kwh = float(cursor.fetchone()[0])

        return {
            "as_of": as_of or date.today().isoformat(),
            "connections": {
                "total": active,
                "residential": residential,
                "commercial": commercial,
                "by_type": by_type,
            },
            "sites": {"total": len(sites), "names": [s["code"] for s in sites],
                      "detail": sites},
            "total_mwh": round(total_kwh / 1000, 2),
            "energy_source": "monthly_consumption rollup",
        }


@router.get("/om/consumption")
def integration_consumption(
    start: str = Query(..., description="ISO date, inclusive"),
    end: str = Query(..., description="ISO date, inclusive"),
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Metered energy sold (kWh) between two dates, from hourly telemetry."""
    _require_key(x_cc_integration_key)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(kwh), 0) FROM hourly_consumption "
            "WHERE reading_hour >= %s::date "
            "AND reading_hour < (%s::date + interval '1 day')",
            (start, end),
        )
        kwh = float(cursor.fetchone()[0])

    return {
        "total_kwh": kwh,
        "total_mwh": round(kwh / 1000, 2),
        "start": start,
        "end": end,
        "source_table": "hourly_consumption",
    }


@router.get("/doe/site-status")
def integration_doe_site_status(
    x_cc_integration_key: Optional[str] = Header(None),
):
    """Per-site connection status in the Department of Energy letter format.

    Base population: customers with a service connection who are not
    terminated. "With electricity" = commissioned (energised). Sites with no
    connected customers are returned under `not_yet_connected` so the report
    can list them as under construction / out of M&V scope.
    """
    _require_key(x_cc_integration_key)

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                community,
                COUNT(*) FILTER (WHERE UPPER(customer_type) IN %s) AS households,
                COUNT(*) FILTER (WHERE UPPER(customer_type) IN %s) AS businesses,
                COUNT(*) FILTER (WHERE UPPER(customer_type) IN %s) AS schools,
                COUNT(*) FILTER (WHERE UPPER(customer_type) IN %s) AS clinics,
                COUNT(*) FILTER (WHERE UPPER(customer_type) IN %s) AS churches,
                COUNT(*) FILTER (WHERE customer_type IS NULL
                                 OR UPPER(customer_type) NOT IN %s) AS other,
                COUNT(*) AS connections_total,
                COUNT(*) FILTER (WHERE meter_installed) AS with_meter,
                COUNT(*) FILTER (WHERE NOT meter_installed) AS without_meter,
                COUNT(*) FILTER (WHERE readyboard_installed) AS with_readyboard,
                COUNT(*) FILTER (WHERE NOT readyboard_installed) AS without_readyboard,
                COUNT(*) FILTER (WHERE customer_commissioned) AS with_electricity,
                COUNT(*) FILTER (WHERE NOT customer_commissioned) AS without_electricity
            FROM customers
            WHERE date_service_connected IS NOT NULL
              AND date_service_terminated IS NULL
              AND community NOT IN %s
            GROUP BY community
            """,
            (
                _HH_TYPES,
                _BIZ_TYPES,
                _SCHOOL_TYPES,
                _CLINIC_TYPES,
                _CHURCH_TYPES,
                _HH_TYPES + _BIZ_TYPES + _SCHOOL_TYPES + _CLINIC_TYPES + _CHURCH_TYPES,
                _EXCLUDED_COMMUNITIES,
            ),
        )
        cols = [d[0] for d in cursor.description]
        rows = {r[0]: dict(zip(cols, r)) for r in cursor.fetchall()}

        cursor.execute(
            "SELECT community, COUNT(*) FROM customers "
            "WHERE community NOT IN %s GROUP BY community",
            (_EXCLUDED_COMMUNITIES,),
        )
        registered = {r[0]: int(r[1]) for r in cursor.fetchall()}

        cursor.execute(
            "SELECT site_code, full_name, region, status FROM site_metadata "
            "WHERE country = 'LS' ORDER BY site_code"
        )
        meta = {r[0]: {"name": r[1], "region": r[2], "status": r[3]}
                for r in cursor.fetchall()}

    sites = []
    totals = {}
    for code, row in sorted(rows.items()):
        m = meta.get(code, {})
        entry = {
            "site_code": code,
            "name": m.get("name") or code,
            "region": m.get("region"),
            "site_status": m.get("status"),
            "registered_total": registered.get(code, 0),
            **{k: int(v) for k, v in row.items() if k != "community"},
        }
        sites.append(entry)
        for k, v in row.items():
            if k != "community":
                totals[k] = totals.get(k, 0) + int(v)

    not_yet_connected = [
        {"site_code": code, "name": m["name"], "region": m["region"],
         "site_status": m["status"],
         "registered_total": registered.get(code, 0)}
        for code, m in sorted(meta.items())
        if code not in rows
    ]

    return {
        "as_of": date.today().isoformat(),
        "category_notes": {
            "households": list(_HH_TYPES),
            "businesses": list(_BIZ_TYPES),
            "schools": list(_SCHOOL_TYPES) + ["SCP = legacy school code"],
            "clinics": list(_CLINIC_TYPES),
            "churches": list(_CHURCH_TYPES),
        },
        "sites": sites,
        "totals": totals,
        "not_yet_connected": not_yet_connected,
        "excluded_communities": list(_EXCLUDED_COMMUNITIES),
    }
