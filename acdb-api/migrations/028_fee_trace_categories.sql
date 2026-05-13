-- Fee trace categories for workbook vs ledger reconciliation (ops queue in 1PDB).

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS connection_fee_trace_category VARCHAR(64),
    ADD COLUMN IF NOT EXISTS readyboard_fee_trace_category VARCHAR(64),
    ADD COLUMN IF NOT EXISTS connection_fee_trace_note TEXT,
    ADD COLUMN IF NOT EXISTS readyboard_fee_trace_note TEXT,
    ADD COLUMN IF NOT EXISTS fee_trace_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS fee_trace_updated_by TEXT;

ALTER TABLE customers DROP CONSTRAINT IF EXISTS chk_connection_fee_trace_category;
ALTER TABLE customers
    ADD CONSTRAINT chk_connection_fee_trace_category
    CHECK (
        connection_fee_trace_category IS NULL
        OR connection_fee_trace_category IN (
            'listed_paid_missing_record',
            'resolved_reference_linked',
            'waived_not_required',
            'investigating'
        )
    );

ALTER TABLE customers DROP CONSTRAINT IF EXISTS chk_readyboard_fee_trace_category;
ALTER TABLE customers
    ADD CONSTRAINT chk_readyboard_fee_trace_category
    CHECK (
        readyboard_fee_trace_category IS NULL
        OR readyboard_fee_trace_category IN (
            'listed_paid_missing_record',
            'resolved_reference_linked',
            'waived_not_required',
            'investigating'
        )
    );

COMMIT;
