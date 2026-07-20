-- Migration 052: Site metadata extensions for investor-grade asset register
-- Stores PV/battery/thermal capacity, commissioning dates, concession expiry, etc.
-- Seeded from the CC/1PDB Analytics Tool Spec site mapping table.

BEGIN;

CREATE TABLE IF NOT EXISTS site_metadata (
    site_code VARCHAR(3) PRIMARY KEY,
    full_name VARCHAR(255),
    country VARCHAR(2),
    region VARCHAR(255),
    status VARCHAR(50) DEFAULT 'Operational',
    commissioning_date DATE,
    pv_kwp NUMERIC(10,2),
    battery_kwh NUMERIC(10,2),
    thermal_kw NUMERIC(10,2),
    concession_expiry DATE,
    metering_tech VARCHAR(100) DEFAULT 'SparkMeter prepaid',
    concession_permit VARCHAR(255),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed from spec site mapping + country_config.py
INSERT INTO site_metadata (site_code, full_name, country, region, status)
VALUES
    ('MAK', 'Ha Makebe',       'LS', 'Maseru',         'Operational'),
    ('MAS', 'Mashai',          'LS', 'Thaba-Tseka',    'Operational'),
    ('SHG', 'Sehonghong',      'LS', 'Thaba-Tseka',    'Operational'),
    ('LEB', 'Lebakeng',        'LS', 'Qacha''s Nek',   'Operational'),
    ('SEH', 'Sehlabathebe',    'LS', 'Qacha''s Nek',   'Operational'),
    ('MAT', 'Matsoaing',       'LS', 'Mokhotlong',     'Operational'),
    ('TLH', 'Tlhanyaku',       'LS', 'Mokhotlong',     'Operational'),
    ('TOS', 'Tosing',          'LS', 'Quthing',        'Energising'),
    ('SEB', 'Sebapala',        'LS', 'Quthing',        'Construction'),
    ('RIB', 'Ribaneng',        'LS', 'Mafeteng',       'Energising'),
    ('KET', 'Ketane',          'LS', 'Mohale''s Hoek', 'Energising'),
    ('LSB', 'Lets''eng-la-Baroa', 'LS', NULL,          'Pipeline'),
    ('NKU', 'Ha Nkau',         'LS', 'Maseru',         'Operational'),
    ('MET', 'Methalaneng',     'LS', 'Thaba-Tseka',    'Operational'),
    ('BOB', 'Bobete',          'LS', 'Thaba-Tseka',    'Operational'),
    ('MAN', 'Manamaneng',      'LS', 'Thaba-Tseka',    'Operational'),
    ('GBO', 'Gbegbowele',      'BN', 'Zou',            'Construction'),
    ('SAM', 'Samionta',        'BN', 'Zou',            'Construction')
ON CONFLICT (site_code) DO NOTHING;

COMMIT;
