"""
Customer Financing module for 1PWR Customer Care Portal.

Provides CRUD for financing products (templates) and agreements,
payment split logic, and contract generation for asset financing
(readyboards, refrigerators, etc.).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from models import CurrentUser
from middleware import require_employee
from customer_api import get_connection
from contract_gen import (
    CONTRACTS_DIR, TEMPLATES_DIR, _html_to_pdf, _safe_name,
    build_download_url, _STAFF_SIGNATURE_B64, STAFF_NAME,
)

import jinja2
import os

logger = logging.getLogger("cc-api.financing")

router = APIRouter(prefix="/api/financing", tags=["financing"])

_loader = jinja2.FileSystemLoader(searchpath=TEMPLATES_DIR)
_env = jinja2.Environment(loader=_loader)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProductCreate(BaseModel):
    name: str
    default_principal: float = 0
    default_interest_rate: float = 0
    default_setup_fee: float = 0
    default_repayment_fraction: float = 0.20
    default_penalty_rate: float = 0
    default_penalty_grace_days: int = 30
    default_penalty_interval_days: int = 30


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    default_principal: Optional[float] = None
    default_interest_rate: Optional[float] = None
    default_setup_fee: Optional[float] = None
    default_repayment_fraction: Optional[float] = None
    default_penalty_rate: Optional[float] = None
    default_penalty_grace_days: Optional[int] = None
    default_penalty_interval_days: Optional[int] = None
    is_active: Optional[bool] = None


class AgreementCreate(BaseModel):
    account_number: str
    product_id: Optional[int] = None
    description: str
    principal: float
    interest_amount: float = 0
    setup_fee: float = 0
    total_owed: Optional[float] = None
    repayment_fraction: float = 0.20
    penalty_rate: float = 0
    penalty_grace_days: int = 30
    penalty_interval_days: int = 30
    customer_signature_b64: Optional[str] = None


class AdjustmentCreate(BaseModel):
    entry_type: str = Field(..., pattern="^(adjustment|fee|writeoff)$")
    amount: float
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Products CRUD
# ---------------------------------------------------------------------------

@router.get("/products")
def list_products(user: CurrentUser = Depends(require_employee)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM financing_products ORDER BY name")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@router.post("/products", status_code=201)
def create_product(body: ProductCreate, user: CurrentUser = Depends(require_employee)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO financing_products
                (name, default_principal, default_interest_rate, default_setup_fee,
                 default_repayment_fraction, default_penalty_rate,
                 default_penalty_grace_days, default_penalty_interval_days)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            body.name, body.default_principal, body.default_interest_rate,
            body.default_setup_fee, body.default_repayment_fraction,
            body.default_penalty_rate, body.default_penalty_grace_days,
            body.default_penalty_interval_days,
        ))
        pid = cur.fetchone()[0]
        conn.commit()
        return {"id": pid, "name": body.name}


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate, user: CurrentUser = Depends(require_employee)):
    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    sets = ", ".join(f"{k} = %s" for k in updates)
    vals = list(updates.values())
    vals.append(product_id)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE financing_products SET {sets}, updated_at = NOW() WHERE id = %s", vals)
        if cur.rowcount == 0:
            raise HTTPException(404, "Product not found")
        conn.commit()
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------

@router.get("/agreements")
def list_agreements(
    status: Optional[str] = Query(None),
    account_number: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    clauses = []
    params: list = []
    if status:
        clauses.append("a.status = %s")
        params.append(status)
    if account_number:
        clauses.append("a.account_number = %s")
        params.append(account_number)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT a.*, p.name AS product_name
            FROM financing_agreements a
            LEFT JOIN financing_products p ON a.product_id = p.id
            {where}
            ORDER BY a.created_at DESC
        """, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@router.post("/agreements", status_code=201)
def create_agreement(body: AgreementCreate, user: CurrentUser = Depends(require_employee)):
    total = body.total_owed if body.total_owed is not None else (
        body.principal + body.interest_amount + body.setup_fee
    )

    with get_connection() as conn:
        cur = conn.cursor()

        # Resolve customer_id from account
        cur.execute(
            "SELECT customer_id FROM accounts WHERE account_number = %s",
            (body.account_number,),
        )
        row = cur.fetchone()
        customer_id = row[0] if row else None

        cur.execute("""
            INSERT INTO financing_agreements
                (customer_id, account_number, product_id, description,
                 principal, interest_amount, setup_fee, total_owed, outstanding_balance,
                 repayment_fraction, penalty_rate, penalty_grace_days,
                 penalty_interval_days, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            customer_id, body.account_number, body.product_id, body.description,
            body.principal, body.interest_amount, body.setup_fee,
            total, total,
            body.repayment_fraction, body.penalty_rate,
            body.penalty_grace_days, body.penalty_interval_days,
            user.user_id,
        ))
        agreement_id = cur.fetchone()[0]

        # Initial ledger entry for the financed amount
        cur.execute("""
            INSERT INTO financing_ledger
                (agreement_id, entry_type, amount, balance_after, note, created_by)
            VALUES (%s, 'fee', %s, %s, %s, %s)
        """, (
            agreement_id, -total, total,
            f"Initial financing: {body.description}", user.user_id,
        ))

        contract_result = None
        if body.customer_signature_b64:
            contract_result = _generate_financing_contract(
                conn, agreement_id, body, customer_id, total,
                body.customer_signature_b64, user.user_id,
            )
            if contract_result:
                cur.execute(
                    "UPDATE financing_agreements SET contract_path = %s WHERE id = %s",
                    (contract_result.get("en_path", ""), agreement_id),
                )

        conn.commit()

        result = {
            "id": agreement_id,
            "account_number": body.account_number,
            "total_owed": float(total),
            "outstanding_balance": float(total),
        }
        if contract_result:
            result["contracts"] = contract_result
        return result


@router.get("/agreements/{agreement_id}")
def get_agreement(agreement_id: int, user: CurrentUser = Depends(require_employee)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.*, p.name AS product_name
            FROM financing_agreements a
            LEFT JOIN financing_products p ON a.product_id = p.id
            WHERE a.id = %s
        """, (agreement_id,))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Agreement not found")
        agreement = dict(zip(cols, row))

        cur.execute("""
            SELECT * FROM financing_ledger
            WHERE agreement_id = %s
            ORDER BY created_at
        """, (agreement_id,))
        lcols = [d[0] for d in cur.description]
        agreement["ledger"] = [dict(zip(lcols, r)) for r in cur.fetchall()]

        return agreement


@router.post("/agreements/{agreement_id}/adjust")
def adjust_agreement(
    agreement_id: int, body: AdjustmentCreate,
    user: CurrentUser = Depends(require_employee),
):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT outstanding_balance, status FROM financing_agreements WHERE id = %s",
            (agreement_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Agreement not found")

        balance = float(row[0])
        new_balance = round(balance - body.amount, 2)
        if new_balance < 0:
            new_balance = 0

        cur.execute("""
            INSERT INTO financing_ledger
                (agreement_id, entry_type, amount, balance_after, note, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            agreement_id, body.entry_type, body.amount,
            new_balance, body.note, user.user_id,
        ))

        new_status = "paid_off" if new_balance <= 0 else row[1]
        paid_off_clause = ", paid_off_at = NOW()" if new_balance <= 0 else ""
        cur.execute(f"""
            UPDATE financing_agreements
            SET outstanding_balance = %s, status = %s {paid_off_clause}
            WHERE id = %s
        """, (new_balance, new_status, agreement_id))

        conn.commit()
        return {"outstanding_balance": new_balance, "status": new_status}


@router.get("/customer/{account_number}")
def customer_financing_summary(
    account_number: str,
    user: CurrentUser = Depends(require_employee),
):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.*, p.name AS product_name
            FROM financing_agreements a
            LEFT JOIN financing_products p ON a.product_id = p.id
            WHERE a.account_number = %s
            ORDER BY a.created_at DESC
        """, (account_number,))
        cols = [d[0] for d in cur.description]
        agreements = [dict(zip(cols, row)) for row in cur.fetchall()]

        total_debt = sum(float(a["outstanding_balance"]) for a in agreements if a["status"] == "active")
        active_count = sum(1 for a in agreements if a["status"] == "active")

        return {
            "account_number": account_number,
            "total_outstanding": round(total_debt, 2),
            "active_agreements": active_count,
            "agreements": agreements,
        }


# ---------------------------------------------------------------------------
# Payment split logic (called from payments.py)
# ---------------------------------------------------------------------------

def compute_financing_split(conn, account_number: str, amount: float) -> Dict[str, Any]:
    """Determine how a payment should be split between financing and electricity.

    Returns dict with:
        has_financing: bool
        debt_portion: float (amount applied to debt)
        electricity_portion: float (amount for electricity/SM credit)
        agreement_id: int or None
        is_dedicated_payment: bool (True if amount ends in 1 or 9)
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, outstanding_balance, repayment_fraction
        FROM financing_agreements
        WHERE account_number = %s AND status = 'active'
        ORDER BY created_at ASC
        LIMIT 1
    """, (account_number,))
    row = cur.fetchone()

    if not row:
        return {
            "has_financing": False,
            "debt_portion": 0,
            "electricity_portion": amount,
            "agreement_id": None,
            "is_dedicated_payment": False,
        }

    agreement_id, outstanding, fraction = row[0], float(row[1]), float(row[2])
    ones_digit = int(amount) % 10
    is_dedicated = ones_digit in (1, 9)

    if is_dedicated:
        debt_portion = min(amount, outstanding)
        electricity_portion = amount - debt_portion
    else:
        debt_portion = min(round(amount * fraction, 2), outstanding)
        electricity_portion = round(amount - debt_portion, 2)

    return {
        "has_financing": True,
        "debt_portion": debt_portion,
        "electricity_portion": electricity_portion,
        "agreement_id": agreement_id,
        "is_dedicated_payment": is_dedicated,
    }


def apply_financing_payment(
    conn, agreement_id: int, amount: float,
    source_transaction_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> float:
    """Apply a payment to a financing agreement. Returns new outstanding balance."""
    cur = conn.cursor()
    cur.execute(
        "SELECT outstanding_balance FROM financing_agreements WHERE id = %s FOR UPDATE",
        (agreement_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0

    balance = float(row[0])
    new_balance = round(max(balance - amount, 0), 2)

    cur.execute("""
        INSERT INTO financing_ledger
            (agreement_id, entry_type, amount, balance_after,
             source_transaction_id, note, created_by)
        VALUES (%s, 'payment', %s, %s, %s, %s, %s)
    """, (
        agreement_id, amount, new_balance,
        source_transaction_id,
        f"Payment of {amount:.2f} applied to debt",
        created_by,
    ))

    if new_balance <= 0:
        cur.execute("""
            UPDATE financing_agreements
            SET outstanding_balance = 0, status = 'paid_off', paid_off_at = NOW()
            WHERE id = %s
        """, (agreement_id,))
    else:
        cur.execute(
            "UPDATE financing_agreements SET outstanding_balance = %s WHERE id = %s",
            (new_balance, agreement_id),
        )

    return new_balance


# ---------------------------------------------------------------------------
# Contract generation for financing agreements
# ---------------------------------------------------------------------------

def _generate_financing_contract(
    conn, agreement_id: int, body: AgreementCreate,
    customer_id: Optional[int], total_owed: float,
    signature_b64: str, employee_id: str,
) -> Optional[Dict]:
    """Generate a bilingual financing agreement PDF."""
    cur = conn.cursor()

    # Look up customer info
    customer_info = {}
    if customer_id:
        cur.execute(
            "SELECT first_name, last_name, national_id, cell_phone_1 FROM customers WHERE id = %s",
            (customer_id,),
        )
        row = cur.fetchone()
        if row:
            customer_info = {
                "first_name": row[0] or "",
                "last_name": row[1] or "",
                "national_id": row[2] or "",
                "phone": row[3] or "",
            }

    # Resolve site code from account
    site_code = body.account_number[-3:].upper() if len(body.account_number) >= 3 else "UNK"

    monthly_estimate = 0
    if body.repayment_fraction > 0:
        monthly_estimate = round(total_owed / max(body.repayment_fraction * 30, 1), 2)

    data = {
        "agreement_id": agreement_id,
        "account_number": body.account_number,
        "first_name": customer_info.get("first_name", ""),
        "last_name": customer_info.get("last_name", ""),
        "national_id": customer_info.get("national_id", ""),
        "phone": customer_info.get("phone", ""),
        "description": body.description,
        "principal": f"{body.principal:.2f}",
        "interest_amount": f"{body.interest_amount:.2f}",
        "setup_fee": f"{body.setup_fee:.2f}",
        "total_owed": f"{total_owed:.2f}",
        "repayment_fraction": f"{body.repayment_fraction * 100:.0f}",
        "penalty_rate": f"{body.penalty_rate * 100:.1f}",
        "penalty_grace_days": body.penalty_grace_days,
        "customer_signature": signature_b64,
        "staff_signature": _STAFF_SIGNATURE_B64,
        "staff_name": STAFF_NAME,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    try:
        tmpl_en = _env.get_template("template_financing_en.html")
    except jinja2.TemplateNotFound:
        logger.warning("Financing contract template not found, skipping PDF generation")
        return None

    html_en = tmpl_en.render(data=data)

    safe_last = _safe_name(customer_info.get("last_name", "customer"))
    safe_first = _safe_name(customer_info.get("first_name", ""))
    en_filename = f"{body.account_number}_{safe_last}_{safe_first}_Financing_{agreement_id}_en.pdf"
    so_filename = f"{body.account_number}_{safe_last}_{safe_first}_Financing_{agreement_id}_so.pdf"

    site_dir = os.path.join(CONTRACTS_DIR, site_code)
    os.makedirs(site_dir, exist_ok=True)
    en_path = os.path.join(site_dir, en_filename)
    _html_to_pdf(html_en, en_path)

    result: Dict[str, str] = {
        "en_filename": en_filename,
        "en_path": en_path,
        "en_url": build_download_url(site_code, en_filename),
    }

    try:
        tmpl_so = _env.get_template("template_financing_so.html")
        html_so = tmpl_so.render(data=data)
        so_path = os.path.join(site_dir, so_filename)
        _html_to_pdf(html_so, so_path)
        result["so_filename"] = so_filename
        result["so_path"] = so_path
        result["so_url"] = build_download_url(site_code, so_filename)
    except jinja2.TemplateNotFound:
        pass

    logger.info("Generated financing contract(s): %s", en_path)
    return result
