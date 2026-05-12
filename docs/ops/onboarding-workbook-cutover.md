# Onboarding workbook cutover

The Dropbox workbook `Customer Onboarding Data_Jan-01, 2026.xlsx` is retired as production truth after CC import and validation. 1PDB commissioning flags, payment verifications, and CC onboarding dashboards are canonical.

## Preconditions

- Lesotho SMP balance cutover is stable (`docs/ops/smp-1pdb-cutover.md`).
- Migration `027_onboarding_workbook_extras.sql` applied on 1PDB.
- Finance sign-off on dry-run reconciliation CSV.

## Host steps (CC API host)

```bash
export DATABASE_URL=...
export PYTHONPATH=/opt/cc-portal/backend

psql "$DATABASE_URL" -f /opt/cc-portal/backend/migrations/027_onboarding_workbook_extras.sql

python3 /opt/cc-portal/backend/../scripts/ops/import_onboarding_workbook.py \
  --workbook /path/to/Customer\ Onboarding\ Data_Jan-01,\ 2026.xlsx \
  --report-csv /tmp/onboarding_import_dryrun.csv

python3 /opt/cc-portal/backend/../scripts/ops/derive_onboarding_payment_steps.py

python3 /opt/cc-portal/backend/../scripts/ops/import_onboarding_workbook.py \
  --workbook /path/to/Customer\ Onboarding\ Data_Jan-01,\ 2026.xlsx \
  --report-csv /tmp/onboarding_import_apply.csv \
  --apply
```

## Validation

- Compare `/api/onboarding/dashboard/summary` and `/api/om-report/pipeline` to workbook **Totals** / **Progress Dashboard** for each site (ALL, MAT, TLH, MAK, SHG, MAS, SEH, KET, LSB).
- Sample audit per site: commissioning steps, fee verification rows (`mm:%` + workbook txn IDs), MAK proof URLs on `payment_proofs.external_url`.
- Confirm uGridPlan commissioning sync still receives `customer_commissioned` dates from 1PDB.

## Spreadsheet freeze

- Set the Dropbox workbook read-only and announce cutover in O&M channels.
- Onboarding edits happen in CC: customer detail **Onboarding** panel, **Pipeline** drill-down, **Payment verification**, and **Onboarding dashboard**.

## Precedence

`payment_status_override` > verified fee rows > commissioning step flags > raw transaction sums > one-time workbook import (`onboarding_import_2026-01`).
