"""
Mobile app BFF (Backend-For-Frontend) endpoints.

Public, unauthenticated routes consumed by the 1PWR mobile app
(``1PWRBENIN-v2`` / ``mionwa``), plus JWT-authenticated customer routes
(dashboard / transactions / fees). Scoped under ``/api/app/*`` to keep the
mobile-facing contract visually separate from the CC web portal and
operational endpoints.

Contract is documented in ``1PWR CC/docs/app-bff-contract.md``.
"""

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel

from app_packs import build_pack, supported_codes
from app_sandbox import sandbox_enabled as _sandbox_enabled
from country_config import COUNTRY, _REGISTRY

logger = logging.getLogger("cc-api.app-bff")

router = APIRouter(prefix="/api/app", tags=["app-bff"])


# Base URL used to build per-code ``appConfigUrl`` values advertised in
# ``/api/app/active-countries``. Override per code in ``_REMOTE_CONFIG_URLS``
# when a pack is hosted elsewhere. Empty string => omit ``appConfigUrl`` for
# that code (app falls back to its bundled asset).
_DEFAULT_CONFIG_URL_TEMPLATE = "https://cc.1pwrafrica.com/api/app/country-config/{code}"

# Registry of remote ``CountryConfig`` packs the mobile app should load
# instead of its bundled JSON asset for that country code. Codes absent
# from this map fall back to ``_DEFAULT_CONFIG_URL_TEMPLATE`` when they
# have a pack in :mod:`app_packs`; set a code to ``""`` to suppress the
# URL (e.g. while staging a pack).
_REMOTE_CONFIG_URLS: Dict[str, Optional[str]] = {}


def _config_url_for(code: str) -> Optional[str]:
    """Resolve the ``appConfigUrl`` advertised for *code*, or None to omit."""
    if code in _REMOTE_CONFIG_URLS:
        url = _REMOTE_CONFIG_URLS[code]
        return url or None
    if code in supported_codes():
        return _DEFAULT_CONFIG_URL_TEMPLATE.format(code=code)
    return None


def _row_for(code: str, cfg: Any) -> Dict[str, Any]:
    """Shape one country row to match `CountryRegistryClient` in the app."""
    row: Dict[str, Any] = {
        "countryCode": code,
        "displayName": getattr(cfg, "display_name", None) or cfg.name,
        "active": True,
    }
    url: Optional[str] = _config_url_for(code)
    if url:
        row["appConfigUrl"] = url
    return row


@router.get("/active-countries")
def active_countries(response: Response) -> Dict[str, List[Dict[str, Any]]]:
    """Return the list of countries the mobile app may select.

    Filters out rows where ``CountryConfig.active`` is False. The registry
    is static Python (see :mod:`country_config`) so the response only
    changes on deploy — cache aggressively. Each active row that has a
    remote config pack advertises it via ``appConfigUrl``.
    """
    rows: List[Dict[str, Any]] = []
    for code, cfg in _REGISTRY.items():
        if not getattr(cfg, "active", True):
            continue
        rows.append(_row_for(code, cfg))

    response.headers["Cache-Control"] = "public, max-age=300"
    return {"countries": rows}


@router.get("/country-config/{code}")
def country_config(response: Response, code: str) -> Dict[str, Any]:
    """Return the Flutter-shaped ``CountryConfig`` pack for *code*.

    The pack is a superset of ``1PWRBENIN-v2/assets/config/country_*.json``.
    Live fee / tariff values are read from ``system_config`` (editable via
    ``/api/admin/country-fees``) so they change without an app build. The
    response is cacheable for 5 minutes — the same TTL as the country list.
    """
    conn = None
    try:
        from customer_api import get_connection

        with get_connection() as db_conn:
            pack = build_pack(code, conn=db_conn)
    except Exception:  # noqa: BLE001 - DB optional; fall back to static pack
        logger.debug("country-config/%s: DB unavailable, using static pack", code, exc_info=True)
        pack = build_pack(code)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"No app config pack for country '{code}'")
    response.headers["Cache-Control"] = "public, max-age=300"
    return pack


# ---------------------------------------------------------------------------
# Auth bridge + authenticated customer routes
# ---------------------------------------------------------------------------
#
# PIN / ``check-client`` are not implemented in CC — they live in the
# per-country legacy API (``apiBaseUrl`` in the app pack). The auth bridge
# proxies PIN verification to that legacy API, then mints a CC customer JWT
# so the app can call the dashboard / transactions / fees endpoints here.
# Dashboard logic is reused verbatim from ``crud.my_dashboard``; transaction
# + fee endpoints reuse the same tables/columns as the employee routes.


class AppAuthRequest(BaseModel):
    client_code: str
    pin: str


def _legacy_api_base_url(code: str) -> Optional[str]:
    """Legacy per-country API base for PIN/check-client proxying."""
    pack = build_pack(code)
    return pack.get("apiBaseUrl") if pack else None


def _proxy_json_post(url: str, body: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    """POST JSON to a legacy endpoint and return the decoded JSON response."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            payload = {}
        raise HTTPException(status_code=502, detail=f"Legacy API error {e.code}: {payload}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Legacy API unreachable: {e.reason}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Legacy API call failed: {e}")


@router.post("/auth/session")
def app_auth_session(req: AppAuthRequest) -> Dict[str, Any]:
    """Exchange a verified client_code + PIN for a CC customer JWT.

    Proxies ``POST {apiBaseUrl}/pin/verify`` to the active country's legacy
    API (the same store the app uses today). On success, normalises the
    client code to a CC account number, validates it exists in 1PDB, and
    mints a short-lived customer JWT (``create_token``) scoped to that
    account. The app persists the token and sends it as a Bearer header to
    the dashboard / transactions / fees routes below.
    """
    from middleware import create_token
    from auth import normalize_account_number, _validate_account_exists

    code = (req.client_code or "").strip()
    pin = (req.pin or "").strip()
    if not code or not pin:
        raise HTTPException(status_code=400, detail="client_code and pin are required")

    # Phase 4 sandbox shortcut: when APP_SANDBOX=1 and the caller presents
    # the sandbox PIN, mint a JWT for the (seeded) dummy account without
    # proxying PIN verification to the legacy per-country API. Lets the app
    # log in on an emulator against synthetic data.
    from auth import normalize_account_number

    if _sandbox_enabled() and pin == "sandbox":
        acct = normalize_account_number(code)
        token, expires_in = create_token(
            user_type="customer",
            user_id=acct,
            role="customer",
            name="Sandbox Customer",
            email="",
        )
        return {
            "access_token": token,
            "expires_in": expires_in,
            "client": {"code": acct, "name": "Sandbox Customer"},
            "sandbox": True,
        }

    base = _legacy_api_base_url(COUNTRY.code) or "https://app.onepowerbenin.com/api"
    verify = _proxy_json_post(f"{base}/pin/verify", {"client_code": code, "pin": pin})
    if not verify.get("success"):
        raise HTTPException(status_code=401, detail="Invalid client code or PIN")

    acct = normalize_account_number(code)
    name = acct
    try:
        from auth import _validate_account_exists
        info = _validate_account_exists(acct)
        name = info.get("name") or acct
    except HTTPException:
        # Account may not yet exist in 1PDB (e.g. newly onboarded). We still
        # mint a JWT scoped to the normalised account so the app can retry
        # dashboard calls once ingest backfills; the token is the source of
        # identity, not a 1PDB row.
        logger.info("app-auth: account %s not found in 1PDB yet; minting token anyway", acct)

    token, expires_in = create_token(
        user_type="customer",
        user_id=acct,
        role="customer",
        name=name,
        email="",
    )
    return {
        "access_token": token,
        "expires_in": expires_in,
        "client": {"code": acct, "name": name},
    }


def _customer_user(user) -> Any:
    """Return the customer JWT user, enforcing customer type."""
    from models import UserType

    if user.user_type != UserType.customer:
        raise HTTPException(status_code=403, detail="Customer endpoint only")
    return user


def _require_customer_dep():
    """Return the customer-only dependency (resolved lazily to avoid import cycles)."""
    from middleware import get_current_user

    def _dep(user=Depends(get_current_user)):
        return _customer_user(user)

    return _dep


@router.get("/dashboard")
def app_dashboard(response: Response, user=Depends(_require_customer_dep())) -> Dict[str, Any]:
    """Customer dashboard (balance, consumption, charts, meters).

    Reuses ``crud.my_dashboard`` verbatim so the app and web portal share
    one source of truth. Adds a ``fee_debt`` + ``financing`` snapshot so the
    app can render the fees/debt panel without a second round-trip.
    """
    from crud import my_dashboard

    user = _customer_user(user)
    dashboard = my_dashboard(user)
    # Augment with fee debt + financing snapshot for the app's fees panel.
    dashboard.setdefault("fee_debt", _fee_debt_snapshot(user.user_id))
    dashboard.setdefault("financing", _financing_snapshot(user.user_id))
    response.headers["Cache-Control"] = "no-store"
    return dashboard


@router.get("/transactions")
def app_transactions(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Paginated transaction ledger with fee/advance/financing/electricity split.

    Mirrors the query used by the employee ``customer-data`` route but
    scoped to the JWT account. Split columns are read when present and
    fall back to zeros on older schemas.
    """
    from customer_api import get_connection
    from country_config import UTC_OFFSET_HOURS

    user = _customer_user(user)
    acct = user.user_id
    offset_delta = timedelta(hours=UTC_OFFSET_HOURS)

    base_sel = (
        "SELECT id, account_number, meter_id, transaction_date, "
        "transaction_amount, rate_used, kwh_value, is_payment, current_balance"
    )
    split_cols = ", fee_repayment_portion, advance_portion, financing_portion, electricity_portion"
    ref_col = ", payment_reference"
    order = " FROM transactions WHERE account_number = %s ORDER BY transaction_date DESC LIMIT %s OFFSET %s"

    with get_connection() as conn:
        cur = conn.cursor()
        include_splits = True
        include_ref = True
        try:
            cur.execute(base_sel + split_cols + ref_col + order, (acct, limit, offset))
            rows = list(cur.fetchall())
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            err = str(e).lower()
            if "does not exist" in err:
                try:
                    cur.execute(base_sel + ref_col + order, (acct, limit, offset))
                    rows = list(cur.fetchall())
                    include_splits = False
                except Exception:  # noqa: BLE001
                    conn.rollback()
                    cur.execute(base_sel + order, (acct, limit, offset))
                    rows = list(cur.fetchall())
                    include_splits = False
                    include_ref = False
            else:
                logger.warning("app/transactions: query failed: %s", e)
                rows = []

        cur.execute("SELECT count(*) FROM transactions WHERE account_number = %s", (acct,))
        total = int(cur.fetchone()[0] or 0)

    items: List[Dict[str, Any]] = []
    for r in rows:
        dt_raw = r[3]
        dt_str = None
        if dt_raw is not None and hasattr(dt_raw, "strftime"):
            local_dt = dt_raw + offset_delta if not (hasattr(dt_raw, "tzinfo") and dt_raw.tzinfo) else dt_raw.replace(tzinfo=None) + offset_delta
            dt_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        elif dt_raw is not None:
            dt_str = str(dt_raw)

        item = {
            "id": r[0],
            "account": r[1],
            "meter": r[2],
            "date": dt_str,
            "amount": round(float(r[4] or 0), 2),
            "rate": round(float(r[5] or 0), 2),
            "kwh": round(float(r[6] or 0), 2),
            "is_payment": bool(r[7]),
            "balance": round(float(r[8] or 0), 2) if r[8] is not None else None,
        }
        if include_splits:
            item["fee_repayment_portion"] = round(float(r[9] or 0), 2)
            item["advance_portion"] = round(float(r[10] or 0), 2)
            item["financing_portion"] = round(float(r[11] or 0), 2)
            item["electricity_portion"] = round(float(r[12] or 0), 2)
            item["payment_reference"] = str(r[13]).strip() if r[13] else None
        else:
            item["fee_repayment_portion"] = 0.0
            item["advance_portion"] = 0.0
            item["financing_portion"] = 0.0
            item["electricity_portion"] = None
            item["payment_reference"] = None
        items.append(item)

    response.headers["Cache-Control"] = "no-store"
    return {"account": acct, "total": total, "limit": limit, "offset": offset, "transactions": items}


@router.get("/fees")
def app_fees(response: Response, user=Depends(_require_customer_dep())) -> Dict[str, Any]:
    """Fee schedule + current debt + split policy for the signed-in customer.

    Combines the country fee amounts (``country_fees.get_country_fees``),
    the customer's remaining fee debt (``fee_debt.fetch_fee_debts``), and
    the financing summary, plus the split policy explainer the app renders
    ("half to energy, half to debt").
    """
    from customer_api import get_connection
    from country_fees import get_country_fees
    from fee_debt import get_customer_id_for_account, fetch_fee_debts

    user = _customer_user(user)
    acct = user.user_id
    with get_connection() as conn:
        schedule = get_country_fees(conn)
        cid = get_customer_id_for_account(conn, acct)
        debts = fetch_fee_debts(conn, cid) if cid else None
        financing = _financing_snapshot(acct, conn=conn)

    fee_debt = {
        "connection_remaining": round(float(debts["fee_debt_connection_remaining"]), 2) if debts else 0.0,
        "readyboard_remaining": round(float(debts["fee_debt_readyboard_remaining"]), 2) if debts else 0.0,
        "total_remaining": round(
            (float(debts["fee_debt_connection_remaining"]) + float(debts["fee_debt_readyboard_remaining"]))
            if debts else 0.0,
            2,
        ),
        "commissioned": bool(debts["customer_commissioned"]) if debts else False,
    }

    response.headers["Cache-Control"] = "no-store"
    return {
        "account": acct,
        "currency": schedule.get("currency", COUNTRY.currency),
        "tariff_rate": schedule.get("tariff_rate", COUNTRY.default_tariff_rate),
        "schedule": {
            "connection_fee": schedule.get("connection_fee_amount", 0.0),
            "readyboard_fee": schedule.get("readyboard_fee_amount", 0.0),
            "low_balance_kwh_threshold": schedule.get("low_balance_kwh_threshold"),
            "low_balance_kwh_clear": schedule.get("low_balance_kwh_clear"),
        },
        "fee_debt": fee_debt,
        "financing": financing,
        # Split policy explainer for the app UI.
        "split_policy": {
            "fee_cap_fraction": 0.5,
            "description": "Up to half of each electricity payment goes to fee debt "
            "(connection first, then readyboard); the remainder buys energy. "
            "Financing is taken from the electricity slice when an active agreement exists.",
            "dedicated_payment_rule": "Payments ending in 1 or 9 are treated as dedicated financing repayments (100% to debt).",
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fee_debt_snapshot(account_number: str, conn=None) -> Dict[str, Any]:
    from fee_debt import get_customer_id_for_account, fetch_fee_debts

    def _compute(c) -> Dict[str, Any]:
        cid = get_customer_id_for_account(c, account_number)
        if not cid:
            return {"connection_remaining": 0.0, "readyboard_remaining": 0.0, "total_remaining": 0.0}
        d = fetch_fee_debts(c, cid)
        conn_rem = float(d["fee_debt_connection_remaining"])
        ready_rem = float(d["fee_debt_readyboard_remaining"])
        return {
            "connection_remaining": round(conn_rem, 2),
            "readyboard_remaining": round(ready_rem, 2),
            "total_remaining": round(conn_rem + ready_rem, 2),
            "commissioned": bool(d["customer_commissioned"]),
        }

    if conn is not None:
        return _compute(conn)
    from customer_api import get_connection

    with get_connection() as c:
        return _compute(c)


def _financing_snapshot(account_number: str, conn=None) -> Dict[str, Any]:
    """Active financing outstanding for the account (0 / empty when table absent)."""
    from financing import _financing_tables_exist

    def _compute(c) -> Dict[str, Any]:
        cur = c.cursor()
        if not _financing_tables_exist(cur):
            return {"has_financing": False, "total_outstanding": 0.0, "active_agreements": 0}
        cur.execute(
            "SELECT outstanding_balance, repayment_fraction "
            "FROM financing_agreements WHERE account_number = %s AND status = 'active' "
            "ORDER BY created_at ASC",
            (account_number,),
        )
        rows = list(cur.fetchall())
        total = sum(float(r[0] or 0) for r in rows)
        fraction = float(rows[0][1] or 0) if rows else 0.0
        return {
            "has_financing": bool(rows),
            "total_outstanding": round(total, 2),
            "active_agreements": len(rows),
            "repayment_fraction": round(fraction, 2),
        }

    if conn is not None:
        return _compute(conn)
    from customer_api import get_connection

    with get_connection() as c:
        return _compute(c)


# ---------------------------------------------------------------------------
# Care messaging + tickets (Phase 2)
# ---------------------------------------------------------------------------


def _ensure_care_table(conn) -> None:
    """Ensure ``app_care_messages`` exists and has the OM ticket / status columns."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_care_messages (
            id              BIGSERIAL PRIMARY KEY,
            account_number  TEXT,
            body_text       TEXT NOT NULL,
            category        TEXT,
            source          TEXT NOT NULL DEFAULT 'app',
            device_id       TEXT,
            idempotency_key TEXT UNIQUE,
            om_ticket_ref   TEXT,
            status          TEXT NOT NULL DEFAULT 'sent',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    for col, ddl in (
        ("om_ticket_ref", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'sent'"),
        ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ):
        cur.execute(f"ALTER TABLE app_care_messages ADD COLUMN IF NOT EXISTS {col} {ddl}")
    conn.commit()


class AppCareMessageCreate(BaseModel):
    text: str
    category: Optional[str] = None
    device_id: Optional[str] = None


@router.get("/care/threads")
def app_care_threads(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """List the signed-in customer's care messages as threads."""
    from customer_api import get_connection

    user = _customer_user(user)
    acct = user.user_id

    with get_connection() as conn:
        _ensure_care_table(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, body_text, category, source, status, om_ticket_ref, created_at "
            "FROM app_care_messages WHERE account_number = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (acct, limit, offset),
        )
        rows = list(cur.fetchall())
        cur.execute(
            "SELECT count(*) FROM app_care_messages WHERE account_number = %s",
            (acct,),
        )
        total = int(cur.fetchone()[0] or 0)

    threads = []
    for r in rows:
        created = r[6]
        threads.append(
            {
                "id": r[0],
                "text": r[1],
                "category": r[2],
                "source": r[3],
                "status": r[4] or "sent",
                "om_ticket_ref": r[5],
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            }
        )
    response.headers["Cache-Control"] = "no-store"
    return {"threads": threads, "total": total, "limit": limit, "offset": offset}


@router.get("/care/threads/{thread_id}/messages")
def app_care_thread_messages(
    thread_id: int,
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Return the messages for a single care thread (today: the inbound message)."""
    from customer_api import get_connection

    user = _customer_user(user)
    acct = user.user_id

    with get_connection() as conn:
        _ensure_care_table(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, body_text, category, source, status, om_ticket_ref, created_at "
            "FROM app_care_messages WHERE id = %s AND account_number = %s",
            (thread_id, acct),
        )
        r = cur.fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Thread not found")

    created = r[6]
    return {
        "thread": {
            "id": r[0],
            "text": r[1],
            "category": r[2],
            "source": r[3],
            "status": r[4] or "sent",
            "om_ticket_ref": r[5],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
        },
        "messages": [
            {
                "id": r[0],
                "direction": "outbound",
                "text": r[1],
                "status": r[4] or "sent",
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            }
        ],
    }


@router.post("/care/messages")
def app_care_create_message(
    body: AppCareMessageCreate,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    user=Depends(_require_customer_dep()),
) -> Dict[str, Any]:
    """Create a care message from the signed-in customer and WA-bridge it.

    Thin JWT-gated wrapper over the existing ``app_care_messages`` table +
    ``notify_cc_bridge`` flow used by ``POST /api/customer/messages``.
    """
    from cc_bridge_notify import notify_cc_bridge
    from country_config import COUNTRY
    from customer_api import get_connection

    user = _customer_user(user)
    acct = user.user_id
    idem = (x_idempotency_key or "").strip() or None

    with get_connection() as conn:
        _ensure_care_table(conn)
        cur = conn.cursor()
        if idem:
            cur.execute(
                "SELECT id FROM app_care_messages WHERE idempotency_key = %s",
                (idem,),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return {"status": "ok", "duplicate": True, "id": int(row[0])}

        cur.execute(
            """
            INSERT INTO app_care_messages
                (account_number, body_text, category, source, device_id, idempotency_key, status)
            VALUES (%s, %s, %s, 'app', %s, %s, 'sent')
            RETURNING id
            """,
            (acct, body.text, body.category, body.device_id, idem),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()

    try:
        notify_cc_bridge(
            {
                "id": new_id,
                "account_number": acct,
                "text": body.text,
                "category": body.category,
                "source": "app",
            },
            country_code=COUNTRY.code,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("app/care/messages: bridge notify failed: %s", e)

    return {"status": "ok", "id": new_id, "duplicate": False}
