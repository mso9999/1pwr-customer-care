# Merchant Payment Refresh (Unmatched Payments pipeline)

How merchant-line (PayMerchant) M-Pesa / EcoCash payments get into CC, and how
the refresh is automated so the import never silently lags again.

## Background

Customers pay us two ways on M-Pesa:

- **Pay-bill / SMS flow** — captured automatically in real time by the SMS
  ingest pipeline (`acdb-api/ingest.py`).
- **Merchant line** ("sent to One Power Lesotho Merchant") — these **never reach
  the SMS system**. They only appear in the monthly **merchant statement files**
  that finance downloads from the M-Pesa/EcoCash portals.

So the **Unmatched Payments** portal page (`/unmatched-payments`) is fed by a
**batch import of statement files**, not by a live feed. There is no M-Pesa
merchant API, so true real-time is not possible — the practical goal is to import
each statement promptly and automatically once it lands in Dropbox.

The 2026-06 incident (payments missing after 31 May) happened because the import
had not been run since 12 May. This automation prevents a recurrence.

## Data flow

```
Finance downloads monthly statements
        │
        ▼
Dropbox: "1PWR Financial Records/mobile money records"   (on MSO's Mac)
        │  rsync (data files only)
        ▼
Server: /opt/cc-portal/merchant_exports
        │  backfill_merchant_payments_from_exports.py
        ▼
1PDB:  transactions (matched, booked) + merchant_unmatched_payments (parked)
        │
        ▼
Portal: /unmatched-payments  (O&M links / dismisses parked rows)
```

Statement files are **monthly** and typically appear in Dropbox in the first few
days of the following month (e.g. May files showed up ~1–2 June). There is
normally **no current-month data mid-month** unless someone does an ad-hoc
portal export.

### Scope: MPESA only (EcoCash excluded for now)

The refresh syncs the **MPESA** subtree only (`CC_MM_SUBDIR=MPESA`, the default).
EcoCash statements use a different date format the parser does not yet read
correctly — their dates default to "now", which would park thousands of
mis-dated junk rows. This matches the proven 2026-06-11 baseline (every parked
row's `source_file` was under `.../MPESA/...`).

**TODO before enabling EcoCash:** fix EcoCash date parsing in
`acdb-api/merchant_export_parser.py` (the `MP######.####.A#####` / numeric-ref
EcoCash rows), verify a dry-run shows real paid dates, then set
`CC_MM_SUBDIR=""` to sync the whole folder.

## Manual run

```bash
# Dry-run (no DB writes) — review the report afterwards
scripts/ops/refresh_merchant_unmatched.sh

# Apply — book matched payments + park unmatched ones in 1PDB
scripts/ops/refresh_merchant_unmatched.sh apply
```

Each run rsyncs the Dropbox folder to the server, runs the backfill, and pulls a
timestamped report CSV back to `docs/ops/merchant-refresh-logs/`.

Safety:
- `--no-repair-credit` is always set → never adds kWh / changes balances.
- Booking is idempotent (dedup on `payment_reference`, fuzzy amount+date, and
  `sms_inbound_log`); parked rows dedup on a unique receipt index.
- Conflicts are reported (not auto-resolved) for human follow-up.

Override any path/date without editing the script via env vars: `CC_SSH_KEY`,
`CC_MM_SRC`, `CC_HOST`, `CC_MM_REMOTE`, `CC_BACKEND`, `CC_ENV_FILE`,
`CC_MM_LOGDIR`, `CC_MM_SINCE`.

## Scheduled run (launchd, on the Mac)

The Mac is where the Dropbox folder lives, so the schedule runs here (the server
has no Dropbox access).

Install / update:

```bash
cp "scripts/ops/com.1pwr.cc.merchant-refresh.plist" ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.1pwr.cc.merchant-refresh.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.1pwr.cc.merchant-refresh.plist
launchctl list | grep merchant-refresh        # confirm loaded
```

Run once on demand (to test):

```bash
launchctl start com.1pwr.cc.merchant-refresh
```

Schedule: **Mondays 06:00 local**. If the Mac is asleep/off, launchd runs at the
next wake. Logs: `docs/ops/merchant-refresh-logs/launchd.{out,err}.log` and
`refresh.log`.

### Dry-run vs apply

The plist runs in **dry-run** by default (the last `ProgramArguments` string).
To let the schedule auto-book, change that string to `apply` and reload:

```xml
<string>apply</string>
```

Recommended: keep dry-run until you've reviewed a couple of weekly reports, then
switch to `apply` so new statements import without manual steps.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.1pwr.cc.merchant-refresh.plist
rm ~/Library/LaunchAgents/com.1pwr.cc.merchant-refresh.plist
```
