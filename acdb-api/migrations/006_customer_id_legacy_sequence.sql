BEGIN;

CREATE SEQUENCE IF NOT EXISTS customers_legacy_id_seq START WITH 6728;

ALTER TABLE customers
    ALTER COLUMN customer_id_legacy SET DEFAULT nextval('customers_legacy_id_seq');

UPDATE customers
   SET customer_id_legacy = nextval('customers_legacy_id_seq')
 WHERE customer_id_legacy IS NULL;

COMMIT;
