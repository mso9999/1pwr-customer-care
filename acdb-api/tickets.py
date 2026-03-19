"""
WhatsApp ticket audit trail endpoints.

Mirrors O&M tickets created in uGridPlan so CC has a local record
of every WhatsApp-originated complaint.

  POST /api/tickets       — log a new ticket (called by WA bridge after UGP creation)
  GET  /api/tickets       — list recent tickets (paginated)
  GET  /api/tickets/{id}  — single ticket by DB id or ugp_ticket_id
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from customer_api import get_connection
from middleware import CurrentUser, require_employee

logger = logging.getLogger("cc-api.tickets")

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

IOT_KEY_HEADER = "X-IoT-Key"


class TicketCreate(BaseModel):
    ugp_ticket_id: str
    source: str = "whatsapp"
    phone: Optional[str] = None
    customer_id: Optional[int] = None
    account_number: Optional[str] = None
    site_code: Optional[str] = None
    fault_description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    reported_by: Optional[str] = None


@router.post("")
def create_ticket(body: TicketCreate):
    """Log a WhatsApp-originated ticket. Called by the WA bridge after
    creating the ticket in uGridPlan. No auth required (bridge is internal)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO wa_tickets
                (ugp_ticket_id, source, phone, customer_id, account_number,
                 site_code, fault_description, category, priority, reported_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (
                body.ugp_ticket_id,
                body.source,
                body.phone,
                body.customer_id,
                body.account_number,
                body.site_code,
                body.fault_description,
                body.category,
                body.priority,
                body.reported_by,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        logger.info("Ticket logged: id=%s ugp=%s acct=%s", row[0], body.ugp_ticket_id, body.account_number)
        return {
            "status": "ok",
            "id": row[0],
            "ugp_ticket_id": body.ugp_ticket_id,
            "created_at": row[1].isoformat() if row[1] else None,
        }


@router.get("")
def list_tickets(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    site_code: Optional[str] = Query(None),
    account_number: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """List recent WhatsApp tickets with optional filters."""
    clauses = []
    params: list = []

    if site_code:
        clauses.append("site_code = %s")
        params.append(site_code.upper())
    if account_number:
        clauses.append("account_number = %s")
        params.append(account_number)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, ugp_ticket_id, source, phone, customer_id,
                   account_number, site_code, fault_description,
                   category, priority, reported_by, created_at
            FROM wa_tickets
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        rows = cur.fetchall()
        cols = [
            "id", "ugp_ticket_id", "source", "phone", "customer_id",
            "account_number", "site_code", "fault_description",
            "category", "priority", "reported_by", "created_at",
        ]
        tickets = []
        for r in rows:
            t = dict(zip(cols, r))
            if t["created_at"]:
                t["created_at"] = t["created_at"].isoformat()
            tickets.append(t)
        return {"tickets": tickets, "count": len(tickets)}


@router.get("/{ticket_ref}")
def get_ticket(
    ticket_ref: str,
    user: CurrentUser = Depends(require_employee),
):
    """Look up a single ticket by DB id (numeric) or ugp_ticket_id (string)."""
    with get_connection() as conn:
        cur = conn.cursor()
        if ticket_ref.isdigit():
            cur.execute(
                "SELECT id, ugp_ticket_id, source, phone, customer_id, "
                "account_number, site_code, fault_description, "
                "category, priority, reported_by, created_at "
                "FROM wa_tickets WHERE id = %s",
                (int(ticket_ref),),
            )
        else:
            cur.execute(
                "SELECT id, ugp_ticket_id, source, phone, customer_id, "
                "account_number, site_code, fault_description, "
                "category, priority, reported_by, created_at "
                "FROM wa_tickets WHERE ugp_ticket_id = %s",
                (ticket_ref,),
            )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Ticket not found: {ticket_ref}")
        cols = [
            "id", "ugp_ticket_id", "source", "phone", "customer_id",
            "account_number", "site_code", "fault_description",
            "category", "priority", "reported_by", "created_at",
        ]
        t = dict(zip(cols, row))
        if t["created_at"]:
            t["created_at"] = t["created_at"].isoformat()
        return t
