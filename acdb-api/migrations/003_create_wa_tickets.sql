-- Migration 003: Create wa_tickets table for WhatsApp ticket audit trail
-- Mirrors O&M tickets created in uGridPlan so CC has a local record.

BEGIN;

CREATE TABLE IF NOT EXISTS wa_tickets (
    id              SERIAL PRIMARY KEY,
    ugp_ticket_id   VARCHAR(64) NOT NULL,
    source          VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
    phone           VARCHAR(32),
    customer_id     INTEGER,
    account_number  VARCHAR(32),
    site_code       VARCHAR(8),
    fault_description TEXT,
    category        VARCHAR(64),
    priority        VARCHAR(8),
    reported_by     VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wa_tickets_ugp_ticket_id
    ON wa_tickets (ugp_ticket_id);

CREATE INDEX IF NOT EXISTS idx_wa_tickets_account_number
    ON wa_tickets (account_number) WHERE account_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wa_tickets_created_at
    ON wa_tickets (created_at DESC);

COMMIT;
