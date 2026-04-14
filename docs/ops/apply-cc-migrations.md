# Applying Customer Care SQL migrations (1PDB)

Migrations live under `acdb-api/migrations/` in this repo.

## Automatic apply (production)

On push to **`main`**, the **deploy-backend** job (`.github/workflows/deploy.yml`) applies **incremental** migrations only: files matching **`0[1-9][0-9]_*.sql`** (i.e. **010+**, e.g. `010_customers_commissioning_contract_flags.sql`). It runs each file with **`sudo -u postgres psql -d onepower_cc`** (Lesotho) and, if the **`onepower_bj`** database exists on the host, the same files against **`onepower_bj`** (Benin), **before** restarting `1pdb-api` / `1pdb-api-bn`. The app role `cc_api` is **not** used for `ALTER TABLE` here because it is not the table owner.

**Why not 001–009 in CI:** Older scripts (e.g. `DROP COLUMN`) require **table owner** privileges; the app role `cc_api` is not the owner — those were one-time DBA/ops applies.

**Root cause this fixes:** API code referenced columns that existed only in repo migrations, not in live 1PDB. Incremental deploy applies **010+** automatically.

For a **full** re-apply of every file in `migrations/` (e.g. new dev DB), use `apply_migrations.sh` manually as a superuser — see below.

If a migration fails, the deploy step fails — fix SQL or DB state, then redeploy.

## When to apply

- After pulling changes that add a new `NNN_*.sql` file (e.g. `010_customers_commissioning_contract_flags.sql`).
- If the portal returns **500** on `/api/commission/execute` with a message about **migration 010**, the Lesotho or Benin database is missing those columns.

## Apply on the CC host (typical)

**SSH key (human Mac):** **`/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem`** — see `CONTEXT.md` → Manual Access. Resolve `<current-cc-linux-host>` with **`aws ec2 describe-instances`** or GitHub **`EC2_LINUX_HOST`**.

On the server, prefer the **postgres** OS user (matches deploy and avoids owner errors):

```bash
# Lesotho
sudo -u postgres psql -d onepower_cc -v ON_ERROR_STOP=1 \
  -f /opt/cc-portal/backend/migrations/010_customers_commissioning_contract_flags.sql

# Benin (if database exists)
sudo -u postgres psql -d onepower_bj -v ON_ERROR_STOP=1 \
  -f /opt/cc-portal/backend/migrations/010_customers_commissioning_contract_flags.sql
```

Alternatively, `psql "$DATABASE_URL"` works if the connection role is a superuser or table owner.

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
