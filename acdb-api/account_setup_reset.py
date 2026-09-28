"""Wipe test activity on an issued account without retiring the customer.

Keeps the person, the account number, the meter assignment, and the meter's
cumulative energy register (so the next reading is only new use). Removes
payments, meter readings, hourly use, and energy debt, then stamps connection
and readyboard debt from the country fees saved right now.

The wiped rows are stored on the mutation. Revert on the Mutations page puts
them back. Refused once the account is older than the setup window, or if it
already has an advance, financing agreement, or unmetered-service enrollment.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from country_config import COUNTRY
from middleware import effective_roles, raise_privilege_denied, require_employee
from models import CCRole, CurrentUser
from mutations import log_mutation

logger = logging.getLogger("cc-api.account-setup-reset")

router = APIRouter(prefix="/api/accounts", tags=["account-setup-reset"])

SETUP_RESET_MAX_AGE_DAYS = 21
SETUP_RESET_MAX_PAYMENTS = 40
MUTATION_TABLE = "account_setup_reset"

_EDITOR_ROLES = {CCRole.superadmin.value, CCRole.onm_team.value}
_SCOPE_ALIASES = {"BJ": "BN", "BENIN": "BN", "LESOTHO": "LS", "ZAMBIA": "ZM"}

_WIPE_TABLES = (
    "payment_verifications",
    "financial_credit_decisions",
    "energy_debt_ledger",
    "sm_credit_retry_queue",
    "relay_commands",
    "hourly_consumption",
    "meter_readings",
    "account_balance_live",
    "transactions",
)
_RESTORE_ORDER = (
    "transactions",
    "payment_verifications",
    "financial_credit_decisions",
    "hourly_consumption",
    "meter_readings",
    "relay_commands",
    "energy_debt_ledger",
    "sm_credit_retry_queue",
    "account_balance_live",
)


class SetupResetBody(BaseModel):
    confirmation: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=8, max_length=500)


def expected_confirmation(account_number: str) -> str:
    return f"RESET {account_number.strip().upper()}"


def reseed_fee_debt(
    connection_fee: float,
    readyboard_fee: float,
    *,
    acquires_readyboard: bool,
) -> dict[str, float]:
    return {
        "fee_debt_connection_remaining": round(float(connection_fee), 2),
        "fee_debt_readyboard_remaining": round(float(readyboard_fee), 2) if acquires_readyboard else 0.0,
    }


def setup_reset_refusal(
    *,
    payment_count: int,
    oldest_payment: Optional[date],
    service_connected: Optional[date],
    today: date,
    has_advance: bool,
    has_financing: bool,
    has_unmetered: bool,
) -> Optional[str]:
    """None when a setup reset is allowed."""
    if has_advance:
        return "This account has an active fee advance. Close that before a setup reset."
    if has_financing:
        return "This account has an active financing agreement. Close that before a setup reset."
    if has_unmetered:
        return "This account is on unmetered service. End that before a setup reset."
    if payment_count > SETUP_RESET_MAX_PAYMENTS:
        return (
            f"This account has {payment_count} payments. "
            f"Setup reset is limited to {SETUP_RESET_MAX_PAYMENTS}."
        )
    for label, when in (("a payment", oldest_payment), ("service connected", service_connected)):
        if when is not None and (today - when).days > SETUP_RESET_MAX_AGE_DAYS:
            return (
                f"This account has {label} from {when.isoformat()}, "
                f"more than {SETUP_RESET_MAX_AGE_DAYS} days ago. "
                "Setup reset is only for a customer still being installed."
            )
    return None


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _may_reset(user: CurrentUser) -> bool:
    roles = set(effective_roles(user))
    if CCRole.superadmin.value in roles:
        return True
    if CCRole.onm_team.value not in roles:
        return False
    scoped = {
        _SCOPE_ALIASES.get(str(code).strip().upper(), str(code).strip().upper())
        for code in (user.scope_countries or [])
    }
    return COUNTRY.code.upper() in scoped


def _require_reset_role(user: CurrentUser) -> None:
    roles = set(effective_roles(user))
    if not roles.intersection(_EDITOR_ROLES):
        raise_privilege_denied(user, _EDITOR_ROLES, "reset customer setup data")
    if not _may_reset(user):
        raise HTTPException(
            status_code=403,
            detail="You can reset setup data only for your own country.",
        )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _dump(cur, sql: str, params: tuple) -> Optional[list[dict]]:
    try:
        cur.execute("SAVEPOINT setup_reset_dump")
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        rows = [{col: _jsonable(val) for col, val in zip(cols, raw)} for raw in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT setup_reset_dump")
        return rows
    except Exception:
        logger.exception("setup reset could not read a table")
        try:
            cur.execute("ROLLBACK TO SAVEPOINT setup_reset_dump")
        except Exception:
            logger.exception("setup reset savepoint rollback failed")
        return None


def _delete_account_rows(cur, table: str, account_number: str) -> None:
    if table == "financial_credit_decisions":
        cur.execute(
            """
            DELETE FROM financial_credit_decisions
             WHERE source_transaction_id IN (
                    SELECT id FROM transactions WHERE account_number = %s
             ) OR related_transaction_id IN (
                    SELECT id FROM transactions WHERE account_number = %s
             )
            """,
            (account_number, account_number),
        )
        return
    cur.execute(f"DELETE FROM {table} WHERE account_number = %s", (account_number,))


def _exists(cur, sql: str, params: tuple) -> Optional[bool]:
    """None when the check itself failed. Callers must refuse the reset then."""
    dumped = _dump(cur, sql, params)
    if dumped is None:
        return None
    return bool(dumped)


def restore_account_setup_reset(conn, snapshot: dict) -> None:
    """Put wiped rows and the previous fee debt back. Caller commits."""
    account_number = snapshot["account_number"]
    rows_by_table = snapshot.get("rows") or {}
    cur = conn.cursor()
    for table in _RESTORE_ORDER:
        rows = rows_by_table.get(table) or []
        if not rows:
            continue
        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        for row in rows:
            cur.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                [row.get(col) for col in columns],
            )
        if "id" in columns:
            cur.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    TRUE
                )
                """
            )
    fees = snapshot.get("fees_before") or {}
    cur.execute(
        """
        UPDATE customers
           SET fee_debt_connection_remaining = %s,
               fee_debt_readyboard_remaining = %s,
               connection_fee_paid = %s,
               readyboard_fee_paid = %s,
               connection_fee_paid_date = %s,
               readyboard_fee_paid_date = %s
         WHERE id = %s
        """,
        (
            fees.get("fee_debt_connection_remaining") or 0,
            fees.get("fee_debt_readyboard_remaining") or 0,
            bool(fees.get("connection_fee_paid")),
            bool(fees.get("readyboard_fee_paid")),
            fees.get("connection_fee_paid_date"),
            fees.get("readyboard_fee_paid_date"),
            snapshot["customer_id"],
        ),
    )
    if "energy_debt_kwh" in fees:
        try:
            cur.execute("SAVEPOINT setup_reset_energy")
            cur.execute(
                "UPDATE customers SET energy_debt_kwh = %s WHERE id = %s",
                (fees.get("energy_debt_kwh") or 0, snapshot["customer_id"]),
            )
            cur.execute("RELEASE SAVEPOINT setup_reset_energy")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT setup_reset_energy")
    logger.info("setup reset restored %s", account_number)


@router.post("/{account_number}/setup-reset")
def reset_account_setup(
    account_number: str,
    body: SetupResetBody,
    user: CurrentUser = Depends(require_employee),
):
    """Clear test payments and readings. Keep the customer and account number."""
    _require_reset_role(user)
    account_number = account_number.strip().upper()
    expected = expected_confirmation(account_number)
    if body.confirmation.strip() != expected:
        raise HTTPException(status_code=400, detail=f"Type {expected} exactly.")

    from customer_api import get_connection

    with get_connection() as conn:
        def refuse(status: int, detail: str) -> None:
            conn.rollback()
            raise HTTPException(status_code=status, detail=detail)

        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.id,
                   COALESCE(c.acquires_1pwr_readyboard, FALSE),
                   c.date_service_connected,
                   c.date_service_terminated,
                   COALESCE(c.fee_debt_connection_remaining, 0),
                   COALESCE(c.fee_debt_readyboard_remaining, 0),
                   COALESCE(c.connection_fee_paid, FALSE),
                   COALESCE(c.readyboard_fee_paid, FALSE),
                   c.connection_fee_paid_date,
                   c.readyboard_fee_paid_date
              FROM accounts a
              JOIN customers c ON c.id = a.customer_id
             WHERE a.account_number = %s
             LIMIT 1
            """,
            (account_number,),
        )
        cust = cur.fetchone()
        if not cust:
            refuse(404, "Account not found")
        if cust[3]:
            refuse(409, "This customer is decommissioned. Setup reset is for an open account.")

        customer_id = int(cust[0])
        acquires_readyboard = bool(cust[1])
        energy_before = 0.0
        energy_row = _dump(
            cur,
            "SELECT COALESCE(energy_debt_kwh, 0) FROM customers WHERE id = %s",
            (customer_id,),
        )
        if energy_row:
            energy_before = float(list(energy_row[0].values())[0] or 0)

        has_advance = _exists(
            cur,
            "SELECT 1 FROM account_advances WHERE account_number = %s AND status = 'active' LIMIT 1",
            (account_number,),
        )
        has_financing = _exists(
            cur,
            "SELECT 1 FROM financing_agreements WHERE account_number = %s AND status = 'active' LIMIT 1",
            (account_number,),
        )
        has_unmetered = _exists(
            cur,
            "SELECT 1 FROM unmetered_service WHERE account_number = %s AND status = 'active' LIMIT 1",
            (account_number,),
        )
        if None in (has_advance, has_financing, has_unmetered):
            refuse(
                409,
                "Could not confirm this account has no advance, financing, or unmetered service. Reset refused.",
            )

        payments = _dump(
            cur,
            """
            SELECT COUNT(*) AS n, MIN(transaction_date) AS oldest
              FROM transactions
             WHERE account_number = %s AND is_payment
            """,
            (account_number,),
        )
        if payments is None:
            refuse(409, "Could not read payments. Reset refused so the account is left unchanged.")
        payment_count = int(payments[0]["n"]) if payments else 0
        oldest_payment = _as_date(payments[0]["oldest"]) if payments else None
        refusal = setup_reset_refusal(
            payment_count=payment_count,
            oldest_payment=oldest_payment,
            service_connected=_as_date(cust[2]),
            today=datetime.now().date(),
            has_advance=bool(has_advance),
            has_financing=bool(has_financing),
            has_unmetered=bool(has_unmetered),
        )
        if refusal:
            refuse(409, refusal)

        from country_fees import get_country_fees
        fees_now = get_country_fees(conn)
        reseed = reseed_fee_debt(
            fees_now["connection_fee_amount"],
            fees_now["readyboard_fee_amount"],
            acquires_readyboard=acquires_readyboard,
        )
        fees_before = {
            "fee_debt_connection_remaining": float(cust[4] or 0),
            "fee_debt_readyboard_remaining": float(cust[5] or 0),
            "connection_fee_paid": bool(cust[6]),
            "readyboard_fee_paid": bool(cust[7]),
            "connection_fee_paid_date": _jsonable(cust[8]),
            "readyboard_fee_paid_date": _jsonable(cust[9]),
            "energy_debt_kwh": energy_before,
        }

        snapshot_rows: dict[str, list] = {}
        try:
            for table in _WIPE_TABLES:
                if table == "financial_credit_decisions":
                    dumped = _dump(
                        cur,
                        """
                        SELECT * FROM financial_credit_decisions
                         WHERE source_transaction_id IN (
                                SELECT id FROM transactions WHERE account_number = %s
                         ) OR related_transaction_id IN (
                                SELECT id FROM transactions WHERE account_number = %s
                         )
                        """,
                        (account_number, account_number),
                    )
                else:
                    dumped = _dump(
                        cur,
                        f"SELECT * FROM {table} WHERE account_number = %s",
                        (account_number,),
                    )
                if dumped is None:
                    if table in ("transactions", "hourly_consumption", "meter_readings"):
                        raise HTTPException(
                            status_code=409,
                            detail=f"Could not read {table}. Reset refused so the account is left unchanged.",
                        )
                    continue
                snapshot_rows[table] = dumped
                if dumped or table in ("transactions", "hourly_consumption", "meter_readings"):
                    cur.execute("SAVEPOINT setup_reset_delete")
                    try:
                        _delete_account_rows(cur, table, account_number)
                        cur.execute("RELEASE SAVEPOINT setup_reset_delete")
                    except Exception:
                        cur.execute("ROLLBACK TO SAVEPOINT setup_reset_delete")
                        if table == "transactions":
                            raise

            cur.execute(
                """
                UPDATE customers
                   SET fee_debt_connection_remaining = %s,
                       fee_debt_readyboard_remaining = %s,
                       connection_fee_paid = FALSE,
                       readyboard_fee_paid = FALSE,
                       connection_fee_paid_date = NULL,
                       readyboard_fee_paid_date = NULL
                 WHERE id = %s
                """,
                (
                    reseed["fee_debt_connection_remaining"],
                    reseed["fee_debt_readyboard_remaining"],
                    customer_id,
                ),
            )
            try:
                cur.execute("SAVEPOINT setup_reset_energy")
                cur.execute(
                    "UPDATE customers SET energy_debt_kwh = 0 WHERE id = %s",
                    (customer_id,),
                )
                cur.execute("RELEASE SAVEPOINT setup_reset_energy")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT setup_reset_energy")

            snapshot = {
                "account_number": account_number,
                "customer_id": customer_id,
                "fees_before": fees_before,
                "fees_after": reseed,
                "rows": snapshot_rows,
                "kept": [
                    "customer identity",
                    "account number",
                    "meter assignment",
                    "meter energy register",
                ],
            }
            mutation_id = log_mutation(
                user,
                "delete",
                MUTATION_TABLE,
                account_number,
                old_values=snapshot,
                new_values={
                    "reason": body.reason.strip(),
                    "fees_after": reseed,
                    "wiped": {table: len(rows) for table, rows in snapshot_rows.items()},
                },
                metadata={"kind": "account_setup_reset", "country": COUNTRY.code},
                conn=conn,
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("setup reset failed for %s", account_number)
            raise HTTPException(status_code=500, detail="Setup reset failed. Nothing was changed.") from exc

    return {
        "status": "ok",
        "account_number": account_number,
        "mutation_id": mutation_id,
        "fees": reseed,
        "wiped": {table: len(rows) for table, rows in snapshot_rows.items()},
    }
