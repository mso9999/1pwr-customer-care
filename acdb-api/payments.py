"""
Payment processing endpoints for 1PDB.

Provides:
  - POST /api/payments/webhook — receive payment notifications from SMS Gateway
  - POST /api/payments/record  — manual payment recording (authenticated)
  - GET  /api/payments/{account_number} — payment history

The SMS Gateway App (onepowerLS/SMS-Gateway-APP) parses M-PESA/EcoCash
confirmation SMSes and POSTs them here. This endpoint is unauthenticated
but validated by a shared secret in the X-Gateway-Key header.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from customer_api import get_connection
from sparkmeter_credit import credit_sparkmeter, CreditResult
from balance_engine import get_balance_kwh, record_payment_kwh

logger = logging.getLogger("cc-api.payments")

router = APIRouter(prefix="/api/payments", tags=["payments"])

GATEWAY_KEY = os.environ.get("SMS_GATEWAY_KEY", "1pwr-sms-gateway-2026")


class PaymentWebhook(BaseModel):
    account_number: str
    amount: float
    meter_id: Optional[str] = None
    reference: Optional[str] = None
    phone: Optional[str] = None
    provider: Optional[str] = None  # mpesa, ecocash, manual
    timestamp: Optional[str] = None


class ManualPayment(BaseModel):
    account_number: str
    amount: float
    meter_id: Optional[str] = None
    kwh: Optional[float] = None
    note: Optional[str] = None


def _verify_gateway_key(x_gateway_key: str = Header(None)):
    if x_gateway_key != GATEWAY_KEY:
        raise HTTPException(status_code=403, detail="Invalid gateway key")


def _resolve_meter(conn, account_number: str, meter_id: Optional[str] = None) -> str:
    """Resolve meter_id for an account. Returns provided meter_id or looks up from DB."""
    if meter_id:
        return meter_id
    cur = conn.cursor()
    cur.execute(
        "SELECT meter_id FROM meters WHERE account_number = %s AND status = 'active' LIMIT 1",
        (account_number,),
    )
    row = cur.fetchone()
    return row[0] if row else ""


def _get_tariff_rate(conn, account_number: str) -> float:
    """Get the applicable tariff rate (LSL/kWh) for an account."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate' LIMIT 1")
    row = cur.fetchone()
    return float(row[0]) if row else 5.0


@router.post("/webhook")
def payment_webhook(
    payload: PaymentWebhook,
    background_tasks: BackgroundTasks,
    _=Depends(_verify_gateway_key),
):
    """Receive payment notification from the SMS Gateway.

    Records the transaction, converts currency to kWh at current tariff,
    updates the running kWh balance, credits SparkMeter in the background,
    and returns the result for the SMS confirmation reply.

    Balance is tracked in kWh:
      payment  → balance += amount / rate
      (consumption deductions happen in the import pipeline)
    """
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if not payload.account_number:
        raise HTTPException(status_code=400, detail="Account number required")

    ts = datetime.now(timezone.utc)
    if payload.timestamp:
        try:
            ts = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        with get_connection() as conn:
            meter_id = _resolve_meter(conn, payload.account_number, payload.meter_id)
            rate = _get_tariff_rate(conn, payload.account_number)

            txn_id, kwh_vended, new_balance_kwh = record_payment_kwh(
                conn, payload.account_number, meter_id,
                amount_currency=payload.amount, rate=rate,
                source="sms_gateway", timestamp=ts,
            )
            conn.commit()

            background_tasks.add_task(
                _credit_sm_and_log, payload.account_number,
                payload.amount, f"sms_gateway txn {txn_id}",
                str(txn_id),
            )

            return {
                "status": "ok",
                "transaction_id": txn_id,
                "account_number": payload.account_number,
                "amount": payload.amount,
                "kwh_vended": kwh_vended,
                "rate": rate,
                "balance_kwh": new_balance_kwh,
                "meter_id": meter_id,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payment webhook failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record")
def record_manual_payment(payload: ManualPayment):
    """Record a manual payment (e.g., from portal or field agent).

    Saves to 1PDB with kWh balance tracking and synchronously credits
    SparkMeter so the operator sees the result immediately.
    """
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    try:
        with get_connection() as conn:
            meter_id = _resolve_meter(conn, payload.account_number, payload.meter_id)
            rate = _get_tariff_rate(conn, payload.account_number)

            txn_id, kwh_vended, new_balance_kwh = record_payment_kwh(
                conn, payload.account_number, meter_id,
                amount_currency=payload.amount, rate=rate,
                kwh_override=payload.kwh,
                source="portal",
            )
            conn.commit()

            sm_result = _credit_sm_sync(
                payload.account_number, payload.amount,
                payload.note or f"portal txn {txn_id}", str(txn_id),
            )

            return {
                "status": "ok",
                "transaction_id": txn_id,
                "amount": payload.amount,
                "kwh": kwh_vended,
                "balance_kwh": new_balance_kwh,
                "sm_credit": sm_result,
            }
    except Exception as e:
        logger.error("Manual payment failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# SparkMeter credit helpers
# ---------------------------------------------------------------------------

def _credit_sm_sync(
    account_number: str, amount: float, memo: str, external_id: str,
) -> dict:
    """Credit SM synchronously and return a summary dict for the API response."""
    result = credit_sparkmeter(account_number, amount, memo, external_id)
    summary = {
        "success": result.success,
        "platform": result.platform,
    }
    if result.sm_transaction_id:
        summary["sm_transaction_id"] = result.sm_transaction_id
    if result.error:
        summary["error"] = result.error
    if not result.success:
        logger.warning(
            "SM credit failed for %s M%.2f: %s",
            account_number, amount, result.error,
        )
    else:
        logger.info(
            "SM credit OK for %s M%.2f → %s txn %s",
            account_number, amount, result.platform, result.sm_transaction_id,
        )
    return summary


def _credit_sm_and_log(
    account_number: str, amount: float, memo: str, external_id: str,
):
    """Background task: credit SM and log the outcome."""
    _credit_sm_sync(account_number, amount, memo, external_id)


@router.get("/sm-credit-status")
def sm_credit_status():
    """Diagnostic: show which SM crediting platforms are configured."""
    from sparkmeter_credit import is_configured
    return is_configured()


@router.get("/balance/{account_number}")
def get_balance(account_number: str):
    """Get the current kWh balance for an account.

    Computes: last_transaction_balance_kwh - consumption_kwh_since.
    Also returns the currency equivalent at current tariff.
    """
    try:
        with get_connection() as conn:
            balance_kwh, as_of = get_balance_kwh(conn, account_number)
            rate = _get_tariff_rate(conn, account_number)
            return {
                "account_number": account_number,
                "balance_kwh": balance_kwh,
                "balance_currency": round(balance_kwh * rate, 2),
                "tariff_rate": rate,
                "as_of": as_of.isoformat() if as_of else None,
            }
    except Exception as e:
        logger.error("Balance query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{account_number}")
def get_payment_history(
    account_number: str,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """Get payment/transaction history for an account."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, account_number, meter_id, transaction_date,
                       transaction_amount, rate_used, kwh_value,
                       is_payment, current_balance, source
                FROM transactions
                WHERE account_number = %s
                ORDER BY transaction_date DESC
                LIMIT %s OFFSET %s
            """, (account_number, limit, offset))
            columns = [d[0] for d in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]

            cur.execute(
                "SELECT count(*) FROM transactions WHERE account_number = %s",
                (account_number,),
            )
            total = cur.fetchone()[0]

            return {"transactions": rows, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error("Payment history failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
