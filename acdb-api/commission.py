"""
Commission Customer router for 1PWR Customer Care Portal.
Handles customer commissioning (populating customers fields on service
connection) and bilingual contract generation with SMS delivery.

Endpoints:
  GET  /api/commission/customer/{customer_id}  – fetch pre-fill data
  POST /api/commission/execute                 – commission + generate contract + SMS
  GET  /api/contracts/download/{site_code}/{filename}  – public PDF download (no auth)
  GET  /api/commission/contracts/{customer_id}  – list contracts for a customer
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from contract_gen import (
    CONTRACTS_DIR,
    build_download_url,
    generate_contract,
    list_customer_contracts,
    send_contract_sms,
)
from middleware import require_employee, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["commission"])


# ---------------------------------------------------------------------------
# DB helper – import from customer_api to share the connection pool
# ---------------------------------------------------------------------------

def _get_connection():
    """Lazy import to avoid circular imports at module level."""
    from customer_api import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CommissionRequest(BaseModel):
    customer_id: Optional[int] = None
    account_number: str
    site_code: str                          # concession code (MAK, LEB, ...)
    customer_type: str                      # HH, SME, CHU, SCP, etc.
    connection_date: str                    # YYYY-MM-DD
    service_phase: str                      # "Single" or "Three"
    ampacity: str                           # "Standard" or custom value
    national_id: str
    phone_number: str
    first_name: Optional[str] = None        # pre-filled from DB, overrideable
    last_name: Optional[str] = None
    gps_lat: Optional[str] = None
    gps_lng: Optional[str] = None
    survey_id: Optional[str] = None         # UGP connection binding (from picker)
    customer_signature: str                 # base64 JPEG from tablet canvas
    commissioned_by: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /api/commission/customer/{identifier}
# Accepts account_number (e.g. 0045MAK) or legacy numeric customer_id.
# ---------------------------------------------------------------------------

def _resolve_customer_for_commission(cursor, identifier: str):
    """Resolve a customer by account_number or legacy ID. Returns (customer_dict, meter_dict, account_number)."""
    import re
    customer = None
    meter = None
    account_number = ""

    is_account = bool(re.match(r"^\d{3,4}[A-Za-z]{2,4}$", identifier.strip()))

    if is_account:
        account_number = identifier.strip().upper()
        cursor.execute(
            "SELECT c.* FROM accounts a "
            "JOIN customers c ON a.customer_id = c.id "
            "WHERE a.account_number = %s LIMIT 1",
            (account_number,),
        )
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            customer = dict(zip(cols, row))
    else:
        cursor.execute(
            "SELECT * FROM customers WHERE customer_id_legacy = %s", (int(identifier),)
        )
        row = cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            customer = dict(zip(cols, row))

    if not customer:
        return None, None, ""

    legacy_id = customer.get("customer_id_legacy")

    # Resolve account_number if not already known
    if not account_number:
        try:
            cursor.execute(
                "SELECT a.account_number FROM accounts a "
                "JOIN customers c ON a.customer_id = c.id "
                "WHERE c.customer_id_legacy = %s ORDER BY a.opened_date DESC NULLS LAST LIMIT 1",
                (legacy_id,),
            )
            arow = cursor.fetchone()
            if arow:
                account_number = str(arow[0])
        except Exception:
            pass

    # Meter info via account_number
    if account_number:
        try:
            cursor.execute(
                "SELECT * FROM meters WHERE account_number = %s "
                "ORDER BY customer_connect_date DESC NULLS LAST LIMIT 1",
                (account_number,),
            )
            mrow = cursor.fetchone()
            if mrow:
                mcols = [d[0] for d in cursor.description]
                meter = dict(zip(mcols, mrow))
        except Exception:
            pass

    return customer, meter, account_number


@router.get("/api/commission/customer/{identifier}")
async def get_commission_data(identifier: str, user: CurrentUser = Depends(require_employee)):
    """Fetch customer + meter + account data for pre-populating the commission form.
    Accepts account_number (e.g. 0045MAK) or legacy numeric customer_id.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        customer, meter, account_number = _resolve_customer_for_commission(cursor, identifier)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

    existing_contracts = []
    if account_number:
        existing_contracts = list_customer_contracts(account_number)

    return {
        "customer": {
            "customer_id_legacy": customer.get("customer_id_legacy"),
            "first_name": customer.get("first_name", ""),
            "last_name": customer.get("last_name", ""),
            "phone": customer.get("phone", "") or customer.get("cell_phone_1", ""),
            "national_id": customer.get("national_id", ""),
            "concession": customer.get("community", ""),
            "customer_type": customer.get("customer_position", ""),
            "gps_x": customer.get("gps_lat", ""),
            "gps_y": customer.get("gps_lon", ""),
            "date_connected": str(customer.get("date_service_connected", "") or ""),
        },
        "meter": {
            "meter_id": meter.get("meter_id", "") if meter else "",
            "community": meter.get("community", "") if meter else "",
        } if meter else None,
        "account_number": account_number,
        "existing_contracts": existing_contracts,
    }


# ---------------------------------------------------------------------------
# POST /api/commission/execute
# ---------------------------------------------------------------------------

@router.post("/api/commission/execute")
async def execute_commission(req: CommissionRequest, user: CurrentUser = Depends(require_employee)):
    """Execute customer commissioning:
    1. Update customers table with commissioning fields
    2. Generate bilingual contract PDFs
    3. SMS download links to customer
    """

    # ----- Phase 1: Update PostgreSQL ----- #
    with _get_connection() as conn:
        cursor = conn.cursor()

        # Resolve customer: prefer account_number, fall back to legacy customer_id
        customer, _, resolved_acct = _resolve_customer_for_commission(
            cursor, req.account_number or str(req.customer_id or "")
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        legacy_id = customer["customer_id_legacy"]

        first_name = req.first_name or str(customer.get("first_name") or "")
        last_name = req.last_name or str(customer.get("last_name") or "")

        updates: Dict[str, Any] = {
            "date_service_connected": req.connection_date,
            "customer_position": req.customer_type,
            "national_id": req.national_id,
        }
        if req.gps_lat:
            updates["gps_lat"] = req.gps_lat
        if req.gps_lng:
            updates["gps_lon"] = req.gps_lng

        if updates:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            values = list(updates.values()) + [legacy_id]
            cursor.execute(
                f"UPDATE customers SET {set_clause} WHERE customer_id_legacy = %s",
                values,
            )
            conn.commit()
            logger.info(
                "Updated customers for %s (legacy %s): %s",
                resolved_acct or req.account_number, legacy_id,
                list(updates.keys()),
            )

    # ----- Phase 2: Generate contracts ----- #
    try:
        result = generate_contract(
            first_name=first_name,
            last_name=last_name,
            national_id=req.national_id,
            phone_number=req.phone_number,
            concession=req.site_code,
            customer_type=req.customer_type,
            service_phase=req.service_phase,
            ampacity=req.ampacity,
            account_number=req.account_number,
            customer_signature_b64=req.customer_signature,
        )
    except Exception as exc:
        logger.error("Contract generation failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Contract generation failed: {exc}",
        )

    en_url = build_download_url(result["site_code"], result["en_filename"])
    so_url = build_download_url(result["site_code"], result["so_filename"])

    # ----- Phase 3: SMS to customer ----- #
    sms_sent = False
    try:
        sms_sent = send_contract_sms(
            first_name=first_name,
            last_name=last_name,
            phone_number=req.phone_number,
            en_url=en_url,
            so_url=so_url,
        )
    except Exception as exc:
        logger.warning("SMS delivery failed: %s", exc)

    # ----- Phase 4: Sync to uGridPLAN ----- #
    ugp_sync_result: Optional[Dict[str, Any]] = None
    survey_id: Optional[str] = None
    try:
        from sync_ugridplan import sync_commission_to_ugp

        # Prefer explicit picker selection, fall back to resolution chain
        survey_id = (req.survey_id or "").strip() or None
        if not survey_id:
            survey_id = _resolve_ugp_survey_id(
                legacy_id, req.account_number, req.site_code
            )
        if survey_id:
            # Look up meter serial for the connection update
            meter_serial = ""
            with _get_connection() as conn2:
                cursor2 = conn2.cursor()
                try:
                    cursor2.execute(
                        "SELECT meter_id FROM meters WHERE account_number = %s "
                        "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
                        (req.account_number,),
                    )
                    mrow = cursor2.fetchone()
                    if mrow:
                        meter_serial = str(mrow[0] or "")
                except Exception:
                    pass

            ugp_sync_result = sync_commission_to_ugp(
                site_code=req.site_code,
                survey_id=survey_id,
                connection_date=req.connection_date,
                account_number=req.account_number,
                meter_serial=meter_serial,
            )
            logger.info(
                "UGP sync for %s (survey=%s): updated=%s, upstream_warnings=%d",
                req.account_number, survey_id,
                ugp_sync_result.get("ugp_updated"),
                len(ugp_sync_result.get("upstream_warnings", [])),
            )

            # Persist the binding on the account row
            try:
                with _get_connection() as conn3:
                    cursor3 = conn3.cursor()
                    cursor3.execute(
                        "UPDATE accounts SET survey_id = %s "
                        "WHERE account_number = %s AND (survey_id IS NULL OR survey_id = '')",
                        (survey_id, req.account_number),
                    )
                    conn3.commit()
            except Exception as pe:
                logger.warning("Could not persist survey_id binding for %s: %s", req.account_number, pe)
        else:
            logger.info(
                "No UGP Survey_ID found for %s (legacy %s) — skipping UGP sync",
                req.account_number, legacy_id,
            )
    except Exception as exc:
        logger.warning("UGP sync failed (non-blocking): %s", exc)

    response: Dict[str, Any] = {
        "status": "ok",
        "customer_id": legacy_id,
        "account_number": resolved_acct or req.account_number,
        "contract_en_url": en_url,
        "contract_so_url": so_url,
        "en_filename": result["en_filename"],
        "so_filename": result["so_filename"],
        "sms_sent": sms_sent,
    }
    if ugp_sync_result:
        response["ugp_sync"] = {
            "updated": ugp_sync_result.get("ugp_updated", False),
            "survey_id": survey_id,
            "upstream_warnings": ugp_sync_result.get("upstream_warnings", []),
            "error": ugp_sync_result.get("error"),
        }
    return response


# ---------------------------------------------------------------------------
# POST /api/commission/decommission/{customer_id}
# ---------------------------------------------------------------------------

@router.post("/api/commission/decommission/{customer_id}")
async def decommission_customer(customer_id: int, user: CurrentUser = Depends(require_employee)):
    """Decommission a customer (non-destructive).

    Sets date_service_terminated on customers table.  All meter, account,
    and transaction records are preserved intact for historical record.
    The terminated date is what marks the customer as decommissioned
    throughout the system.

    Returns the customer's associated meters and accounts for reference.
    """
    from datetime import datetime

    with _get_connection() as conn:
        cursor = conn.cursor()

        # Verify the customer exists and is currently commissioned
        cursor.execute(
            "SELECT date_service_connected, date_service_terminated "
            "FROM customers WHERE customer_id_legacy = %s",
            (customer_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")

        connected = row[0]
        terminated = row[1]
        if not connected or (isinstance(connected, str) and not connected.strip()):
            raise HTTPException(
                status_code=400,
                detail="Customer has not been commissioned (no date_service_connected).",
            )
        if terminated and str(terminated).strip():
            raise HTTPException(
                status_code=400,
                detail="Customer is already terminated.",
            )

        # Collect associated records for the response (read-only)
        meters: List[Dict[str, str]] = []
        accounts: List[Dict[str, str]] = []

        try:
            cursor.execute(
                "SELECT m.meter_id, m.account_number, m.community "
                "FROM meters m "
                "JOIN accounts a ON m.account_number = a.account_number "
                "JOIN customers c ON a.customer_id = c.id "
                "WHERE c.customer_id_legacy = %s",
                (customer_id,),
            )
            for mrow in cursor.fetchall():
                meters.append({
                    "meterid": str(mrow[0] or ""),
                    "accountnumber": str(mrow[1] or ""),
                    "community": str(mrow[2] or ""),
                })
        except Exception as e:
            logger.warning("Could not query meters for decommission info: %s", e)

        try:
            cursor.execute(
                "SELECT account_number, meter_id "
                "FROM accounts WHERE customer_id = %s",
                (customer_id,),
            )
            for arow in cursor.fetchall():
                accounts.append({
                    "accountnumber": str(arow[0] or ""),
                    "meterid": str(arow[1] or ""),
                })
        except Exception as e:
            logger.warning("Could not query accounts for decommission info: %s", e)

        # Set date_service_terminated — the only write operation
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "UPDATE customers SET date_service_terminated = %s "
            "WHERE customer_id_legacy = %s",
            (today, customer_id),
        )
        conn.commit()
        logger.info("Decommissioned customer %d: date_service_terminated = %s", customer_id, today)

    return {
        "status": "ok",
        "customer_id": customer_id,
        "terminated_date": today,
        "connected_date": str(connected or ""),
        "meters": meters,
        "accounts": accounts,
    }


# ---------------------------------------------------------------------------
# GET /api/contracts/download/{site_code}/{filename}  (PUBLIC – no auth)
# ---------------------------------------------------------------------------

@router.get("/api/contracts/download/{site_code}/{filename}")
async def download_contract(site_code: str, filename: str):
    """Public endpoint for customers to download their contract PDF via SMS link.
    No authentication required.
    """
    # Sanitize to prevent path traversal
    safe_site = os.path.basename(site_code)
    safe_name = os.path.basename(filename)

    file_path = os.path.join(CONTRACTS_DIR, safe_site, safe_name)

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Contract not found")

    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=safe_name,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


# ---------------------------------------------------------------------------
# GET /api/commission/contracts/{customer_id}  (authenticated)
# ---------------------------------------------------------------------------

@router.get("/api/commission/contracts/{identifier}")
async def list_contracts_for_customer(identifier: str, user: CurrentUser = Depends(require_employee)):
    """List all contract files on disk for a given customer.
    Accepts account_number (e.g. 0045MAK) or legacy numeric customer_id.
    """
    import re
    is_account = bool(re.match(r"^\d{3,4}[A-Za-z]{2,4}$", identifier.strip()))

    if is_account:
        account_number = identifier.strip().upper()
    else:
        account_number = ""
        customer_id = int(identifier)
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT a.account_number FROM accounts a "
                    "JOIN customers c ON a.customer_id = c.id "
                    "WHERE c.customer_id_legacy = %s ORDER BY a.opened_date DESC NULLS LAST LIMIT 1",
                    (customer_id,),
                )
                row = cursor.fetchone()
                if row:
                    account_number = str(row[0])
            except Exception:
                pass
            if not account_number:
                try:
                    cursor.execute(
                        "SELECT account_number FROM meters "
                        "WHERE customer_id_legacy = %s ORDER BY customer_connect_date DESC NULLS LAST LIMIT 1",
                        (customer_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        account_number = str(row[0])
                except Exception:
                    pass

    if not account_number:
        return {"contracts": [], "account_number": ""}

    contracts = list_customer_contracts(account_number)
    return {"contracts": contracts, "account_number": account_number}


# ---------------------------------------------------------------------------
# Bulk commissioning status update (replaces VBA file-based sync)
# ---------------------------------------------------------------------------

class BulkStatusItem(BaseModel):
    customer_id: int
    step: str  # one of the 7 commissioning step field names
    value: bool
    date: Optional[str] = None


class BulkStatusRequest(BaseModel):
    updates: List[BulkStatusItem]


COMMISSIONING_STEPS = {
    "connection_fee_paid",
    "readyboard_fee_paid",
    "readyboard_tested",
    "readyboard_installed",
    "airdac_connected",
    "meter_installed",
    "customer_commissioned",
}


@router.post("/api/commission/bulk-status")
def bulk_update_commissioning_status(
    req: BulkStatusRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Bulk update commissioning step flags for multiple customers.

    Replaces VBA: retrievecustomerstatus.bas, updatecommissioning.bas
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        updated = 0
        errors = []

        for item in req.updates:
            if item.step not in COMMISSIONING_STEPS:
                errors.append({
                    "customer_id": item.customer_id,
                    "error": f"Invalid step: {item.step}",
                })
                continue

            date_col = f"{item.step}_date"

            try:
                cursor.execute(
                    f"UPDATE customers SET {item.step} = %s, {date_col} = %s, "
                    f"updated_at = NOW(), updated_by = %s "
                    f"WHERE customer_id_legacy = %s",
                    (item.value, item.date or datetime.now().isoformat(),
                     user.user_id, item.customer_id),
                )
                if cursor.rowcount > 0:
                    updated += 1
                else:
                    errors.append({
                        "customer_id": item.customer_id,
                        "error": "Customer not found",
                    })
            except Exception as e:
                errors.append({
                    "customer_id": item.customer_id,
                    "error": str(e),
                })
                conn.rollback()

        conn.commit()

    return {
        "updated": updated,
        "errors": errors,
        "total_requested": len(req.updates),
    }


# ---------------------------------------------------------------------------
# UGP Survey_ID resolution
# ---------------------------------------------------------------------------

def _resolve_ugp_survey_id(
    customer_id: int, account_number: str, site_code: str
) -> Optional[str]:
    """Resolve the uGridPLAN Survey_ID for a customer.

    Tries in order:
    0. Explicit binding in accounts.survey_id (source of truth)
    1. cc_customer_metadata SQLite table (from previous sync)
    2. Derive from plot_number in PostgreSQL (e.g. "MAK 0045 HH" format)
    3. Derive from account_number (e.g. "0045MAK" → "MAK 0045")
    """
    from db_auth import get_auth_db
    import re

    # Strategy 0: Explicit binding stored on the account row
    if account_number:
        try:
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT survey_id FROM accounts "
                    "WHERE account_number = %s AND survey_id IS NOT NULL",
                    (account_number,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return str(row[0]).strip()
        except Exception:
            pass

    # Strategy 1: SQLite metadata from previous UGP sync
    try:
        with get_auth_db() as auth_conn:
            row = auth_conn.execute(
                "SELECT ugp_survey_id FROM cc_customer_metadata "
                "WHERE customer_id = ? AND ugp_survey_id IS NOT NULL",
                (str(customer_id),),
            ).fetchone()
            if row and row["ugp_survey_id"]:
                return row["ugp_survey_id"]
    except Exception:
        pass

    # Strategy 2: Derive from plot_number in PostgreSQL
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT plot_number FROM customers WHERE customer_id_legacy = %s",
                (customer_id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                plot = str(row[0]).strip()
                if plot and plot.lower() != "none":
                    return plot
    except Exception:
        pass

    # Strategy 3: Derive from account_number (e.g. "0045MAK" → "MAK 0045 HH")
    if account_number and site_code:
        m = re.match(r"^(\d{3,4})([A-Za-z]{2,4})$", account_number.strip())
        if m:
            number = m.group(1)
            code = m.group(2).upper()
            return f"{code} {number} HH"

    return None


# ---------------------------------------------------------------------------
# POST /api/commission/energize-upstream
# ---------------------------------------------------------------------------

class EnergizeUpstreamRequest(BaseModel):
    site_code: str
    lines: List[Dict[str, str]]


@router.post("/api/commission/energize-upstream")
async def energize_upstream(
    req: EnergizeUpstreamRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Energize upstream conductors that were flagged during commissioning.

    Accepts the list of non-energized lines returned by the commission
    execute endpoint and sets their status to energized (St_code_4 = 5)
    in uGridPLAN.
    """
    from sync_ugridplan import energize_upstream_lines

    result = energize_upstream_lines(
        site_code=req.site_code,
        line_ids=req.lines,
    )
    return result
