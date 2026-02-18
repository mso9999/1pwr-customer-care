"""
Tariff management for the 1PWR Customer Care Portal.

Provides:
  - Hierarchical rate resolution: customer override > concession override > global
  - CRUD endpoints for setting/removing overrides at each level
  - Append-only tariff history (time series) for auditing
  - Future effective-date support (overrides stored but not active until date)

Tariff overrides live in the SQLite auth DB (cc_tariff_overrides / cc_tariff_history).
The global rate is synced to system_config (key='tariff_rate') in PostgreSQL.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db_auth import get_auth_db
from models import CCRole, CurrentUser
from middleware import require_employee
from mutations import log_mutation

logger = logging.getLogger("acdb-api.tariff")

router = APIRouter(prefix="/api/tariff", tags=["tariff"])

# Roles allowed to manage tariffs
_TARIFF_ROLES = {CCRole.superadmin.value, CCRole.onm_team.value, CCRole.finance_team.value}


def _require_tariff_role(user: CurrentUser):
    if user.role not in _TARIFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Tariff management requires superadmin, onm_team, or finance_team role",
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class TariffUpdateRequest(BaseModel):
    rate_lsl: float = Field(..., gt=0, description="Rate in LSL per kWh")
    effective_from: Optional[str] = Field(
        None, description="ISO datetime when rate takes effect (default: now)"
    )
    notes: Optional[str] = Field("", description="Optional notes about this change")


# ---------------------------------------------------------------------------
# Rate resolution helper  (importable by other modules)
# ---------------------------------------------------------------------------

def resolve_rate(
    cursor=None,
    customer_id: str = None,
    concession: str = None,
) -> Dict[str, Any]:
    """Resolve the effective tariff rate for a customer.

    Cascade:
      1. Customer-level override  (if set and effective)
      2. Concession-level override (if set and effective)
      3. Global rate from system_config (key='tariff_rate')

    Returns dict: {rate_lsl, source, source_key, effective_from}
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Derive concession from customer if needed
    if customer_id and not concession and cursor:
        try:
            cursor.execute(
                "SELECT community FROM customers WHERE customer_id_legacy = %s",
                (str(customer_id),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                concession = str(row[0]).strip().upper()
        except Exception:
            pass

    with get_auth_db() as conn:
        # 1. Customer override
        if customer_id:
            row = conn.execute(
                "SELECT rate_lsl, effective_from FROM cc_tariff_overrides "
                "WHERE scope = 'customer' AND scope_key = ? AND effective_from <= ?",
                (str(customer_id), now_iso),
            ).fetchone()
            if row:
                return {
                    "rate_lsl": row["rate_lsl"],
                    "source": "customer",
                    "source_key": str(customer_id),
                    "effective_from": row["effective_from"],
                }

        # 2. Concession override
        if concession:
            row = conn.execute(
                "SELECT rate_lsl, effective_from FROM cc_tariff_overrides "
                "WHERE scope = 'concession' AND scope_key = ? AND effective_from <= ?",
                (concession.upper(), now_iso),
            ).fetchone()
            if row:
                return {
                    "rate_lsl": row["rate_lsl"],
                    "source": "concession",
                    "source_key": concession.upper(),
                    "effective_from": row["effective_from"],
                }

    # 3. Global rate from system_config
    global_rate = 5.0  # fallback
    if cursor:
        try:
            cursor.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
            row = cursor.fetchone()
            if row and row[0] is not None:
                global_rate = float(row[0])
        except Exception:
            pass
    else:
        try:
            from customer_api import get_connection
            with get_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
                row = c.fetchone()
                if row and row[0] is not None:
                    global_rate = float(row[0])
        except Exception:
            pass

    return {
        "rate_lsl": global_rate,
        "source": "global",
        "source_key": "",
        "effective_from": "",
    }


# ---------------------------------------------------------------------------
# GET /api/tariff/current
# ---------------------------------------------------------------------------

@router.get("/current")
def get_current_tariffs(user: CurrentUser = Depends(require_employee)):
    """Return the global rate, all concession overrides, and customer override count."""
    from customer_api import get_connection

    # Global rate from PostgreSQL
    global_rate = 5.0
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
            row = c.fetchone()
            if row and row[0] is not None:
                global_rate = float(row[0])
    except Exception as e:
        logger.warning("Failed to read global rate: %s", e)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_auth_db() as conn:
        # Concession overrides
        concession_rows = conn.execute(
            "SELECT scope_key, rate_lsl, effective_from, set_by, set_by_name, set_at, notes "
            "FROM cc_tariff_overrides WHERE scope = 'concession' ORDER BY scope_key"
        ).fetchall()
        concession_overrides = []
        for r in concession_rows:
            entry = dict(r)
            entry["pending"] = entry["effective_from"] > now_iso
            concession_overrides.append(entry)

        # Customer override count
        cust_count = conn.execute(
            "SELECT COUNT(*) FROM cc_tariff_overrides WHERE scope = 'customer'"
        ).fetchone()[0]

        # Customer overrides list
        cust_rows = conn.execute(
            "SELECT scope_key, rate_lsl, effective_from, set_by, set_by_name, set_at, notes "
            "FROM cc_tariff_overrides WHERE scope = 'customer' ORDER BY scope_key"
        ).fetchall()
        customer_overrides = []
        for r in cust_rows:
            entry = dict(r)
            entry["pending"] = entry["effective_from"] > now_iso
            customer_overrides.append(entry)

        # Check for pending global rate
        pending_global = conn.execute(
            "SELECT rate_lsl, effective_from, notes FROM cc_tariff_history "
            "WHERE scope = 'global' AND effective_from > ? ORDER BY effective_from ASC LIMIT 1",
            (now_iso,),
        ).fetchone()

    return {
        "global_rate": global_rate,
        "pending_global": dict(pending_global) if pending_global else None,
        "concession_overrides": concession_overrides,
        "customer_overrides": customer_overrides,
        "customer_override_count": cust_count,
    }


# ---------------------------------------------------------------------------
# GET /api/tariff/resolve/{identifier}
# ---------------------------------------------------------------------------

@router.get("/resolve/{identifier}")
def resolve_customer_rate(
    identifier: str,
    user: CurrentUser = Depends(require_employee),
):
    """Resolve the effective rate for a customer ID or account number."""
    from customer_api import get_connection, _resolve_accounts_for_customer

    with get_connection() as conn:
        cursor = conn.cursor()

        cust_id = identifier.strip()
        concession = None

        # If numeric, treat as customer ID
        if cust_id.isdigit():
            try:
                cursor.execute(
                    "SELECT community FROM customers WHERE customer_id_legacy = %s",
                    (cust_id,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    concession = str(row[0]).strip().upper()
            except Exception:
                pass
        else:
            # Account number -> resolve to customer ID via meters
            try:
                cursor.execute(
                    "SELECT customer_id_legacy FROM meters WHERE account_number = %s",
                    (cust_id,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    cust_id = str(row[0]).strip()
            except Exception:
                pass

            if cust_id and cust_id.isdigit():
                try:
                    cursor.execute(
                        "SELECT community FROM customers WHERE customer_id_legacy = %s",
                        (cust_id,),
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        concession = str(row[0]).strip().upper()
                except Exception:
                    pass

        result = resolve_rate(cursor, customer_id=cust_id, concession=concession)
        result["customer_id"] = cust_id
        result["concession"] = concession

        # Also show the full cascade for context
        cascade = []

        # Check customer override
        with get_auth_db() as auth_conn:
            if cust_id:
                cr = auth_conn.execute(
                    "SELECT rate_lsl, effective_from FROM cc_tariff_overrides "
                    "WHERE scope = 'customer' AND scope_key = ?",
                    (cust_id,),
                ).fetchone()
                if cr:
                    cascade.append({
                        "level": "customer",
                        "key": cust_id,
                        "rate_lsl": cr["rate_lsl"],
                        "effective_from": cr["effective_from"],
                    })

            if concession:
                cr = auth_conn.execute(
                    "SELECT rate_lsl, effective_from FROM cc_tariff_overrides "
                    "WHERE scope = 'concession' AND scope_key = ?",
                    (concession,),
                ).fetchone()
                if cr:
                    cascade.append({
                        "level": "concession",
                        "key": concession,
                        "rate_lsl": cr["rate_lsl"],
                        "effective_from": cr["effective_from"],
                    })

        cascade.append({
            "level": "global",
            "key": "",
            "rate_lsl": resolve_rate(cursor)["rate_lsl"],
            "effective_from": "",
        })

        result["cascade"] = cascade
        return result


# ---------------------------------------------------------------------------
# PUT /api/tariff/global
# ---------------------------------------------------------------------------

@router.put("/global")
def update_global_rate(
    req: TariffUpdateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Update the global tariff rate (system_config key='tariff_rate')."""
    _require_tariff_role(user)

    from customer_api import get_connection

    eff = req.effective_from or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Read current global rate
    old_rate = 5.0
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
            row = c.fetchone()
            if row and row[0] is not None:
                old_rate = float(row[0])
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    is_future = eff > now_iso

    # Update PostgreSQL if effective now
    if not is_future:
        try:
            with get_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "UPDATE system_config SET value = %s, updated_at = NOW() WHERE key = 'tariff_rate'",
                    (req.rate_lsl,),
                )
                conn.commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update system_config: {e}")

    # Log to tariff history
    with get_auth_db() as conn:
        conn.execute(
            "INSERT INTO cc_tariff_history "
            "(scope, scope_key, rate_lsl, previous_rate, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("global", "", req.rate_lsl, old_rate, eff,
             user.user_id, user.name or user.user_id, req.notes or ""),
        )

    # Also log to cc_mutations for unified audit trail
    log_mutation(
        user, "update", "system_config", "tariff_rate",
        old_values={"value": old_rate},
        new_values={"value": req.rate_lsl, "effective_from": eff},
    )

    status = "scheduled" if is_future else "applied"
    return {
        "status": status,
        "rate_lsl": req.rate_lsl,
        "previous_rate": old_rate,
        "effective_from": eff,
    }


# ---------------------------------------------------------------------------
# PUT /api/tariff/concession/{code}
# ---------------------------------------------------------------------------

@router.put("/concession/{code}")
def update_concession_rate(
    code: str,
    req: TariffUpdateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Set or update tariff override for a concession."""
    _require_tariff_role(user)

    code = code.strip().upper()
    eff = req.effective_from or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Get previous rate
    previous_rate = None
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT rate_lsl FROM cc_tariff_overrides WHERE scope = 'concession' AND scope_key = ?",
            (code,),
        ).fetchone()
        if row:
            previous_rate = row["rate_lsl"]

        # Upsert override
        conn.execute(
            "INSERT INTO cc_tariff_overrides (scope, scope_key, rate_lsl, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope, scope_key) DO UPDATE SET "
            "rate_lsl = excluded.rate_lsl, effective_from = excluded.effective_from, "
            "set_by = excluded.set_by, set_by_name = excluded.set_by_name, "
            "set_at = datetime('now'), notes = excluded.notes",
            ("concession", code, req.rate_lsl, eff,
             user.user_id, user.name or user.user_id, req.notes or ""),
        )

        # Log to history
        conn.execute(
            "INSERT INTO cc_tariff_history "
            "(scope, scope_key, rate_lsl, previous_rate, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("concession", code, req.rate_lsl, previous_rate, eff,
             user.user_id, user.name or user.user_id, req.notes or ""),
        )

    log_mutation(
        user, "update", "cc_tariff_overrides", f"concession:{code}",
        old_values={"rate_lsl": previous_rate} if previous_rate else None,
        new_values={"rate_lsl": req.rate_lsl, "effective_from": eff, "scope": "concession", "scope_key": code},
    )

    return {
        "status": "set",
        "scope": "concession",
        "scope_key": code,
        "rate_lsl": req.rate_lsl,
        "previous_rate": previous_rate,
        "effective_from": eff,
    }


# ---------------------------------------------------------------------------
# PUT /api/tariff/customer/{customer_id}
# ---------------------------------------------------------------------------

@router.put("/customer/{customer_id}")
def update_customer_rate(
    customer_id: str,
    req: TariffUpdateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Set or update tariff override for a single customer."""
    _require_tariff_role(user)

    cid = customer_id.strip()
    eff = req.effective_from or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    previous_rate = None
    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT rate_lsl FROM cc_tariff_overrides WHERE scope = 'customer' AND scope_key = ?",
            (cid,),
        ).fetchone()
        if row:
            previous_rate = row["rate_lsl"]

        conn.execute(
            "INSERT INTO cc_tariff_overrides (scope, scope_key, rate_lsl, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope, scope_key) DO UPDATE SET "
            "rate_lsl = excluded.rate_lsl, effective_from = excluded.effective_from, "
            "set_by = excluded.set_by, set_by_name = excluded.set_by_name, "
            "set_at = datetime('now'), notes = excluded.notes",
            ("customer", cid, req.rate_lsl, eff,
             user.user_id, user.name or user.user_id, req.notes or ""),
        )

        conn.execute(
            "INSERT INTO cc_tariff_history "
            "(scope, scope_key, rate_lsl, previous_rate, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("customer", cid, req.rate_lsl, previous_rate, eff,
             user.user_id, user.name or user.user_id, req.notes or ""),
        )

    log_mutation(
        user, "update", "cc_tariff_overrides", f"customer:{cid}",
        old_values={"rate_lsl": previous_rate} if previous_rate else None,
        new_values={"rate_lsl": req.rate_lsl, "effective_from": eff, "scope": "customer", "scope_key": cid},
    )

    return {
        "status": "set",
        "scope": "customer",
        "scope_key": cid,
        "rate_lsl": req.rate_lsl,
        "previous_rate": previous_rate,
        "effective_from": eff,
    }


# ---------------------------------------------------------------------------
# DELETE /api/tariff/concession/{code}
# ---------------------------------------------------------------------------

@router.delete("/concession/{code}")
def delete_concession_override(
    code: str,
    user: CurrentUser = Depends(require_employee),
):
    """Remove a concession tariff override (reverts to global rate)."""
    _require_tariff_role(user)

    code = code.strip().upper()

    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT rate_lsl FROM cc_tariff_overrides WHERE scope = 'concession' AND scope_key = ?",
            (code,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No override for concession {code}")

        old_rate = row["rate_lsl"]
        conn.execute(
            "DELETE FROM cc_tariff_overrides WHERE scope = 'concession' AND scope_key = ?",
            (code,),
        )

        conn.execute(
            "INSERT INTO cc_tariff_history "
            "(scope, scope_key, rate_lsl, previous_rate, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("concession", code, 0, old_rate,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             user.user_id, user.name or user.user_id, "Override removed"),
        )

    log_mutation(
        user, "delete", "cc_tariff_overrides", f"concession:{code}",
        old_values={"rate_lsl": old_rate, "scope": "concession", "scope_key": code},
    )

    return {"status": "removed", "scope": "concession", "scope_key": code, "previous_rate": old_rate}


# ---------------------------------------------------------------------------
# DELETE /api/tariff/customer/{customer_id}
# ---------------------------------------------------------------------------

@router.delete("/customer/{customer_id}")
def delete_customer_override(
    customer_id: str,
    user: CurrentUser = Depends(require_employee),
):
    """Remove a customer tariff override (reverts to concession/global)."""
    _require_tariff_role(user)

    cid = customer_id.strip()

    with get_auth_db() as conn:
        row = conn.execute(
            "SELECT rate_lsl FROM cc_tariff_overrides WHERE scope = 'customer' AND scope_key = ?",
            (cid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No override for customer {cid}")

        old_rate = row["rate_lsl"]
        conn.execute(
            "DELETE FROM cc_tariff_overrides WHERE scope = 'customer' AND scope_key = ?",
            (cid,),
        )

        conn.execute(
            "INSERT INTO cc_tariff_history "
            "(scope, scope_key, rate_lsl, previous_rate, effective_from, set_by, set_by_name, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("customer", cid, 0, old_rate,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             user.user_id, user.name or user.user_id, "Override removed"),
        )

    log_mutation(
        user, "delete", "cc_tariff_overrides", f"customer:{cid}",
        old_values={"rate_lsl": old_rate, "scope": "customer", "scope_key": cid},
    )

    return {"status": "removed", "scope": "customer", "scope_key": cid, "previous_rate": old_rate}


# ---------------------------------------------------------------------------
# GET /api/tariff/history
# ---------------------------------------------------------------------------

@router.get("/history")
def get_tariff_history(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    scope: Optional[str] = Query(None, description="Filter: global, concession, customer"),
    key: Optional[str] = Query(None, description="Filter by scope_key"),
    date_from: Optional[str] = Query(None, alias="from", description="Start date (ISO)"),
    date_to: Optional[str] = Query(None, alias="to", description="End date (ISO)"),
    user: CurrentUser = Depends(require_employee),
):
    """Paginated tariff change history with optional filters."""
    where_parts = []
    params = []

    if scope:
        where_parts.append("scope = ?")
        params.append(scope)
    if key:
        where_parts.append("scope_key = ?")
        params.append(key)
    if date_from:
        where_parts.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("timestamp <= ?")
        params.append(date_to)

    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

    with get_auth_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM cc_tariff_history{where_sql}", params
        ).fetchone()[0]

        offset = (page - 1) * limit
        rows = conn.execute(
            f"SELECT * FROM cc_tariff_history{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return {
            "history": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, math.ceil(total / limit)),
        }
