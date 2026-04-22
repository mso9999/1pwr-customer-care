"""
1PDB data access for the gensite module.

Thin layer over psycopg2 — no ORM, matching the style of the rest of the CC
API (crud.py, commission.py). All queries use the pooled connection obtained
via `customer_api.get_connection()`.

Credentials stored as Fernet ciphertext (bytea); decrypted only when handed
to an adapter or for a `rotate` operation. API responses NEVER include
plaintext — the `masked_credential_view` helper returns display-safe dicts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2.extras

from .adapters.base import SiteCredential, SiteEquipment
from .crypto import decrypt, encrypt, mask

logger = logging.getLogger("cc-api.gensite.store")


# ---------------------------------------------------------------------------
# Connection helper (lazy import to avoid a circular with customer_api)
# ---------------------------------------------------------------------------

def _conn():
    from customer_api import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# sites
# ---------------------------------------------------------------------------

def seed_sites_from_country_config() -> int:
    """Insert one row per site known to country_config.site_abbrev for the
    currently active country. Idempotent — only inserts missing codes.

    Returns count of newly inserted rows.
    """
    from country_config import SITE_ABBREV, SITE_DISTRICTS, COUNTRY

    inserted = 0
    with _conn() as conn:
        with conn.cursor() as cur:
            for code, name in SITE_ABBREV.items():
                cur.execute(
                    """
                    INSERT INTO sites (code, country, kind, display_name, district, ugp_project_id)
                    VALUES (%s, %s, 'minigrid', %s, %s, %s)
                    ON CONFLICT (code) DO NOTHING
                    """,
                    (code, COUNTRY.code, name, SITE_DISTRICTS.get(code), code),
                )
                if cur.rowcount:
                    inserted += cur.rowcount
        conn.commit()
    if inserted:
        logger.info(
            "gensite: seeded %d sites into 1PDB from country_config (%s)",
            inserted,
            COUNTRY.code,
        )
    return inserted


def list_sites(country: Optional[str] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if country:
                cur.execute(
                    "SELECT * FROM sites WHERE country = %s ORDER BY code",
                    (country.upper(),),
                )
            else:
                cur.execute("SELECT * FROM sites ORDER BY code")
            return [dict(r) for r in cur.fetchall()]


def get_site(code: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM sites WHERE code = %s", (code.upper(),))
            row = cur.fetchone()
            return dict(row) if row else None


def upsert_site(
    *,
    code: str,
    country: str,
    kind: str,
    display_name: str,
    district: Optional[str] = None,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    ugp_project_id: Optional[str] = None,
    notes: Optional[str] = None,
    commissioned_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO sites (
                    code, country, kind, display_name, district,
                    gps_lat, gps_lon, ugp_project_id, notes, commissioned_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    country         = EXCLUDED.country,
                    kind            = EXCLUDED.kind,
                    display_name    = EXCLUDED.display_name,
                    district        = COALESCE(EXCLUDED.district,        sites.district),
                    gps_lat         = COALESCE(EXCLUDED.gps_lat,         sites.gps_lat),
                    gps_lon         = COALESCE(EXCLUDED.gps_lon,         sites.gps_lon),
                    ugp_project_id  = COALESCE(EXCLUDED.ugp_project_id,  sites.ugp_project_id),
                    notes           = COALESCE(EXCLUDED.notes,           sites.notes),
                    commissioned_at = COALESCE(EXCLUDED.commissioned_at, sites.commissioned_at)
                RETURNING *
                """,
                (
                    code.upper(), country.upper(), kind, display_name, district,
                    gps_lat, gps_lon, ugp_project_id, notes, commissioned_at,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


# ---------------------------------------------------------------------------
# site_equipment
# ---------------------------------------------------------------------------

def list_equipment(site_code: str, include_decommissioned: bool = False) -> List[Dict[str, Any]]:
    q = "SELECT * FROM site_equipment WHERE site_code = %s"
    if not include_decommissioned:
        q += " AND decommissioned_at IS NULL"
    q += " ORDER BY id"
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(q, (site_code.upper(),))
            return [dict(r) for r in cur.fetchall()]


def insert_equipment(
    *,
    site_code: str,
    kind: str,
    vendor: str,
    model: Optional[str],
    serial: Optional[str],
    role: Optional[str],
    nameplate_kw: Optional[float],
    nameplate_kwh: Optional[float],
    firmware_version: Optional[str] = None,
    commissioned_at: Optional[datetime] = None,
    installed_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO site_equipment (
                    site_code, kind, vendor, model, serial, role,
                    nameplate_kw, nameplate_kwh, firmware_version,
                    commissioned_at, installed_by, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_site_equipment_serial DO UPDATE SET
                    kind             = EXCLUDED.kind,
                    model            = COALESCE(EXCLUDED.model,            site_equipment.model),
                    role             = COALESCE(EXCLUDED.role,             site_equipment.role),
                    nameplate_kw     = COALESCE(EXCLUDED.nameplate_kw,     site_equipment.nameplate_kw),
                    nameplate_kwh    = COALESCE(EXCLUDED.nameplate_kwh,    site_equipment.nameplate_kwh),
                    firmware_version = COALESCE(EXCLUDED.firmware_version, site_equipment.firmware_version),
                    commissioned_at  = COALESCE(EXCLUDED.commissioned_at,  site_equipment.commissioned_at),
                    installed_by     = COALESCE(EXCLUDED.installed_by,     site_equipment.installed_by),
                    notes            = COALESCE(EXCLUDED.notes,            site_equipment.notes),
                    decommissioned_at = NULL
                RETURNING *
                """,
                (
                    site_code.upper(), kind, vendor, model, serial, role,
                    nameplate_kw, nameplate_kwh, firmware_version,
                    commissioned_at, installed_by, notes,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def decommission_equipment(equipment_id: int, when: Optional[datetime] = None) -> bool:
    when = when or datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE site_equipment SET decommissioned_at = %s WHERE id = %s",
                (when, equipment_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
    return ok


def as_adapter_equipment(row: Dict[str, Any]) -> SiteEquipment:
    return SiteEquipment(
        id=row["id"],
        site_code=row["site_code"],
        vendor=row["vendor"],
        kind=row["kind"],
        model=row.get("model"),
        serial=row.get("serial"),
        role=row.get("role"),
    )


# ---------------------------------------------------------------------------
# site_credentials
# ---------------------------------------------------------------------------

def list_credentials(site_code: str) -> List[Dict[str, Any]]:
    """Return masked credential rows — never plaintext secrets."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, site_code, vendor, backend, base_url, username,
                       site_id_on_vendor, extra, created_by, created_at,
                       rotated_at, last_verified_at, last_verified_ok,
                       last_verify_error,
                       (secret_ciphertext IS NOT NULL)   AS has_secret,
                       (api_key_ciphertext IS NOT NULL)  AS has_api_key
                FROM site_credentials
                WHERE site_code = %s
                ORDER BY vendor, backend
                """,
                (site_code.upper(),),
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["username_masked"] = mask(r.get("username") or "")
    return rows


def masked_credential_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip bytea payloads and mask username for API responses."""
    out = dict(row)
    out.pop("secret_ciphertext", None)
    out.pop("api_key_ciphertext", None)
    out["username_masked"] = mask(out.get("username") or "")
    return out


def upsert_credential(
    *,
    site_code: str,
    vendor: str,
    backend: str,
    base_url: Optional[str],
    username: Optional[str],
    secret: Optional[str],
    api_key: Optional[str],
    site_id_on_vendor: Optional[str],
    extra: Optional[Dict[str, Any]],
    created_by: Optional[str],
    verify_ok: Optional[bool] = None,
    verify_error: Optional[str] = None,
    verify_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO site_credentials (
                    site_code, vendor, backend, base_url, username,
                    secret_ciphertext, api_key_ciphertext, site_id_on_vendor,
                    extra, created_by, created_at,
                    last_verified_at, last_verified_ok, last_verify_error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_site_credentials_site_vendor_backend DO UPDATE SET
                    base_url            = EXCLUDED.base_url,
                    username            = EXCLUDED.username,
                    secret_ciphertext   = COALESCE(EXCLUDED.secret_ciphertext,  site_credentials.secret_ciphertext),
                    api_key_ciphertext  = COALESCE(EXCLUDED.api_key_ciphertext, site_credentials.api_key_ciphertext),
                    site_id_on_vendor   = COALESCE(EXCLUDED.site_id_on_vendor,  site_credentials.site_id_on_vendor),
                    extra               = EXCLUDED.extra,
                    rotated_at          = %s,
                    last_verified_at    = EXCLUDED.last_verified_at,
                    last_verified_ok    = EXCLUDED.last_verified_ok,
                    last_verify_error   = EXCLUDED.last_verify_error
                RETURNING id, site_code, vendor, backend, base_url, username,
                          site_id_on_vendor, extra, created_by, created_at,
                          rotated_at, last_verified_at, last_verified_ok,
                          last_verify_error,
                          (secret_ciphertext IS NOT NULL)  AS has_secret,
                          (api_key_ciphertext IS NOT NULL) AS has_api_key
                """,
                (
                    site_code.upper(), vendor, backend, base_url, username,
                    encrypt(secret), encrypt(api_key), site_id_on_vendor,
                    psycopg2.extras.Json(extra or {}), created_by, now,
                    verify_ts or now, verify_ok, verify_error,
                    now,  # rotated_at on ON CONFLICT branch
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return masked_credential_view(dict(row))


def load_credential_for_adapter(site_code: str, vendor: str, backend: str) -> Optional[SiteCredential]:
    """Fetch + decrypt a credential for handoff to an adapter."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT site_code, vendor, backend, base_url, username,
                       secret_ciphertext, api_key_ciphertext,
                       site_id_on_vendor, extra
                FROM site_credentials
                WHERE site_code = %s AND vendor = %s AND backend = %s
                """,
                (site_code.upper(), vendor, backend),
            )
            row = cur.fetchone()
    if not row:
        return None
    return SiteCredential(
        site_code=row["site_code"],
        vendor=row["vendor"],
        backend=row["backend"],
        base_url=row.get("base_url"),
        username=row.get("username"),
        secret=decrypt(row.get("secret_ciphertext")),
        api_key=decrypt(row.get("api_key_ciphertext")),
        site_id_on_vendor=row.get("site_id_on_vendor"),
        extra=dict(row.get("extra") or {}),
    )


def update_credential_verify_result(
    credential_id: int,
    *,
    ok: bool,
    error: Optional[str],
    discovered_site_id: Optional[str] = None,
) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            if discovered_site_id:
                cur.execute(
                    """
                    UPDATE site_credentials SET
                        last_verified_at  = NOW(),
                        last_verified_ok  = %s,
                        last_verify_error = %s,
                        site_id_on_vendor = COALESCE(site_id_on_vendor, %s)
                    WHERE id = %s
                    """,
                    (ok, error, discovered_site_id, credential_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE site_credentials SET
                        last_verified_at  = NOW(),
                        last_verified_ok  = %s,
                        last_verify_error = %s
                    WHERE id = %s
                    """,
                    (ok, error, credential_id),
                )
        conn.commit()


# ---------------------------------------------------------------------------
# inverter_readings (latest-per-equipment used by the dashboard)
# ---------------------------------------------------------------------------

def latest_readings_for_site(site_code: str) -> List[Dict[str, Any]]:
    """Return the most recent reading per equipment at a site."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (r.equipment_id)
                    r.equipment_id, r.ts_utc, r.ac_kw, r.ac_kwh_total, r.pv_kw,
                    r.battery_kw, r.battery_soc_pct, r.grid_kw,
                    r.ac_freq_hz, r.ac_v_avg, r.status_code,
                    e.vendor, e.kind, e.model, e.serial, e.role
                FROM inverter_readings r
                JOIN site_equipment e ON e.id = r.equipment_id
                WHERE r.site_code = %s
                ORDER BY r.equipment_id, r.ts_utc DESC
                """,
                (site_code.upper(),),
            )
            return [dict(r) for r in cur.fetchall()]


def insert_readings(readings) -> int:
    """Bulk-insert LiveReading / IntervalReading rows. No-op when empty."""
    if not readings:
        return 0
    values = [
        (
            r.equipment_id,
            _resolve_site_code_for_equipment(r.equipment_id),
            r.ts_utc,
            r.ac_kw, r.ac_kwh_total, r.dc_kw, r.pv_kw,
            r.battery_kw, r.battery_soc_pct, r.grid_kw,
            r.ac_freq_hz, r.ac_v_avg, r.status_code,
            psycopg2.extras.Json(r.raw_json) if r.raw_json is not None else None,
        )
        for r in readings
    ]
    with _conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO inverter_readings (
                    equipment_id, site_code, ts_utc, ac_kw, ac_kwh_total, dc_kw,
                    pv_kw, battery_kw, battery_soc_pct, grid_kw,
                    ac_freq_hz, ac_v_avg, status_code, raw_json
                )
                VALUES %s
                """,
                values,
            )
            count = cur.rowcount
        conn.commit()
    return count or len(values)


# In-process cache keyed by equipment_id to avoid repeated lookups.
_equipment_site_cache: Dict[int, str] = {}


def _resolve_site_code_for_equipment(equipment_id: int) -> str:
    code = _equipment_site_cache.get(equipment_id)
    if code:
        return code
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT site_code FROM site_equipment WHERE id = %s",
                (equipment_id,),
            )
            row = cur.fetchone()
    if not row:
        raise ValueError(f"equipment_id {equipment_id} not found")
    _equipment_site_cache[equipment_id] = row[0]
    return row[0]
