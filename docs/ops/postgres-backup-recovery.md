# PostgreSQL Backup And Recovery

## Scope

This runbook covers the Customer Care production databases and the remaining
SQLite auth store:

- `onepower_cc`
- `onepower_bj`
- `/opt/cc-portal/backend/cc_auth.db`

It documents the live backup job on the production CC host and the restore
verification flow that must run on a disposable restore environment rather than
on the production host itself.

## Current Live State

- Production CC host: `EOL`
- EC2 instance: `i-04291e12e64de36d7` in `af-south-1`
- Host-level DR: EBS snapshots via DLM on the `backup=yes` tag
- Logical backup bucket: `s3://1pwr-cc-backups-758201218523-af-south-1`
- S3 prefix: `customer-care/ip-172-31-3-91/`
- Production backup timer: `cc-postgres-backup.timer`
- Production backup script: `/usr/local/bin/cc_postgres_backup.sh`
- Production backup config: `/etc/default/cc-postgres-backup`

## What The Backup Job Does

Each run on the production host:

1. Reads `DATABASE_URL` from:
   - `/opt/1pdb/.env`
   - `/opt/1pdb-bn/.env`
2. Runs `pg_dump --format=custom --compress=9` for:
   - `onepower_cc`
   - `onepower_bj`
3. Creates a consistent SQLite backup with:
   - `sqlite3 /opt/cc-portal/backend/cc_auth.db ".backup ..."`
4. Writes artifacts locally to:
   - `/var/backups/1pwr-cc/<UTC timestamp>/`
5. Uploads the directory to S3.
6. Prunes local backup directories older than `LOCAL_RETENTION_DAYS` (currently `7`).

Per-run artifacts:

- `onepower_cc.dump`
- `onepower_bj.dump`
- `cc_auth.db.backup`
- `manifest.json`
- `*.sha256`

## Production Commands

Manual backup run on the production host:

```bash
sudo systemctl start cc-postgres-backup.service
sudo systemctl status cc-postgres-backup.service --no-pager
sudo journalctl -u cc-postgres-backup.service -n 50 --no-pager
```

Check the timer:

```bash
sudo systemctl status cc-postgres-backup.timer --no-pager
systemctl list-timers cc-postgres-backup.timer
```

List the latest uploaded objects from a machine with AWS access:

```bash
aws s3 ls \
  s3://1pwr-cc-backups-758201218523-af-south-1/customer-care/ip-172-31-3-91/ \
  --recursive
```

## Restore Verification Boundary

Do **not** run the full restore drill on the production CC host.

Why:

- The production root volume was expanded to `120 GiB` on `2026-04-02`, but the host still should not be used as the restore target.
- A real restore needs large free disk and can create heavy I/O, temporary database growth, and PostgreSQL recovery pressure on the live service.
- Running the drill on the live host creates avoidable contention with the
  production PostgreSQL service.

The restore drill must run on a disposable restore host or workstation with:

- PostgreSQL server/client installed
- SQLite installed
- AWS CLI configured (instance role is preferred)
- At least `~120 GiB` free disk

The restore script is intentionally kept in-repo and should be installed only on
that disposable restore environment:

- `scripts/ops/cc_postgres_restore_verify.sh`

## Disposable Restore Host Procedure

Reference shape used successfully on `2026-04-01`:

- Ubuntu 24.04 EC2
- `t3.medium`
- `120 GiB` gp3 root disk
- same key pair as the CC host (`EOver`)
- IAM instance profile: `cc-postgres-backup-profile`

Provisioning outline:

```bash
sudo apt-get update
sudo apt-get install -y postgresql postgresql-client sqlite3 unzip curl
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
rm -rf /tmp/aws
unzip -q /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install --update
sudo systemctl start postgresql
```

Install the restore script and minimal env file:

```bash
sudo install -m 750 cc_postgres_restore_verify.sh /usr/local/bin/cc_postgres_restore_verify.sh
printf '%s\n' \
  'AWS_REGION=af-south-1' \
  'S3_BUCKET=1pwr-cc-backups-758201218523-af-south-1' \
  'S3_PREFIX=customer-care' \
  'BACKUP_ROOT=/var/backups/1pwr-cc' \
  'HOST_NAME=ip-172-31-3-91' \
  | sudo tee /etc/default/cc-postgres-backup >/dev/null
sudo chmod 600 /etc/default/cc-postgres-backup
```

Run a restore drill for a specific backup timestamp:

```bash
sudo bash -lc '
  set -a
  . /etc/default/cc-postgres-backup
  set +a
  /usr/local/bin/cc_postgres_restore_verify.sh 20260401T210624Z
'
```

Expected outputs:

- local report under `/var/backups/1pwr-cc/<timestamp>/restore-verify-*.json`
- matching report uploaded back into the same S3 backup prefix
- no leftover `cc_restore_*` databases

## Latest Verified Drill

Backup timestamp:

- `20260401T210624Z`

Uploaded report:

- `s3://1pwr-cc-backups-758201218523-af-south-1/customer-care/ip-172-31-3-91/20260401T210624Z/restore-verify-20260401211803.json`

Verified contents:

- SQLite integrity: `ok`
- LS restore:
  - `customers=1465`
  - `accounts=1464`
  - `meters=1735`
  - `cc_mutations=1950`
- BN restore:
  - `customers=165`
  - `accounts=165`
  - `meters=162`
  - `cc_mutations=1950`

## Audit Ledger Cutover

The mutation ledger is now PostgreSQL-backed per country database.

Files:

- migration: `acdb-api/migrations/005_create_cc_mutations.sql`
- runtime ledger: `acdb-api/mutations.py`
- one-time backfill helper: `scripts/ops/backfill_cc_mutations.py`

Cutover notes:

- Both country databases now have their own `cc_mutations` table.
- The historical SQLite `cc_mutations` data was backfilled into both databases
  during the cutover because the old SQLite ledger was shared by both country
  services.
- `cc_employee_roles` and `cc_customer_passwords` remain in SQLite for now.
- New PostgreSQL-backed business writes now record audit rows in the same store.

## Ownership Boundary

- The live CC backup job currently sits on the production CC host because this
  repo was the available workspace during the hardening work.
- The long-term owner of database-native backup automation should be the
  `1PDB` ops/repo layer, not the CC app repo.
- The CC deploy path still does **not** run SQL migrations automatically. Apply
  database migrations explicitly before or alongside runtime deploys.
