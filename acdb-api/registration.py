"""
Customer Registration Module
=============================
Account number generation, portal registration, and Excel bulk import.

Replaces VBA: ExcelImport.bas, programs.bas (assignaccno)

Endpoints:
  POST /api/customers/register         — Create single customer
  POST /api/customers/bulk-import      — Upload Excel for bulk registration
  GET  /api/customers/next-account     — Preview next account number for a site
"""

import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from country_config import COUNTRY
from middleware import require_employee, require_role
from models import CurrentUser
from mutations import log_mutation

logger = logging.getLogger("cc-api.registration")

router = APIRouter(prefix="/api/customers", tags=["registration"])


def _get_connection():
    from customer_api import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Account number generation
# ---------------------------------------------------------------------------

def generate_account_number(conn, community: str) -> str:
    """Generate the next sequential account number for a community.

    Format: NNNNXXX (e.g., 0042MAS)
    Uses PostgreSQL's next_account_number() function defined in schema.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT next_account_number(%s)", (community.upper(),))
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CustomerCreateRequest(BaseModel):
    first_name: str = Field(..., min_length=1)
    middle_name: Optional[str] = None
    gender: Optional[str] = None
    last_name: str = Field(..., min_length=1)
    community: str = Field(..., min_length=2, max_length=10)
    phone: Optional[str] = None
    cell_phone_1: Optional[str] = None
    cell_phone_2: Optional[str] = None
    email: Optional[str] = None
    national_id: Optional[str] = None
    plot_number: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    customer_type: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    date_service_connected: Optional[str] = None
    meter_id: Optional[str] = None


class BulkImportResult(BaseModel):
    total_rows: int
    imported: int
    skipped: int
    errors: List[Dict[str, Any]]


VALID_CUSTOMER_TYPES = {
    "HH1", "HH2", "HH3",
    "SME", "CHU", "SCP", "SCH", "HC", "PWH", "GOV", "COM", "IND",
    "REL", "AGR", "CLI", "PUE", "HCF", "OTH", "OTHER",
}

VALID_GENDERS = {
    "MALE": "Male",
    "M": "Male",
    "FEMALE": "Female",
    "F": "Female",
}


def _infer_customer_type(explicit_value: Optional[str], plot_number: Optional[str]) -> Optional[str]:
    """Resolve a canonical stored customer type without persisting aggregate HH."""
    explicit = str(explicit_value or "").strip().upper()
    if explicit in VALID_CUSTOMER_TYPES:
        return explicit
    if explicit == "HH":
        explicit = ""

    plot = str(plot_number or "").strip().upper()
    if plot:
        for code in sorted(VALID_CUSTOMER_TYPES | {"HH"}, key=len, reverse=True):
            if re.search(rf"(?:^|[\s_]){re.escape(code)}(?:[\s_]|$)", plot):
                if code == "HH":
                    return None
                return code

    return None


def _normalize_phone_for_storage(raw: Optional[str]) -> Optional[str]:
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if not digits:
        return None

    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(COUNTRY.dial_code) and len(digits) > len(COUNTRY.dial_code):
        digits = digits[len(COUNTRY.dial_code):]
    if digits.startswith("0") and len(digits) > 8:
        digits = digits[1:]

    return digits or None


def _normalize_gender_for_storage(raw: Optional[str]) -> Optional[str]:
    value = str(raw or "").strip()
    if not value:
        return None

    normalized = VALID_GENDERS.get(value.upper())
    if not normalized:
        raise HTTPException(status_code=400, detail="gender must be Male or Female when provided")

    return normalized


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register")
def register_customer(
    req: CustomerCreateRequest,
    user: CurrentUser = Depends(require_employee),
):
    """Register a new customer with auto-generated account number."""
    with _get_connection() as conn:
        cursor = conn.cursor()

        community = req.community.upper()

        # Generate account number
        account_number = generate_account_number(conn, community)

        resolved_customer_type = _infer_customer_type(req.customer_type, req.plot_number)
        gender = _normalize_gender_for_storage(req.gender)
        phone = _normalize_phone_for_storage(req.phone)
        cell_phone_1 = _normalize_phone_for_storage(req.cell_phone_1)
        cell_phone_2 = _normalize_phone_for_storage(req.cell_phone_2)

        # Insert customer
        cursor.execute("""
            INSERT INTO customers (
                first_name, middle_name, gender, last_name, community, phone, cell_phone_1,
                cell_phone_2, email, national_id, plot_number,
                street_address, city, district, customer_type,
                gps_lat, gps_lon, date_service_connected, is_active,
                created_by, updated_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                TRUE, %s, %s
            ) RETURNING id, customer_id_legacy
        """, (
            req.first_name, req.middle_name, gender, req.last_name, community,
            phone, cell_phone_1, cell_phone_2,
            req.email, req.national_id, req.plot_number,
            req.street_address, req.city, req.district,
            resolved_customer_type, req.gps_lat, req.gps_lon, req.date_service_connected,
            user.user_id, user.user_id,
        ))

        row = cursor.fetchone()
        customer_pg_id = row[0]
        customer_legacy_id = row[1]

        # Extract sequence number from account number
        seq = int(account_number[:4])

        # Create account record
        cursor.execute("""
            INSERT INTO accounts (
                account_number, customer_id, meter_id, community,
                account_sequence, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """, (account_number, customer_pg_id, req.meter_id, community, seq, user.user_id))

        conn.commit()

        customer_values = {
            "id": customer_pg_id,
            "customer_id_legacy": customer_legacy_id,
            "first_name": req.first_name,
            "middle_name": req.middle_name,
            "gender": gender,
            "last_name": req.last_name,
            "community": community,
            "phone": phone,
            "cell_phone_1": cell_phone_1,
            "cell_phone_2": cell_phone_2,
            "email": req.email,
            "national_id": req.national_id,
            "plot_number": req.plot_number,
            "street_address": req.street_address,
            "city": req.city,
            "district": req.district,
            "customer_type": resolved_customer_type,
            "gps_lat": req.gps_lat,
            "gps_lon": req.gps_lon,
            "date_service_connected": req.date_service_connected,
            "is_active": True,
            "created_by": user.user_id,
            "updated_by": user.user_id,
        }
        account_values = {
            "account_number": account_number,
            "customer_id": customer_pg_id,
            "meter_id": req.meter_id,
            "community": community,
            "account_sequence": seq,
            "created_by": user.user_id,
        }
        log_mutation(user, "create", "customers", str(customer_pg_id), new_values=customer_values)
        log_mutation(user, "create", "accounts", account_number, new_values=account_values)

        logger.info(
            "Customer registered: %s %s -> %s by %s",
            req.first_name, req.last_name, account_number, user.user_id,
        )

        return {
            "account_number": account_number,
            "customer_id": customer_pg_id,
            "customer_id_legacy": customer_legacy_id,
            "first_name": req.first_name,
            "last_name": req.last_name,
            "community": community,
        }


@router.get("/next-account")
def preview_next_account(
    community: str = Query(..., min_length=2, max_length=10),
    user: CurrentUser = Depends(require_employee),
):
    """Preview the next account number that would be generated for a site."""
    with _get_connection() as conn:
        account_number = generate_account_number(conn, community.upper())
        # Don't commit — this is a preview, not a reservation
        conn.rollback()
        return {"community": community.upper(), "next_account_number": account_number}


@router.post("/bulk-import", response_model=BulkImportResult)
async def bulk_import_customers(
    file: UploadFile = File(...),
    community: str = Query(..., min_length=2, max_length=10),
    user: CurrentUser = Depends(require_role(["superadmin", "onm_team"])),
):
    """Bulk import customers from an Excel file.

    Expected columns: first_name, last_name, phone, customer_type,
                      gender,
                      plot_number, national_id, gps_lat, gps_lon
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="File must be .xlsx or .xls")

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    contents = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(contents), read_only=True)
    ws = wb.active

    # Read header row
    headers = [str(cell.value or "").strip().lower().replace(" ", "_")
               for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    required = {"first_name", "last_name"}
    if not required.issubset(set(headers)):
        raise HTTPException(
            status_code=400,
            detail=f"Excel must have columns: {', '.join(required)}. Found: {headers}",
        )

    community_upper = community.upper()
    imported = 0
    skipped = 0
    errors = []

    with _get_connection() as conn:
        cursor = conn.cursor()

        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            row_dict = dict(zip(headers, row))

            first_name = str(row_dict.get("first_name", "")).strip()
            last_name = str(row_dict.get("last_name", "")).strip()

            if not first_name or not last_name:
                skipped += 1
                continue

            try:
                account_number = generate_account_number(conn, community_upper)
                seq = int(account_number[:4])
                gender = _normalize_gender_for_storage(
                    str(row_dict.get("gender", "") or "").strip() or None
                )

                cursor.execute("""
                    INSERT INTO customers (
                        first_name, gender, last_name, community, phone,
                        national_id, plot_number, customer_type,
                        gps_lat, gps_lon, is_active,
                        created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                    RETURNING id
                """, (
                    # Preserve explicit HH1/HH2/HH3/etc. when present, but do not
                    # persist aggregate HH as though it were an atomic type.
                    first_name, gender, last_name, community_upper,
                    _normalize_phone_for_storage(str(row_dict.get("phone", "") or "").strip() or None),
                    str(row_dict.get("national_id", "") or "").strip() or None,
                    str(row_dict.get("plot_number", "") or "").strip() or None,
                    _infer_customer_type(
                        str(row_dict.get("customer_type", "") or "").strip() or None,
                        str(row_dict.get("plot_number", "") or "").strip() or None,
                    ),
                    float(row_dict["gps_lat"]) if row_dict.get("gps_lat") else None,
                    float(row_dict["gps_lon"]) if row_dict.get("gps_lon") else None,
                    user.user_id,
                ))
                customer_pg_id = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO accounts (
                        account_number, customer_id, community,
                        account_sequence, created_by
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (account_number, customer_pg_id, community_upper, seq, user.user_id))

                imported += 1

            except Exception as e:
                errors.append({"row": row_num, "error": str(e)})
                conn.rollback()

        if imported > 0:
            conn.commit()

    wb.close()
    logger.info(
        "Bulk import: %d imported, %d skipped, %d errors from %s by %s",
        imported, skipped, len(errors), file.filename, user.user_id,
    )

    return BulkImportResult(
        total_rows=row_num - 1 if 'row_num' in dir() else 0,
        imported=imported,
        skipped=skipped,
        errors=errors[:20],  # Cap error list
    )
