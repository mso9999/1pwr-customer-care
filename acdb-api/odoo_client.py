"""
Odoo JSON-RPC Client
====================

Connects to the Odoo (Benin) ERP instance to pull invoiced revenue
for clinic/institutional (non-prepaid) customers.

Environment variables:
  ODOO_URL      — e.g. https://odoo.1pwrafrica.com
  ODOO_DB       — database name
  ODOO_USERNAME — login user
  ODOO_PASSWORD — login password / API key
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("cc-api.odoo")

ODOO_URL = os.environ.get("ODOO_URL", "")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USERNAME = os.environ.get("ODOO_USERNAME", "")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")

API_TIMEOUT = 60


class OdooClient:
    """Lightweight Odoo JSON-RPC client."""

    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.url = (url or ODOO_URL).rstrip("/")
        self.db = db or ODOO_DB
        self.username = username or ODOO_USERNAME
        self.password = password or ODOO_PASSWORD
        self._uid: Optional[int] = None

    def _json_rpc(self, endpoint: str, params: Any) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "params": params, "id": random.randint(0, 1_000_000_000)}
        r = requests.post(
            f"{self.url}{endpoint}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"Odoo error: {body['error']}")
        return body.get("result")

    def authenticate(self) -> int:
        """Authenticate and return the user UID."""
        if self._uid is not None:
            return self._uid
        uid = self._json_rpc(
            "/jsonrpc",
            {"service": "common", "method": "authenticate", "args": [self.db, self.username, self.password, {}]},
        )
        if not uid:
            raise RuntimeError("Odoo authentication failed — check ODOO_URL/DB/USERNAME/PASSWORD")
        self._uid = uid
        logger.info("Odoo authenticated as %s (uid=%d)", self.username, uid)
        return uid

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Execute a search_read on an Odoo model."""
        uid = self.authenticate()
        result = self._json_rpc(
            "/jsonrpc",
            {
                "service": "object",
                "method": "execute_kw",
                "args": [self.db, uid, self.password, model, "search_read", [domain, fields], {"limit": limit, "offset": offset}],
            },
        )
        return result or []

    def search(self, model: str, domain: List[Any], limit: Optional[int] = None) -> List[int]:
        """Execute a search on an Odoo model, returning IDs."""
        uid = self.authenticate()
        result = self._json_rpc(
            "/jsonrpc",
            {
                "service": "object",
                "method": "execute_kw",
                "args": [self.db, uid, self.password, model, "search", [domain], {"limit": limit}],
            },
        )
        return result or []

    # -----------------------------------------------------------------------
    # High-level helpers for invoiced revenue
    # -----------------------------------------------------------------------

    def search_invoices(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        partner_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch posted invoices from account.move."""
        domain: List[Any] = [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
        ]
        if date_from:
            domain.append(("invoice_date", ">=", date_from))
        if date_to:
            domain.append(("invoice_date", "<=", date_to))
        if partner_ids:
            domain.append(("partner_id", "in", partner_ids))

        return self.search_read(
            "account.move",
            domain,
            ["id", "name", "partner_id", "invoice_date", "amount_total", "amount_untaxed", "currency_id", "state"],
        )

    def get_invoice_lines(self, invoice_id: int) -> List[Dict[str, Any]]:
        """Fetch invoice lines for a specific invoice."""
        return self.search_read(
            "account.move.line",
            [("move_id", "=", invoice_id), ("display_type", "=", "product")],
            ["id", "product_id", "name", "quantity", "price_unit", "price_subtotal", "account_id"],
        )

    def search_partners(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search res.partner records, optionally filtered by category."""
        domain: List[Any] = [("is_company", "=", True)]
        if category:
            domain.append(("category_id.name", "ilike", category))
        return self.search_read(
            "res.partner",
            domain,
            ["id", "name", "email", "phone", "country_id", "state_id"],
        )

    def get_expenses(
        self,
        date_from: str,
        date_to: str,
        analytic_account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch expense / vendor bill lines for a date range."""
        domain: List[Any] = [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", date_from),
            ("invoice_date", "<=", date_to),
        ]
        if analytic_account:
            domain.append(("analytic_distribution", "ilike", analytic_account))
        return self.search_read(
            "account.move",
            domain,
            ["id", "name", "partner_id", "invoice_date", "amount_total", "amount_untaxed", "currency_id"],
        )

    def get_income_statement(self, date_from: str, date_to: str) -> Dict[str, Any]:
        """Fetch income statement accounts for a date range."""
        lines = self.search_read(
            "account.move.line",
            [
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("parent_state", "=", "posted"),
            ],
            ["account_id", "name", "debit", "credit", "date", "move_id"],
        )
        return {"date_from": date_from, "date_to": date_to, "lines": lines}


# ---------------------------------------------------------------------------
# ETL: Pull Odoo invoices into invoiced_revenue table
# ---------------------------------------------------------------------------

def pull_odoo_invoices(
    date_from: str,
    date_to: str,
    site_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch invoices from Odoo and store in invoiced_revenue table.

    Returns a summary dict with counts.
    """
    from customer_api import get_connection

    client = OdooClient()
    if not client.url:
        return {"error": "ODOO_URL not configured", "invoices": 0}

    invoices = client.search_invoices(date_from=date_from, date_to=date_to)
    logger.info("Odoo: fetched %d invoices (%s to %s)", len(invoices), date_from, date_to)

    with get_connection() as conn:
        stored = 0
        with conn.cursor() as cur:
            for inv in invoices:
                invoice_id = f"odoo-{inv['id']}"
                partner_name = inv.get("partner_id", [None, ""])[1] if isinstance(inv.get("partner_id"), list) else str(inv.get("partner_id", ""))
                invoice_date = inv.get("invoice_date")
                amount_local = float(inv.get("amount_total", 0) or 0)
                currency = inv.get("currency_id", [None, "XOF"])[1] if isinstance(inv.get("currency_id"), list) else "XOF"

                # Derive period from invoice_date
                if invoice_date:
                    if isinstance(invoice_date, str):
                        d = datetime.fromisoformat(invoice_date)
                    else:
                        d = invoice_date
                    period = f"{d.year}-{d.month:02d}"
                else:
                    continue

                # Get FX rate
                from investor_analytics import _fx_to_usd
                d_date = date(d.year, d.month, 1)
                fx = _fx_to_usd(conn, currency, d_date)
                amount_usd = round(amount_local * fx, 2)

                # Try to get kWh from invoice lines
                kwh = None
                try:
                    lines = client.get_invoice_lines(inv["id"])
                    for line in lines:
                        product_name = str(line.get("product_id", [None, ""])[1]).lower() if isinstance(line.get("product_id"), list) else ""
                        if "kwh" in product_name or "energy" in product_name or "electric" in product_name:
                            kwh = float(line.get("quantity", 0) or 0)
                            break
                except Exception:
                    pass

                cur.execute(
                    """
                    INSERT INTO invoiced_revenue
                        (invoice_id, site_code, customer_name, customer_type,
                         invoice_date, period, kwh, amount_local, currency,
                         amount_usd, collection_status, synced_at)
                    VALUES (%s, %s, %s, 'C_I', %s, %s, %s, %s, %s, %s, 'invoiced', NOW())
                    ON CONFLICT (invoice_id) DO UPDATE SET
                        kwh = EXCLUDED.kwh,
                        amount_local = EXCLUDED.amount_local,
                        amount_usd = EXCLUDED.amount_usd,
                        synced_at = NOW()
                    """,
                    (invoice_id, site_code, partner_name, d.strftime("%Y-%m-%d"),
                     period, kwh, amount_local, currency, amount_usd),
                )
                stored += 1
        conn.commit()

    logger.info("Odoo ETL: stored %d invoices", stored)
    return {"invoices_fetched": len(invoices), "invoices_stored": stored}


def match_odoo_to_1pdb() -> Dict[str, Any]:
    """Attempt to match Odoo partners to 1PDB customers by name/phone.

    This is a best-effort matching — updates account_number on invoiced_revenue
    rows where a match is found.
    """
    from customer_api import get_connection

    client = OdooClient()
    if not client.url:
        return {"error": "ODOO_URL not configured", "matched": 0}

    partners = client.search_partners()
    matched = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for p in partners:
                partner_name = p.get("name", "")
                if not partner_name:
                    continue

                # Try exact name match in customers
                cur.execute(
                    "SELECT account_number FROM customers WHERE name ILIKE %s LIMIT 1",
                    (f"%{partner_name}%",),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE invoiced_revenue SET account_number = %s WHERE customer_name = %s",
                        (row[0], partner_name),
                    )
                    if cur.rowcount > 0:
                        matched += cur.rowcount
        conn.commit()

    logger.info("Odoo matching: %d invoices matched", matched)
    return {"partners_checked": len(partners), "matched": matched}
