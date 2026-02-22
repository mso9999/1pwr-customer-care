-- Migration: Remove customer_id_legacy from meters table
-- This column is redundant with meters.account_number, which can resolve
-- to a customer via the accounts table or directly via customers.
--
-- Run this AFTER deploying the code changes that no longer reference
-- meters.customer_id_legacy.
--
-- Idempotent: safe to re-run.

ALTER TABLE meters DROP COLUMN IF EXISTS customer_id_legacy;
