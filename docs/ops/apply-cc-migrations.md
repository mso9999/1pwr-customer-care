# Applying Customer Care SQL migrations (1PDB)

Migrations live under `acdb-api/migrations/` in this repo.

## Automatic apply (production)

On push to **`main`**, the **deploy-backend** job (`.github/workflows/deploy.yml`) runs `migrations/apply_migrations.sh` on the CC host **after** rsync and **before** restarting `1pdb-api` / `1pdb-api-bn`. It sources `/opt/1pdb/.env` (Lesotho → `onepower_cc`) and `/opt/1pdb-bn/.env` (Benin → `onepower_bj`) and applies every `*.sql` file in **sorted order**.

**Root cause this fixes:** API code referenced columns (e.g. commissioning flags) that existed only in repo migrations, not in live 1PDB — causing 500s and aborted transactions. Deploy now keeps schema aligned with code.

If a migration fails, the deploy step fails — fix SQL or DB state, then redeploy.

## When to apply

- After pulling changes that add a new `NNN_*.sql` file (e.g. `010_customers_commissioning_contract_flags.sql`).
- If the portal returns **500** on `/api/commission/execute` with a message about **migration 010**, the Lesotho or Benin database is missing those columns.

## Apply on the CC host (typical)

**SSH key (human Mac):** **`/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem`** — see `CONTEXT.md` → Manual Access. Resolve `<current-cc-linux-host>` with **`aws ec2 describe-instances`** or GitHub **`EC2_LINUX_HOST`**.

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
