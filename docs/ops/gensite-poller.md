# Gensite poller — install, operate, troubleshoot

> Status: ready-to-install. Ships as part of the backend rsync (both the
> script and the systemd units). First target site is **GBO** (Victron VRM).

## What it does

- Runs every 60 s as a oneshot via `cc-gensite-poll.timer`.
- Enumerates `site_credentials`, filters by per-vendor cadence, calls
  `adapter.fetch_live()` + (every 5 min) `adapter.fetch_alarms()`.
- Writes rows to `inverter_readings` / `inverter_alarms` in 1PDB.
- Maintains per-credential state in `/var/lib/cc-gensite-poll/state.json`
  so alerts only fire on **transitions** (offline / recovery / new CRITICAL
  alarm), never on every run.
- Routes alerts to the country-appropriate WhatsApp bridge using the
  existing `cc_bridge_notify.py` helper → CC phone + `1PWR LS - OnM Ticket
  Tracker` group (LS sites), or the BN equivalents.

## One-time install on the CC host

The script itself deploys automatically with the backend rsync (under
`/opt/cc-portal/backend/scripts/ops/gensite_poller.py`). The systemd units do
**not** — install once, manually:

```bash
# As ubuntu on the CC host
sudo install -o root -g root -m 644 \
  /opt/cc-portal/backend/deploy/systemd/cc-gensite-poll.service \
  /etc/systemd/system/cc-gensite-poll.service
sudo install -o root -g root -m 644 \
  /opt/cc-portal/backend/deploy/systemd/cc-gensite-poll.timer \
  /etc/systemd/system/cc-gensite-poll.timer

sudo systemctl daemon-reload
sudo systemctl enable --now cc-gensite-poll.timer
```

Wait — the backend rsync drops files under `/opt/cc-portal/backend/` and the
`deploy/` directory isn't under `acdb-api/`. For now copy the units from the
Dropbox checkout or from the repo on the deploy runner:

```bash
# From a workstation with the repo checked out
scp -i "/path/EOver.pem" \
    deploy/systemd/cc-gensite-poll.service \
    deploy/systemd/cc-gensite-poll.timer \
    ubuntu@<cc-host>:/tmp/
# Then on the host:
sudo mv /tmp/cc-gensite-poll.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cc-gensite-poll.timer
```

Verify:

```bash
systemctl list-timers cc-gensite-poll.timer
journalctl -u cc-gensite-poll.service -n 50 --no-pager
```

First useful run requires:

- `CC_CREDENTIAL_ENCRYPTION_KEY` set in `/opt/1pdb/.env`
  (see `docs/ops/gensite-credentials.md`)
- At least one `site_credentials` row with a non-stub adapter
  (currently: Victron only)

Without either, the poller logs the reason and exits 0 or 2.

## Sanity-check it works

Run once on the host without affecting state, before relying on the timer:

```bash
sudo -u cc_api \
  GENSITE_DRY_RUN=1 \
  /opt/cc-portal/backend/venv/bin/python \
  /opt/cc-portal/backend/scripts/ops/gensite_poller.py
```

Expected: one log line per credential — either `OK, N reading(s)` or a
well-formed adapter error.

When the timer is enabled, confirm one real run has written to the DB:

```sql
SELECT site_code, MAX(ts_utc)
FROM inverter_readings
GROUP BY site_code;
```

and on the portal visit `https://cc.1pwrafrica.com/gensite/<CODE>`. Tiles
should populate within 60–120 seconds of commissioning a site.

## Operational behaviour

| Aspect | Behaviour |
|---|---|
| Live cadence | Per-vendor: Victron 60 s, Solarman/Deye 5 min, Sinosoar 2 min, SMA 10 min |
| Alarm cadence | 5 min for all vendors; only runs after a successful live poll |
| Backoff on failure | Next poll delayed `cadence × min(2^consecutive_failures, 30)` seconds |
| Offline alert | After `POLL_FAIL_ALERT_THRESHOLD` (default 3) consecutive failures; once only until recovery |
| Recovery alert | Sent once when a credential succeeds after having alerted offline |
| CRITICAL alarm alert | Sent once per unique `(equipment, vendor_code, raised_at)` |
| State file | `/var/lib/cc-gensite-poll/state.json`, owned by `cc_api` via `StateDirectory=` in the unit |
| Stub adapters | Sinosoar / Deye / SMA are skipped with a debug log until their adapter is implemented |

## Environment knobs

All read from `/opt/1pdb/.env` (systemd `EnvironmentFile=`). Per-host overrides
can go in `/etc/default/cc-gensite-poll`.

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | required (already set for `1pdb-api`) | |
| `CC_CREDENTIAL_ENCRYPTION_KEY` | required | See `docs/ops/gensite-credentials.md` |
| `CC_BRIDGE_NOTIFY_URL_{LS,BN}` / `CC_BRIDGE_SECRET_{LS,BN}` | per-country bridges | Already set for 1Meter monitor |
| `POLL_FAIL_ALERT_THRESHOLD` | 3 | Consecutive failures before alerting |
| `STATE_FILE` | `/var/lib/cc-gensite-poll/state.json` | |
| `GENSITE_DRY_RUN` | unset | Set to `1` for a safe dry run |

## Troubleshooting

- **Timer enabled but no DB rows appear.** Check `journalctl -u cc-gensite-poll.service`.
  Most common causes: missing Fernet key, all adapters still stubs, no rows
  in `site_credentials` yet.
- **`key_is_configured() == False`.** The poller exits 2 immediately. Fix by
  setting `CC_CREDENTIAL_ENCRYPTION_KEY` in `/opt/1pdb/.env` and
  `sudo systemctl restart 1pdb-api 1pdb-api-bn` (the APIs share the same env).
- **Adapter error repeated for one vendor only.** `site_credentials.last_verify_error`
  gets populated on every failed poll — check the site's dashboard or:
  ```sql
  SELECT site_code, vendor, backend, last_verified_ok, last_verify_error
    FROM site_credentials
    WHERE last_verified_ok IS FALSE;
  ```
- **Want to force a fresh verify without waiting for the timer.** Use the
  **Test connection** button on the `/gensite/{code}` page — it calls
  `POST /api/gensite/sites/{code}/credentials/{vendor}/{backend}/verify`
  which does the same auth check without depending on the poller.

## Related

- `docs/ops/gensite-commissioning.md` — operator flow that produces the rows
  this poller consumes.
- `docs/ops/gensite-credentials.md` — Fernet key setup/rotation.
- `scripts/ops/monitor_1meter_offline.py` — the blueprint for this poller's
  state-file / transition-only alerting model.
