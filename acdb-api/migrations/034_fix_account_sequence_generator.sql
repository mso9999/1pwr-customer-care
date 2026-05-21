-- Repair account_sequence drift and harden next_account_number().
--
-- Root cause:
-- Some rows have account_sequence lower than the numeric prefix in
-- account_number (e.g. 0273MAS stored with account_sequence=182), so
-- next_account_number() based on MAX(account_sequence) can reissue an
-- existing account_number and fail with accounts_account_number_key.

-- 1) Backfill / correct account_sequence from account_number where possible.
UPDATE accounts
SET account_sequence = SUBSTRING(account_number FROM '^\d{4}')::INTEGER
WHERE account_number ~ '^\d{4}[A-Za-z]{2,4}$'
  AND (
    account_sequence IS NULL
    OR account_sequence <> SUBSTRING(account_number FROM '^\d{4}')::INTEGER
  );

-- 2) Make generator resilient by using the greater of:
--    - MAX(account_sequence)
--    - MAX(numeric prefix parsed from account_number)
CREATE OR REPLACE FUNCTION next_account_number(p_community VARCHAR)
RETURNS VARCHAR AS $$
DECLARE
    next_seq INTEGER;
BEGIN
    SELECT GREATEST(
               COALESCE(MAX(account_sequence), 0),
               COALESCE(
                   MAX(
                       CASE
                           WHEN account_number ~ '^\d{4}[A-Za-z]{2,4}$'
                               THEN SUBSTRING(account_number FROM '^\d{4}')::INTEGER
                           ELSE NULL
                       END
                   ),
                   0
               )
           ) + 1
    INTO next_seq
    FROM accounts
    WHERE UPPER(community) = UPPER(p_community);

    RETURN LPAD(next_seq::TEXT, 4, '0') || UPPER(p_community);
END;
$$ LANGUAGE plpgsql;
