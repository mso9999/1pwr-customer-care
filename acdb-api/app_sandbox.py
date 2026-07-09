"""Sandbox / dummy test pipeline for the mobile app (Phase 4).

Gated by env ``APP_SANDBOX=1``. When enabled:

* ``POST /api/app/sandbox/seed`` creates a dummy customer + account + meter
  and N synthetic electricity payments through the same ``transactions``
  shape used by real ingest — no real payment gateway, no real energy.
* The app-auth bridge (``POST /api/app/auth/session``) accepts a sandbox
  shortcut: when ``sandbox_enabled()`` and the request carries
  ``pin == "sandbox"``, it mints a CC customer JWT for the (seeded) dummy
  account WITHOUT proxying PIN verification to the legacy per-country API.

Notification + care endpoints behave identically against the dummy data,
so the inbox/messages flows can be exercised on an Android emulator.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("cc-api.app-sandbox")

router = APIRouter(prefix="/api/app/sandbox", tags=["app-sandbox"])

SANDBOX_DUMMY_ACCOUNT = os.environ.get("APP_SANDBOX_ACCOUNT", "0000SBX")
SANDBOX_DUMMY_PIN = "sandbox"
SANDBOX_DUMMY_METER = os.environ.get("APP_SANDBOX_METER", "SBX-TEST-0001")
SANDBOX_DUMMY_NAME = os.environ.get("APP_SANDBOX_NAME", "Sandbox Customer")
# `community` and (for accounts) `account_sequence` are NOT NULL without
# defaults on the production schema, so the seeder must supply them. Use a
# clearly non-production community tag ("SBX"); the column is a free varchar
# with no FK, so the tag need not exist in any reference table.
SANDBOX_DUMMY_COMMUNITY = os.environ.get("APP_SANDBOX_COMMUNITY", "SBX")


def sandbox_enabled() -> bool:
    return os.environ.get("APP_SANDBOX", "0").lower() in ("1", "true", "yes")


# Database names treated as production. When APP_SANDBOX is enabled against
# one of these, the seeder refuses to run unless APP_SANDBOX_ALLOW_PROD_DB=1
# is explicitly set — a guard against accidentally pointing the sandbox
# seeder at a production database.
_PROD_DB_NAMES = {
    "onepower_cc",
    "onepower_bj",
    "onepower_ls",
    "onepower_zm",
}


def _resolved_db_name() -> str:
    """Best-effort parse of the dbname from the active DATABASE_URL.

    Tries psycopg2's DSN parser (handles both URL and key=value forms) and
    falls back to urllib parsing of the URL path.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return ""
    try:
        import psycopg2.extensions  # noqa: WPS433

        return str(psycopg2.extensions.parse_dsn(url).get("dbname", "") or "")
    except Exception:  # noqa: BLE001
        from urllib.parse import urlparse

        return (urlparse(url).path or "").lstrip("/")


def _assert_not_prod_db() -> None:
    if os.environ.get("APP_SANDBOX_ALLOW_PROD_DB", "0").lower() in ("1", "true", "yes"):
        return
    name = _resolved_db_name().lower()
    if name and name in _PROD_DB_NAMES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Sandbox is enabled but DATABASE_URL points at a production "
                f"database ('{name}'). Run the sandbox against a separate "
                "sandbox database, or set APP_SANDBOX_ALLOW_PROD_DB=1 to "
                "explicitly override this guard."
            ),
        )


def _get_connection():
    from customer_api import get_connection

    return get_connection()


class SandboxSeedRequest(BaseModel):
    payments: int = 12
    amount: float = 5000.0
    rate: float | None = None


class SandboxRechargeRequest(BaseModel):
    """Single simulated recharge (Phase 1 sandbox). No payment gateway, no
    real energy — inserts one ``source='sandbox'`` payment through the same
    ``transactions`` shape as the seeder so the app's Recharge flow can be
    exercised end-to-end against synthetic data."""
    amount: float = 50.0
    rate: float | None = None


def _ensure_dummy_customer(conn) -> None:
    """Best-effort insert of a dummy customer + account row.

    Tolerates pre-existing rows and missing optional columns; the seeder
    is idempotent on the account number.
    """
    cur = conn.cursor()
    cur.execute("SELECT account_number FROM accounts WHERE account_number = %s",
                (SANDBOX_DUMMY_ACCOUNT,))
    if cur.fetchone():
        return

    # Customer row (best-effort on optional columns).
    cur.execute(
        """
        INSERT INTO customers (first_name, last_name, phone, customer_type, community)
        VALUES (%s, %s, %s, 'residential', %s)
        RETURNING id
        """,
        ("Sandbox", "Customer", "00000000", SANDBOX_DUMMY_COMMUNITY),
    )
    cust_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO accounts (account_number, customer_id, community, account_sequence)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (account_number) DO NOTHING
        """,
        (SANDBOX_DUMMY_ACCOUNT, cust_id, SANDBOX_DUMMY_COMMUNITY, 1),
    )

    # Dummy meter with a bench/test name (blocked from production overwrite
    # by meter_provisioning's naming guard).
    cur.execute(
        """
        INSERT INTO meters (meter_id, account_number, community, meter_number)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (meter_id) DO NOTHING
        """,
        (SANDBOX_DUMMY_METER, SANDBOX_DUMMY_ACCOUNT, SANDBOX_DUMMY_COMMUNITY, "SBX-TEST-BENCH"),
    )
    conn.commit()


def _seed_payments(conn, n: int, amount: float, rate: float) -> int:
    cur = conn.cursor()
    now = datetime.utcnow()
    created = 0
    for i in range(n):
        ts = now - timedelta(days=n - i)
        kwh = round(amount / rate, 2) if rate else 0.0
        balance = round(kwh * (i + 1), 2)
        try:
            cur.execute(
                """
                INSERT INTO transactions
                    (account_number, meter_id, transaction_date,
                     transaction_amount, rate_used, kwh_value,
                     is_payment, current_balance, source,
                     payment_reference, payment_category,
                     electricity_portion, fee_repayment_portion)
                VALUES (%s, %s, %s, %s, %s, %s, true, %s, 'sandbox',
                        %s, 'electricity', %s, 0)
                RETURNING id
                """,
                (
                    SANDBOX_DUMMY_ACCOUNT,
                    SANDBOX_DUMMY_METER,
                    ts,
                    amount,
                    rate,
                    kwh,
                    balance,
                    f"SBX-{ts.strftime('%Y%m%d%H%M%S')}-{i}",
                    amount,
                ),
            )
            cur.fetchone()
            created += 1
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            logger.warning("sandbox: payment insert failed: %s", e)
            # Fall back to a minimal column set.
            try:
                cur.execute(
                    """
                    INSERT INTO transactions
                        (account_number, meter_id, transaction_date,
                         transaction_amount, rate_used, kwh_value,
                         is_payment, current_balance, source)
                    VALUES (%s, %s, %s, %s, %s, %s, true, %s, 'sandbox')
                    """,
                    (SANDBOX_DUMMY_ACCOUNT, SANDBOX_DUMMY_METER, ts,
                     amount, rate, kwh, balance),
                )
                created += 1
            except Exception as e2:  # noqa: BLE001
                conn.rollback()
                logger.warning("sandbox: minimal payment insert failed: %s", e2)
    conn.commit()
    return created


@router.post("/seed")
def sandbox_seed(req: SandboxSeedRequest) -> Dict[str, Any]:
    if not sandbox_enabled():
        raise HTTPException(status_code=404, detail="Sandbox mode is not enabled")
    _assert_not_prod_db()
    n = max(1, min(req.payments, 200))
    rate = req.rate or 160.0
    amount = req.amount or 5000.0
    with _get_connection() as conn:
        _ensure_dummy_customer(conn)
        created = _seed_payments(conn, n, amount, rate)
    return {
        "status": "ok",
        "account_number": SANDBOX_DUMMY_ACCOUNT,
        "pin": SANDBOX_DUMMY_PIN,
        "meter_id": SANDBOX_DUMMY_METER,
        "name": SANDBOX_DUMMY_NAME,
        "payments_created": created,
    }


@router.post("/recharge")
def sandbox_recharge(req: SandboxRechargeRequest) -> Dict[str, Any]:
    """Simulate one recharge against the dummy sandbox account.

    Inserts a single ``source='sandbox'`` payment (same shape as the seeder)
    so the mobile app's Recharge flow can be validated without a real payment
    gateway. The dashboard / transactions balance is derived from the
    ``transactions`` table, so the credit surfaces with no extra wiring.
    Gated identically to ``/seed``: 404 when sandbox is off, refuses to run
    against a production database.
    """
    if not sandbox_enabled():
        raise HTTPException(status_code=404, detail="Sandbox mode is not enabled")
    _assert_not_prod_db()
    rate = req.rate or 160.0
    amount = req.amount or 0.0
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be greater than 0")
    with _get_connection() as conn:
        _ensure_dummy_customer(conn)
        created = _seed_payments(conn, 1, amount, rate)
    kwh = round(amount / rate, 2) if rate else 0.0
    return {
        "status": "ok",
        "account_number": SANDBOX_DUMMY_ACCOUNT,
        "meter_id": SANDBOX_DUMMY_METER,
        "amount": round(amount, 2),
        "rate": rate,
        "kwh": kwh,
        "payments_created": created,
    }


@router.get("/status")
def sandbox_status() -> Dict[str, Any]:
    return {
        "enabled": sandbox_enabled(),
        "account_number": SANDBOX_DUMMY_ACCOUNT if sandbox_enabled() else None,
        "meter_id": SANDBOX_DUMMY_METER if sandbox_enabled() else None,
    }
