-- Zambia country-lane defaults. This migration runs on every country DB but
-- mutates only the dedicated production Zambia database.
DO $$
BEGIN
    IF current_database() <> 'onepower_zm' THEN
        RETURN;
    END IF;

    ALTER TABLE customers
        ALTER COLUMN country SET DEFAULT 'Zambia';

    INSERT INTO system_config (key, value, description)
    VALUES
        ('country_code', 'ZM', 'ISO 3166-1 alpha-2 operating country'),
        ('country_name', 'Zambia', 'Operating country name'),
        ('currency', 'ZMW', 'ISO 4217 ledger currency'),
        ('tariff_rate', '0', 'ZMW per kWh; zero keeps electricity payments disabled'),
        ('connection_fee_amount', '0', 'ZMW; configure only after commercial approval'),
        ('readyboard_fee_amount', '0', 'ZMW; configure only after commercial approval')
    ON CONFLICT (key) DO UPDATE
       SET value = EXCLUDED.value,
           description = EXCLUDED.description,
           updated_at = NOW(),
           updated_by = '060_zambia_country_bootstrap';
END
$$;
