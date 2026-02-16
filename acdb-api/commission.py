"""
Commission Customer router for 1PWR Customer Care Portal.
Handles customer commissioning (populating tblcustomer fields on service
connection) and bilingual contract generation with SMS delivery.

Endpoints:
  GET  /api/commission/customer/{customer_id}  – fetch pre-fill data
  POST /api/commission/execute                 – commission + generate contract + SMS
  GET  /api/contracts/download/{site_code}/{filename}  – public PDF download (no auth)
  GET  /api/commission/contracts/{customer_id}  – list contracts for a customer
"""

import logging
import os
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
    customer_id: int
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
    customer_signature: str                 # base64 JPEG from tablet canvas
    commissioned_by: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /api/commission/customer/{customer_id}
# ---------------------------------------------------------------------------

@router.get("/api/commission/customer/{customer_id}")
async def get_commission_data(customer_id: int, user: CurrentUser = Depends(require_employee)):
    """Fetch customer + meter + account data for pre-populating the commission form."""

    with _get_connection() as conn:
        cursor = conn.cursor()

        # Customer basics
        cursor.execute(
            "SELECT * FROM tblcustomer WHERE [CUSTOMER ID] = ?", (customer_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")

        cols = [desc[0] for desc in cursor.description]
        customer = dict(zip(cols, row))

        # Meter info (most recent)
        meter = None
        try:
            cursor.execute(
                "SELECT TOP 1 * FROM tblmeter WHERE [customer id] = ? "
                "ORDER BY [customer connect date] DESC",
                (customer_id,),
            )
            mrow = cursor.fetchone()
            if mrow:
                mcols = [desc[0] for desc in cursor.description]
                meter = dict(zip(mcols, mrow))
        except Exception:
            pass

        # Account number
        account_number = ""
        if meter:
            account_number = str(meter.get("accountnumber", ""))
        if not account_number:
            try:
                cursor.execute(
                    "SELECT TOP 1 accountnumber FROM tblaccountnumbers "
                    "WHERE customerid = ? ORDER BY [opened date] DESC",
                    (customer_id,),
                )
                arow = cursor.fetchone()
                if arow:
                    account_number = str(arow[0])
            except Exception:
                pass

    # Check for existing contracts on disk
    existing_contracts = []
    if account_number:
        existing_contracts = list_customer_contracts(account_number)

    return {
        "customer": {
            "customer_id": customer_id,
            "first_name": customer.get("FIRST NAME", ""),
            "last_name": customer.get("LAST NAME", ""),
            "phone": customer.get("PHONE", "") or customer.get("CELL PHONE 1", ""),
            "national_id": customer.get("ID NUMBER", ""),
            "concession": customer.get("Concession name", ""),
            "customer_type": customer.get("CUSTOMER POSITION", ""),
            "gps_x": customer.get("GPS X", ""),
            "gps_y": customer.get("GPS Y", ""),
            "date_connected": str(customer.get("DATE SERVICE CONNECTED", "") or ""),
        },
        "meter": {
            "meter_id": meter.get("meterid", "") if meter else "",
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
    1. Update tblcustomer with commissioning fields
    2. Generate bilingual contract PDFs
    3. SMS download links to customer
    """

    # ----- Phase 1: Update ACCDB ----- #
    with _get_connection() as conn:
        cursor = conn.cursor()

        # Fetch current customer to get names if not provided
        cursor.execute(
            "SELECT [FIRST NAME], [LAST NAME] FROM tblcustomer WHERE [CUSTOMER ID] = ?",
            (req.customer_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")

        first_name = req.first_name or str(row[0] or "")
        last_name = req.last_name or str(row[1] or "")

        # Build UPDATE
        updates: Dict[str, Any] = {
            "DATE SERVICE CONNECTED": req.connection_date,
            "CUSTOMER POSITION": req.customer_type,
            "ID NUMBER": req.national_id,
        }
        if req.gps_lat:
            updates["GPS Y"] = req.gps_lat
        if req.gps_lng:
            updates["GPS X"] = req.gps_lng

        if updates:
            set_clause = ", ".join(f"[{k}] = ?" for k in updates)
            values = list(updates.values()) + [req.customer_id]
            cursor.execute(
                f"UPDATE tblcustomer SET {set_clause} WHERE [CUSTOMER ID] = ?",
                values,
            )
            conn.commit()
            logger.info(
                "Updated tblcustomer for customer %d: %s",
                req.customer_id,
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

    return {
        "status": "ok",
        "customer_id": req.customer_id,
        "contract_en_url": en_url,
        "contract_so_url": so_url,
        "en_filename": result["en_filename"],
        "so_filename": result["so_filename"],
        "sms_sent": sms_sent,
    }


# ---------------------------------------------------------------------------
# POST /api/commission/decommission/{customer_id}
# ---------------------------------------------------------------------------

@router.post("/api/commission/decommission/{customer_id}")
async def decommission_customer(customer_id: int, user: CurrentUser = Depends(require_employee)):
    """Decommission a customer (non-destructive).

    Sets DATE SERVICE TERMINATED on tblcustomer.  All meter, account,
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
            "SELECT [DATE SERVICE CONNECTED], [DATE SERVICE TERMINATED] "
            "FROM tblcustomer WHERE [CUSTOMER ID] = ?",
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
                detail="Customer has not been commissioned (no DATE SERVICE CONNECTED).",
            )
        if terminated and str(terminated).strip():
            raise HTTPException(
                status_code=400,
                detail="Customer is already terminated.",
            )

        # Collect associated records for the response (read-only)
        meters: List[Dict[str, str]] = []
        accounts: List[Dict[str, str]] = []

        for meter_table in ["tblmeter", "Copy Of tblmeter"]:
            try:
                cursor.execute(
                    f"SELECT [meterid], [accountnumber], [community] "
                    f"FROM [{meter_table}] WHERE [customer id] = ?",
                    (customer_id,),
                )
                for mrow in cursor.fetchall():
                    meters.append({
                        "source": meter_table,
                        "meterid": str(mrow[0] or ""),
                        "accountnumber": str(mrow[1] or ""),
                        "community": str(mrow[2] or ""),
                    })
            except Exception as e:
                logger.warning("Could not query %s for decommission info: %s", meter_table, e)

        try:
            cursor.execute(
                "SELECT [accountnumber], [meterid] "
                "FROM tblaccountnumbers WHERE [customerid] = ?",
                (customer_id,),
            )
            for arow in cursor.fetchall():
                accounts.append({
                    "accountnumber": str(arow[0] or ""),
                    "meterid": str(arow[1] or ""),
                })
        except Exception as e:
            logger.warning("Could not query tblaccountnumbers for decommission info: %s", e)

        # Set DATE SERVICE TERMINATED — the only write operation
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "UPDATE tblcustomer SET [DATE SERVICE TERMINATED] = ? "
            "WHERE [CUSTOMER ID] = ?",
            (today, customer_id),
        )
        conn.commit()
        logger.info("Decommissioned customer %d: DATE SERVICE TERMINATED = %s", customer_id, today)

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

@router.get("/api/commission/contracts/{customer_id}")
async def list_contracts_for_customer(customer_id: int, user: CurrentUser = Depends(require_employee)):
    """List all contract files on disk for a given customer.
    Used by the customer detail page to show available contracts.
    """

    # Look up account number for this customer
    account_number = ""
    with _get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TOP 1 accountnumber FROM tblaccountnumbers "
                "WHERE customerid = ? ORDER BY [opened date] DESC",
                (customer_id,),
            )
            row = cursor.fetchone()
            if row:
                account_number = str(row[0])
        except Exception:
            pass

        # Also try tblmeter
        if not account_number:
            try:
                cursor.execute(
                    "SELECT TOP 1 accountnumber FROM tblmeter "
                    "WHERE [customer id] = ? ORDER BY [customer connect date] DESC",
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
