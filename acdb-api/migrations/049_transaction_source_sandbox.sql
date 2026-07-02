-- Add 'sandbox' as a valid transaction_source value.
-- Used by app_sandbox._seed_payments() to tag synthetic sandbox payments so
-- they can never be confused with real ingest. Idempotent (IF NOT EXISTS).
-- Required for the ring-fenced app sandbox DB (and harmless on production,
-- where the seeder is gated off by APP_SANDBOX + the production-DB guard).

ALTER TYPE transaction_source ADD VALUE IF NOT EXISTS 'sandbox';
