"""
Connection / readyboard advances ledger.

When a customer cannot pay the full connection fee (or readyboard fee) up
front, finance / O&M can record a currency-denominated *advance* against
the account. Subsequent payments are split (default 50/50) between the
advance outstanding balance and kWh credit until the advance is paid off.
A monthly fee % can be assessed on the outstanding by an authorised user
(see ``scripts/ops/accrue_advance_fees.py``).

Every advance MUST have a signed contract uploaded at creation time --
this is enforced both at the API (``contract`` is a required ``UploadFile``)
and at the database (``contract_path NOT NULL``).

Endpoints (all under /api/advances):
    POST   /api/advances                   multipart/form-data, creates advance
                                           + uploads contract (gated)
    GET    /api/advances                   list advances (filterable)
    GET    /api/advances/{id}              detail + ledger
    PATCH  /api/advances/{id}              edit monthly_fee_pct / repayment_fraction / note (gated)
    POST   /api/advances/{id}/contract     replace contract (gated)
    GET    /api/advances/{id}/contract     download contract (any employee)
    POST   /api/advances/{id}/writeoff     mark written off (gated)

Helper exported for payment ingestion paths:
    apply_advance_payment(conn, advance_id, amount, source_txn_id, created_by)
        → new_outstanding
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from contract_gen import CONTRACTS_DIR
from country_config import COUNTRY
from customer_api import get_connection
from middleware import require_employee
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.advances")

router = APIRouter(prefix="/api/advances", tags=["advances"])


# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

_ADMIN_ROLES = {
    CCRole.superadmin.value,
    CCRole.onm_team.value,
    CCRole.finance_team.value,
}

_ALLOWED_TYPES = {"connection", "readyboard"}

_CONTRACT_MAX_BYTES = int(os.environ.get("ADVANCE_CONTRACT_MAX_BYTES", 10 * 1024 * 1024))
_CONTRACT_ALLOWED_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
}
_EXT_FOR_MIME = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
}

_SITE_CODE_RE = re.compile(r"([A-Z]{3})$")


def _require_admin(user: CurrentUser) -> None:
    if user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Advance management requires superadmin, onm_team, or finance_team",
        )


def _site_for_account(account_number: str) -> str:
    """Derive the site code (last 3 chars) from the account number, e.g. 0045MAK → MAK."""
    m = _SITE_CODE_RE.search(account_number.strip().upper())
    return m.group(1) if m else "UNK"


# ---------------------------------------------------------------------------
# Contract storage
# ---------------------------------------------------------------------------


def _advance_contract_dir(site_code: str) -> str:
    path = os.path.join(CONTRACTS_DIR, site_code.upper(), "advances")
    os.makedirs(path, exist_ok=True)
    return path


def _archive_contract_dir(site_code: str) -> str:
    path = os.path.join(CONTRACTS_DIR, site_code.upper(), "advances", "archive")
    os.makedirs(path, exist_ok=True)
    return path


async def _persist_contract(
    file: UploadFile,
    *,
    account_number: str,
    advance_type: str,
) -> dict:
    """Read the upload, validate, hash and write to disk.

    Returns a dict with the metadata we persist on ``account_advances``.
    Raises HTTPException on validation failure.
    """
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="contract file is required")

    content_type = (file.content_type or "").lower().strip()
    if content_type not in _CONTRACT_ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported contract type {content_type!r}; "
                f"allowed: {', '.join(sorted(_CONTRACT_ALLOWED_MIME))}"
            ),
        )

    body = await file.read()
    size = len(body)
    if size == 0:
        raise HTTPException(status_code=400, detail="contract file is empty")
    if size > _CONTRACT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"contract too large ({size} bytes; max {_CONTRACT_MAX_BYTES})",
        )

    site_code = _site_for_account(account_number)
    sha256 = hashlib.sha256(body).hexdigest()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ext = _EXT_FOR_MIME.get(content_type, "bin")
    fname = f"advance_{account_number}_{advance_type}_{ts}.{ext}"

    dirpath = _advance_contract_dir(site_code)
    fpath = os.path.join(dirpath, fname)

    with open(fpath, "wb") as f:
        f.write(body)

    return {
        "contract_path": fpath,
        "contract_filename": fname,
        "contract_content_type": content_type,
        "contract_size_bytes": size,
        "contract_sha256": sha256,
    }


def _archive_existing_contract(current_path: Optional[str]) -> Optional[str]:
    """Move the previously-stored contract into the ``archive/`` subfolder."""
    if not current_path or not os.path.isfile(current_path):
        return None
    site_dir = os.path.dirname(current_path)  # .../<SITE>/advances
    site_code = os.path.basename(os.path.dirname(site_dir))
    archive_dir = _archive_contract_dir(site_code)
    archived = os.path.join(
        archive_dir,
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.path.basename(current_path)}",
    )
    try:
        shutil.move(current_path, archived)
        return archived
    except OSError as exc:
        logger.warning("Failed to archive %s: %s", current_path, exc)
        return None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AdvancePatch(BaseModel):
    monthly_fee_pct: Optional[float] = Field(None, ge=0, lt=1)
    repayment_fraction: Optional[float] = Field(None, ge=0, le=1)
    note: Optional[str] = None


class WriteoffRequest(BaseModel):
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Core helper used by payment ingestion (payments.py + ingest.py)
# ---------------------------------------------------------------------------


def get_active_advance(conn, account_number: str) -> Optional[dict]:
    """Return the active advance row for an account (one or none)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, account_number, advance_type,
               original_amount, outstanding, currency,
               repayment_fraction, monthly_fee_pct, status
        FROM account_advances
        WHERE account_number = %s AND status = 'active'
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (account_number,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "account_number": row[1],
        "advance_type": row[2],
        "original_amount": float(row[3]),
        "outstanding": float(row[4]),
        "currency": row[5],
        "repayment_fraction": float(row[6]),
        "monthly_fee_pct": float(row[7]),
        "status": row[8],
    }


def compute_advance_split(advance: dict, amount: float) -> dict:
    """Compute the (advance, electricity) split for an electricity payment.

    The advance portion is capped at the outstanding balance so we never
    over-collect; the electricity portion is whatever is left.
    """
    if not advance:
        return {
            "advance_portion": 0.0,
            "electricity_portion": round(amount, 2),
            "advance_id": None,
        }
    fraction = float(advance.get("repayment_fraction") or 0)
    outstanding = float(advance.get("outstanding") or 0)
    raw = round(amount * fraction, 2)
    advance_portion = round(min(raw, outstanding), 2)
    electricity_portion = round(amount - advance_portion, 2)
    return {
        "advance_portion": advance_portion,
        "electricity_portion": electricity_portion,
        "advance_id": int(advance["id"]),
    }


def apply_advance_payment(
    conn,
    advance_id: int,
    amount: float,
    source_transaction_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> float:
    """Decrement an advance's outstanding balance and write a ledger row.

    Returns the new outstanding balance. Marks the advance ``paid_off`` once
    outstanding hits zero. Does NOT commit -- the caller (payments.py /
    ingest.py) commits after the full transaction body succeeds.
    """
    if amount <= 0:
        return 0.0

    cur = conn.cursor()
    cur.execute(
        "SELECT outstanding FROM account_advances WHERE id = %s FOR UPDATE",
        (advance_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0.0

    outstanding = float(row[0])
    repay = round(min(amount, outstanding), 2)
    new_outstanding = round(outstanding - repay, 2)

    cur.execute(
        """
        INSERT INTO account_advance_ledger
            (advance_id, entry_type, amount, balance_after,
             source_transaction_id, created_by, note)
        VALUES (%s, 'repayment', %s, %s, %s, %s, %s)
        """,
        (
            advance_id, repay, new_outstanding,
            source_transaction_id, created_by,
            f"Repayment of {repay:.2f} from txn {source_transaction_id or '-'}",
        ),
    )

    if new_outstanding <= 0:
        cur.execute(
            """
            UPDATE account_advances
               SET outstanding = 0,
                   status = 'paid_off',
                   paid_off_at = NOW()
             WHERE id = %s
            """,
            (advance_id,),
        )
    else:
        cur.execute(
            "UPDATE account_advances SET outstanding = %s WHERE id = %s",
            (new_outstanding, advance_id),
        )

    return new_outstanding


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _serialise_advance(row: tuple, columns: list[str]) -> dict:
    out = {col: row[i] for i, col in enumerate(columns)}
    for k in (
        "original_amount", "outstanding", "repayment_fraction", "monthly_fee_pct",
    ):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for k in ("contract_size_bytes",):
        if out.get(k) is not None:
            out[k] = int(out[k])
    if out.get("contract_path"):
        # Don't leak server filesystem paths to the UI.
        out["contract_url"] = f"/api/advances/{out['id']}/contract"
        del out["contract_path"]
    return out


_ADVANCE_COLS = [
    "id", "account_number", "advance_type",
    "original_amount", "outstanding", "currency",
    "repayment_fraction", "monthly_fee_pct", "status",
    "created_by", "created_at", "last_accrual_at",
    "paid_off_at", "note",
    "contract_path", "contract_filename", "contract_content_type",
    "contract_size_bytes", "contract_sha256",
    "contract_uploaded_by", "contract_uploaded_at",
]
_ADVANCE_COL_SQL = ", ".join(_ADVANCE_COLS)


@router.get("")
def list_advances(
    account_number: Optional[str] = Query(None),
    status: Optional[str] = Query(None, pattern="^(active|paid_off|written_off)$"),
    advance_type: Optional[str] = Query(None, pattern="^(connection|readyboard)$"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_employee),
):
    clauses_aa: list[str] = []
    clauses_count: list[str] = []
    params: list = []
    if account_number:
        clauses_aa.append("aa.account_number = %s")
        clauses_count.append("account_number = %s")
        params.append(account_number)
    if status:
        clauses_aa.append("aa.status = %s::advance_status_enum")
        clauses_count.append("status = %s::advance_status_enum")
        params.append(status)
    if advance_type:
        clauses_aa.append("aa.advance_type = %s::advance_type_enum")
        clauses_count.append("advance_type = %s::advance_type_enum")
        params.append(advance_type)
    where_join = ("WHERE " + " AND ".join(clauses_aa)) if clauses_aa else ""
    where_count = ("WHERE " + " AND ".join(clauses_count)) if clauses_count else ""

    select_cols = ", ".join(f"aa.{c}" for c in _ADVANCE_COLS)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {select_cols}, c.first_name, c.last_name
              FROM account_advances aa
              LEFT JOIN accounts a  ON a.account_number = aa.account_number
              LEFT JOIN customers c ON c.id = a.customer_id
              {where_join}
              ORDER BY aa.created_at DESC
              LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()
        out_cols = _ADVANCE_COLS + ["first_name", "last_name"]
        result = [_serialise_advance(r, out_cols) for r in rows]

        cur.execute(
            f"SELECT COUNT(*) FROM account_advances {where_count}",
            params,
        )
        total = cur.fetchone()[0]

        return {"advances": result, "total": int(total), "limit": limit, "offset": offset}


@router.get("/{advance_id}")
def get_advance(advance_id: int, user: CurrentUser = Depends(require_employee)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {", ".join(f"aa.{c}" for c in _ADVANCE_COLS)},
                   c.first_name, c.last_name
              FROM account_advances aa
              LEFT JOIN accounts a  ON a.account_number = aa.account_number
              LEFT JOIN customers c ON c.id = a.customer_id
             WHERE aa.id = %s
            """,
            (advance_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Advance not found")
        out_cols = _ADVANCE_COLS + ["first_name", "last_name"]
        advance = _serialise_advance(row, out_cols)

        cur.execute(
            """
            SELECT id, entry_type, amount, balance_after,
                   source_transaction_id, accrual_period,
                   created_by, created_at, note
            FROM account_advance_ledger
            WHERE advance_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (advance_id,),
        )
        ledger_cols = [
            "id", "entry_type", "amount", "balance_after",
            "source_transaction_id", "accrual_period",
            "created_by", "created_at", "note",
        ]
        ledger = []
        for lr in cur.fetchall():
            entry = dict(zip(ledger_cols, lr))
            entry["amount"] = float(entry["amount"])
            entry["balance_after"] = float(entry["balance_after"])
            ledger.append(entry)
        advance["ledger"] = ledger
        return advance


@router.post("", status_code=201)
async def create_advance(
    account_number: str = Form(...),
    advance_type: str = Form(...),
    original_amount: float = Form(..., gt=0),
    monthly_fee_pct: float = Form(0.0, ge=0, lt=1),
    repayment_fraction: float = Form(0.5, ge=0, le=1),
    note: Optional[str] = Form(None),
    contract: UploadFile = File(...),
    user: CurrentUser = Depends(require_employee),
):
    """Create a new advance + upload the signed contract.

    Multipart form-data only. The contract file is required and persisted
    under ``acdb-api/contracts/<SITE>/advances/`` with a sha256 hash for
    tamper-evidence.
    """
    _require_admin(user)

    advance_type = advance_type.strip().lower()
    if advance_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"advance_type must be one of {sorted(_ALLOWED_TYPES)}",
        )

    contract_meta = await _persist_contract(
        contract,
        account_number=account_number,
        advance_type=advance_type,
    )

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            cur.execute(
                "SELECT 1 FROM accounts WHERE account_number = %s LIMIT 1",
                (account_number,),
            )
            if cur.fetchone() is None:
                raise HTTPException(404, f"Unknown account {account_number}")

            cur.execute(
                """
                SELECT id FROM account_advances
                WHERE account_number = %s
                  AND advance_type = %s::advance_type_enum
                  AND status = 'active'
                LIMIT 1
                """,
                (account_number, advance_type),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"An active {advance_type} advance already exists for "
                        f"account {account_number}; pay it off or write it off "
                        f"before creating another."
                    ),
                )

            cur.execute(
                """
                INSERT INTO account_advances
                    (account_number, advance_type, original_amount, outstanding,
                     currency, repayment_fraction, monthly_fee_pct,
                     status, created_by, note,
                     contract_path, contract_filename, contract_content_type,
                     contract_size_bytes, contract_sha256, contract_uploaded_by)
                VALUES (%s, %s::advance_type_enum, %s, %s,
                        %s, %s, %s,
                        'active', %s, %s,
                        %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    account_number, advance_type, original_amount, original_amount,
                    COUNTRY.currency, repayment_fraction, monthly_fee_pct,
                    user.user_id, (note or "").strip() or None,
                    contract_meta["contract_path"],
                    contract_meta["contract_filename"],
                    contract_meta["contract_content_type"],
                    contract_meta["contract_size_bytes"],
                    contract_meta["contract_sha256"],
                    user.user_id,
                ),
            )
            advance_id = int(cur.fetchone()[0])

            cur.execute(
                """
                INSERT INTO account_advance_ledger
                    (advance_id, entry_type, amount, balance_after,
                     created_by, note)
                VALUES (%s, 'grant', %s, %s, %s, %s)
                """,
                (
                    advance_id, original_amount, original_amount,
                    user.user_id,
                    f"Advance granted ({advance_type}) -- contract {contract_meta['contract_filename']}",
                ),
            )

            try_log_mutation(
                user, "create", "account_advances", str(advance_id),
                new_values={
                    "account_number": account_number,
                    "advance_type": advance_type,
                    "original_amount": original_amount,
                    "monthly_fee_pct": monthly_fee_pct,
                    "repayment_fraction": repayment_fraction,
                    "currency": COUNTRY.currency,
                    "contract_filename": contract_meta["contract_filename"],
                    "contract_sha256": contract_meta["contract_sha256"],
                },
                metadata={"endpoint": "POST /api/advances"},
                conn=conn,
            )
            conn.commit()

        return {"id": advance_id, "status": "ok", "contract": contract_meta["contract_filename"]}
    except HTTPException:
        if os.path.isfile(contract_meta["contract_path"]):
            try:
                os.remove(contract_meta["contract_path"])
            except OSError:
                pass
        raise
    except Exception as exc:
        logger.exception("Advance creation failed: %s", exc)
        if os.path.isfile(contract_meta["contract_path"]):
            try:
                os.remove(contract_meta["contract_path"])
            except OSError:
                pass
        raise HTTPException(500, str(exc))


@router.patch("/{advance_id}")
def patch_advance(
    advance_id: int,
    body: AdvancePatch,
    user: CurrentUser = Depends(require_employee),
):
    _require_admin(user)
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No fields to update")

    set_clauses: list[str] = []
    params: list = []
    for k, v in fields.items():
        set_clauses.append(f"{k} = %s")
        params.append(v)
    params.append(advance_id)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_ADVANCE_COL_SQL} FROM account_advances WHERE id = %s",
            (advance_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Advance not found")
        old = _serialise_advance(row, _ADVANCE_COLS)

        cur.execute(
            f"UPDATE account_advances SET {', '.join(set_clauses)} WHERE id = %s",
            params,
        )

        if "monthly_fee_pct" in fields or "repayment_fraction" in fields or fields.get("note"):
            cur.execute(
                """
                INSERT INTO account_advance_ledger
                    (advance_id, entry_type, amount, balance_after,
                     created_by, note)
                VALUES (%s, 'adjustment', 0, %s, %s, %s)
                """,
                (
                    advance_id, old["outstanding"], user.user_id,
                    f"Settings updated: {', '.join(fields.keys())}",
                ),
            )

        try_log_mutation(
            user, "update", "account_advances", str(advance_id),
            old_values={k: old.get(k) for k in fields.keys()},
            new_values=fields,
            metadata={"endpoint": "PATCH /api/advances/{id}"},
            conn=conn,
        )
        conn.commit()

    return {"status": "ok", "id": advance_id, **fields}


@router.post("/{advance_id}/contract")
async def replace_contract(
    advance_id: int,
    contract: UploadFile = File(...),
    user: CurrentUser = Depends(require_employee),
):
    _require_admin(user)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT account_number, advance_type, contract_path FROM account_advances WHERE id = %s",
            (advance_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Advance not found")
        account_number, advance_type, current_path = row[0], row[1], row[2]

    contract_meta = await _persist_contract(
        contract,
        account_number=account_number,
        advance_type=advance_type,
    )

    archived = _archive_existing_contract(current_path)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE account_advances
               SET contract_path         = %s,
                   contract_filename     = %s,
                   contract_content_type = %s,
                   contract_size_bytes   = %s,
                   contract_sha256       = %s,
                   contract_uploaded_by  = %s,
                   contract_uploaded_at  = NOW()
             WHERE id = %s
            """,
            (
                contract_meta["contract_path"],
                contract_meta["contract_filename"],
                contract_meta["contract_content_type"],
                contract_meta["contract_size_bytes"],
                contract_meta["contract_sha256"],
                user.user_id,
                advance_id,
            ),
        )

        cur.execute(
            "SELECT outstanding FROM account_advances WHERE id = %s",
            (advance_id,),
        )
        outstanding = float(cur.fetchone()[0])

        cur.execute(
            """
            INSERT INTO account_advance_ledger
                (advance_id, entry_type, amount, balance_after,
                 created_by, note)
            VALUES (%s, 'contract_replaced', 0, %s, %s, %s)
            """,
            (
                advance_id, outstanding, user.user_id,
                f"Contract replaced -- new sha256={contract_meta['contract_sha256'][:12]}…"
                + (f" old archived to {os.path.basename(archived)}" if archived else ""),
            ),
        )

        try_log_mutation(
            user, "update", "account_advances", str(advance_id),
            new_values={
                "contract_filename": contract_meta["contract_filename"],
                "contract_sha256": contract_meta["contract_sha256"],
            },
            metadata={"endpoint": "POST /api/advances/{id}/contract"},
            conn=conn,
        )
        conn.commit()

    return {"status": "ok", "id": advance_id, "contract": contract_meta["contract_filename"]}


@router.get("/{advance_id}/contract")
def download_contract(
    advance_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Authenticated download of the signed contract for an advance."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT contract_path, contract_filename, contract_content_type "
            "FROM account_advances WHERE id = %s",
            (advance_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Advance not found")
    path, fname, content_type = row[0], row[1], row[2] or "application/octet-stream"
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Contract file missing on disk")
    return FileResponse(
        path,
        media_type=content_type,
        filename=fname,
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@router.post("/{advance_id}/writeoff")
def writeoff_advance(
    advance_id: int,
    body: WriteoffRequest,
    user: CurrentUser = Depends(require_employee),
):
    _require_admin(user)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT outstanding, status FROM account_advances WHERE id = %s FOR UPDATE",
            (advance_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Advance not found")
        outstanding, status = float(row[0]), row[1]
        if status != "active":
            raise HTTPException(409, f"Advance status is '{status}' (must be 'active' to write off)")

        cur.execute(
            """
            UPDATE account_advances
               SET outstanding = 0, status = 'written_off', paid_off_at = NOW()
             WHERE id = %s
            """,
            (advance_id,),
        )
        cur.execute(
            """
            INSERT INTO account_advance_ledger
                (advance_id, entry_type, amount, balance_after, created_by, note)
            VALUES (%s, 'writeoff', %s, 0, %s, %s)
            """,
            (
                advance_id, outstanding, user.user_id,
                (body.note or "").strip() or "Written off",
            ),
        )
        try_log_mutation(
            user, "update", "account_advances", str(advance_id),
            new_values={"status": "written_off", "outstanding": 0},
            metadata={"endpoint": "POST /api/advances/{id}/writeoff"},
            conn=conn,
        )
        conn.commit()
    return {"status": "ok", "id": advance_id, "wrote_off_outstanding": outstanding}
