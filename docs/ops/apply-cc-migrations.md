# Applying Customer Care SQL migrations (1PDB)

Migrations live under `acdb-api/migrations/` in this repo. They are **not** always run automatically on deploy: the GitHub Actions job rsyncs Python files and restarts the API but does **not** execute SQL against production unless you add that step.

## When to apply

- After pulling changes that add a new `NNN_*.sql` file (e.g. `010_customers_commissioning_contract_flags.sql`).
- If the portal returns **500** on `/api/commission/execute` with a message about **migration 010**, the Lesotho or Benin database is missing those columns.

## Apply on the CC host (typical)

**SSH key (human Mac):** Use **`/Users/mattmso/Dropbox/AI Projects/PEMs/EOver.pem`** — see `CONTEXT.md` → Manual Access. Resolve `<current-cc-linux-host>` from AWS or `EC2_LINUX_HOST`.

Use the same `DATABASE_URL` (or role) the `1pdb-api` service uses. Example:

```bash
# Lesotho (adjust path and connection string)
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /opt/cc-portal/backend/migrations/010_customers_commissioning_contract_flags.sql

# Benin API database (separate DSN / service)
# psql "$DATABASE_URL_BN" -v ON_ERROR_STOP=1 -f ...
```

`IF NOT EXISTS` in the migration makes re-runs safe.

## Verify

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'customers'
  AND column_name IN (
    'customer_commissioned', 'customer_commissioned_date',
    'contract_signed', 'contract_signed_date'
  );
```

After columns exist, restart the API if needed (`sudo systemctl restart 1pdb-api`) and retry commissioning.

## Related code

- `acdb-api/commission.py` — `POST /api/commission/execute` updates these columns after successful PDF generation.
