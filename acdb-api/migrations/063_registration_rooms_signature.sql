-- Migration 063: registration-time "number of rooms" + customer signature
--
-- Closes the two gaps between the MGF018 paper Customer Registration Ledger
-- and CC registration (analysis 2026-08-17): the ledger captures a room count
-- and a customer signature at registration; CC captured neither.
--
-- Both are OPTIONAL at registration. The signature JPEG is stored on disk
-- under acdb-api/contracts/<SITE>/registration/ (same host-side convention as
-- generated contracts and advance contracts); only metadata lives here.

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS number_of_rooms INTEGER;

ALTER TABLE customers
  DROP CONSTRAINT IF EXISTS chk_number_of_rooms_nonneg;
ALTER TABLE customers
    ADD CONSTRAINT chk_number_of_rooms_nonneg
    CHECK (number_of_rooms IS NULL OR number_of_rooms >= 0);

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS registration_signature_path TEXT,
    ADD COLUMN IF NOT EXISTS registration_signature_filename VARCHAR(255),
    ADD COLUMN IF NOT EXISTS registration_signature_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS registration_signature_captured_by TEXT,
    ADD COLUMN IF NOT EXISTS registration_signature_captured_at TIMESTAMPTZ;

COMMENT ON COLUMN customers.number_of_rooms IS
    'Room count from the MGF018 registration ledger. Optional; informs demand estimation and installation planning.';
COMMENT ON COLUMN customers.registration_signature_path IS
    'Host-side path to the registration signature JPEG (drawn or uploaded at registration).';
COMMENT ON COLUMN customers.registration_signature_sha256 IS
    'SHA-256 of the signature JPEG for tamper-evidence.';

COMMIT;
