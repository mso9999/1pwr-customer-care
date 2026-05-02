# Monthly Staff PIN Rotation

> **Why this doc exists**: 2026-05-01 the entire LS team got locked out of CC at 02:00 SAST because the date-based staff PIN silently rotated at 00:00 UTC. This page documents the root cause, the protective layers we added, and how ops should run them.

## How the PIN works

CC employee login is **two-factor by design**:

1. **Knowledge factor**: a shared monthly **staff PIN** computed from `YYYYMM / reverse(YYYYMM)` (first 4 significant digits of the quotient). Pure function in `acdb-api/auth.py::date_password_for(year, month)`.
2. **Identity factor**: `employee_id` cross-referenced against the HR portal at `lookup_employee()` for name/email/role enrichment. (HR being unreachable doesn't block login -- the system falls through to "limited info" mode -- but the PIN is always required.)

The PIN is shared across the team on purpose: it's a defense-in-depth gate so a leaked employee ID alone can't grant access. We are **keeping** this layer; the only thing wrong with the original design was zero notification when it rotated.

| Month | PIN |
|---|---|
| Apr 2026 | `4987` |
| May 2026 | `4002` |
| Jun 2026 | `3342` |

(Compute any month with `python3 -c "from auth import date_password_for; print(date_password_for(2026, 7))"`.)

## What we added (2026-05-01)

| Layer | Where | What it does |
|---|---|---|
| Bridge `/broadcast` route | `whatsapp-bridge/whatsapp-customer-care.js` | Sends a verbatim text to the discovered ticket-tracker group, no `[App / meter relay]` prefix. |
| Python helper | `acdb-api/cc_bridge_notify.py::broadcast_to_bridge()` | Posts to `/broadcast` for any country (resolves URL/secret per-country). |
| PIN broadcast module | `acdb-api/auth_pin_broadcast.py` | `compose_pin_message(year, month)` + `broadcast_pin_for_active_countries()`. Always includes next month's PIN as advance notice. |
| Ops script | `scripts/ops/broadcast_monthly_pin.py` | CLI wrapper: `--country LS` / `--dry-run` / `--year/--month`. |
| systemd unit | `scripts/ops/cc-auth-pin-broadcast.{service,timer}` | Runs the script at 04:00 UTC on the 1st of every month. |
| Manual trigger | `POST /api/admin/auth/broadcast-pin` (superadmin) | In-portal fallback when the timer is down. UI panel on `/admin/roles`. |
| Login UX hint | `frontend/src/pages/LoginPage.tsx` | "The staff PIN rotates on the 1st of each month..." next to the password field (employee mode only). |
| Friendly 401 | `acdb-api/auth.py::employee_login` | If the wrong PIN is given during the first 7 days of a month, the 401 detail says "the PIN rotates on the 1st" instead of just "Invalid credentials". The PIN itself is never echoed. |

## One-time install on the production CC host

The systemd unit and timer ship in the repo but aren't installed automatically (they only need to live on whichever host runs the WhatsApp bridge for that country).

```bash
ssh ubuntu@<cc-linux-host>
sudo install -m 644 -o root -g root \
    /opt/cc-portal/backend/scripts/ops/cc-auth-pin-broadcast.service \
    /etc/systemd/system/cc-auth-pin-broadcast.service
sudo install -m 644 -o root -g root \
    /opt/cc-portal/backend/scripts/ops/cc-auth-pin-broadcast.timer \
    /etc/systemd/system/cc-auth-pin-broadcast.timer
sudo systemctl daemon-reload
sudo systemctl enable --now cc-auth-pin-broadcast.timer
systemctl list-timers cc-auth-pin-broadcast.timer
```

The unit reads its environment from `/opt/cc-portal/backend/.env` and `/etc/default/cc-portal`; make sure `CC_BRIDGE_NOTIFY_URL[_LS]` and `CC_BRIDGE_SECRET[_LS]` are set there (same vars used by the API for ticket-tracker pings).

## Re-broadcasting on demand

Three equivalent ways:

```bash
# 1. CLI on the API host
sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \
    /opt/cc-portal/backend/scripts/ops/broadcast_monthly_pin.py

# 2. Trigger the systemd unit immediately (uses the timer's env)
sudo systemctl start cc-auth-pin-broadcast.service
journalctl -u cc-auth-pin-broadcast.service -n 50

# 3. From the portal (superadmin only): /admin/roles -> "Broadcast PIN to WhatsApp"
```

## Verifying without sending

```bash
# Print the message that would go out, no HTTP calls
sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \
    /opt/cc-portal/backend/scripts/ops/broadcast_monthly_pin.py --dry-run

# Preview an arbitrary month
.../broadcast_monthly_pin.py --year 2026 --month 12 --dry-run
```

The portal's preview panel (`GET /api/admin/auth/pin-preview`, rendered on `/admin/roles`) shows the same rendered text the broadcast would send, with the live target country list.

## What to do if the broadcast fails

1. Check the timer ran: `systemctl list-timers cc-auth-pin-broadcast.timer`.
2. Check the unit log: `journalctl -u cc-auth-pin-broadcast.service -n 100`.
3. Common causes (each surfaces as `bridge_broadcast country=XX failed: ...` in the log):
   - **Bridge process down**: `pm2 restart whatsapp-cc` on the bridge host.
   - **Tracker JID not yet discovered after a fresh bridge install**: bridge logs show `[DISCOVER] Group not found` -- add the CC phone to the "1PWR LS - OnM Ticket Tracker" group, restart bridge.
   - **Missing env vars on the API host**: confirm `CC_BRIDGE_NOTIFY_URL_LS` (or unsuffixed) is set in `/etc/default/cc-portal` or `/opt/cc-portal/backend/.env`.
4. As a last resort, broadcast manually from `/admin/roles` -- it uses the same code path so the same error will surface in the portal toast.

## Long-term direction (out of scope for now)

The PIN rotation + broadcast keeps the team unblocked, but per-employee credentials would be a stronger model. Track in a future session under "Replace shared monthly PIN with per-employee credentials" if/when prioritised.
