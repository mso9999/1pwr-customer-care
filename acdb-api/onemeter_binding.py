"""Which 1Meter gateway readings may enter billing.

An account owns a meter serial (``meters``). A gateway is whichever Thing
publishes that serial, and one gateway may read several meters, so
``meter_provisioning`` (one serial per Thing) cannot be the gate. Readings
are accepted once the account's customer is commissioned, unless the serial
is commissioned to a different account.
"""

import logging
from typing import Optional

logger = logging.getLogger("cc-api.onemeter-binding")

NOT_COMMISSIONED = "Gateway telemetry cannot enter billing until commissioning is complete."
ASSIGNMENT_MISMATCH = "Gateway, meter serial, and customer assignment do not match."


def telemetry_refusal(cur, meter_id: str, account_number: str) -> Optional[str]:
    """Reason a gateway reading for this meter must stay out of billing, or None."""
    cur.execute(
        "SELECT c.customer_commissioned FROM accounts a "
        "JOIN customers c ON c.id = a.customer_id "
        "WHERE UPPER(a.account_number) = UPPER(%s)",
        (account_number,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return NOT_COMMISSIONED
    cur.execute(
        """
        SELECT 1 FROM meter_provisioning
         WHERE status = 'commissioned'
           AND regexp_replace(meter_serial, '^0+', '') = regexp_replace(%s, '^0+', '')
           AND NULLIF(account_number, '') IS NOT NULL
           AND UPPER(account_number) <> UPPER(%s)
         LIMIT 1
        """,
        (meter_id, account_number),
    )
    if cur.fetchone():
        return ASSIGNMENT_MISMATCH
    return None


def ensure_1meter_binding(cur, account_number: str, gateway_thing: Optional[str] = None) -> int:
    """Mark the account's gateway provisioning row commissioned.

    Thing-level relay commands resolve the account from this row. Same rules
    as migration 059: the customer is commissioned, the serial matches exactly
    one provisioning row, and that row is unassigned or already on this
    account. Returns the number of rows promoted.
    """
    acct = (account_number or "").strip().upper()
    if not acct:
        return 0
    gw = (gateway_thing or "").strip() or None
    cur.execute(
        """
        UPDATE meter_provisioning mp
           SET account_number = %s,
               status = 'commissioned',
               commissioned_at = COALESCE(mp.commissioned_at, NOW()),
               updated_at = NOW()
          FROM meters m
          JOIN accounts a ON UPPER(a.account_number) = UPPER(m.account_number)
          JOIN customers c ON c.id = a.customer_id AND c.customer_commissioned
         WHERE UPPER(m.account_number) = %s
           AND m.platform = 'prototype'
           AND m.status = 'active'
           AND NULLIF(mp.meter_serial, '') IS NOT NULL
           AND regexp_replace(mp.meter_serial, '^0+', '') = regexp_replace(m.meter_id, '^0+', '')
           AND (NULLIF(mp.account_number, '') IS NULL OR UPPER(mp.account_number) = %s)
           AND mp.status NOT IN ('commissioned', 'rotating')
           AND (%s::text IS NULL OR mp.thing_name = %s::text)
           AND NOT EXISTS (
               SELECT 1 FROM meter_provisioning other
                WHERE other.id <> mp.id
                  AND regexp_replace(other.meter_serial, '^0+', '') =
                      regexp_replace(mp.meter_serial, '^0+', '')
           )
        """,
        (acct, acct, acct, gw, gw),
    )
    return cur.rowcount or 0
