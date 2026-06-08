# Proactive balance freshness (live SparkMeter pulls)

## Problem this solves
Koios/ThunderCloud readings import on a ~1-day batch. A customer whose meter just hit
zero can run a balance check and still see *yesterday's* positive number, conclude the
system is broken, and not realise they simply need to top up. This layer keeps a
near-real-time per-account balance by pulling the meter's **current credit** from
SparkMeter (a) whenever an account is touched, and (b) on a cadence that escalates as
the account approaches depletion.

It is **refresh-only**: no new SMS/notifications. The existing balance checks and the
existing low-balance alert job simply read a fresher value.

## Components
| Piece | File | Role |
|---|---|---|
| Live lookups | `acdb-api/sparkmeter_credit.py` | `koios_lookup_balance` (v1 `/customers?code`), `tc_lookup_balance` (v0 `/customer/{code}`), `lookup_sm_balance` router (short timeout) |
| Cache + resolver | `acdb-api/balance_live.py` | `account_balance_live` cache, `refresh_balance_live()`, `get_display_balance()` (live when fresh, else `balance_engine`), `mark_account_due()` |
| Cache table | `migrations/040_balance_live_cache.sql` | `account_balance_live` + `balance_live_ttl_s` |
| Schedule state | `migrations/041_balance_refresh_state.sql` | `balance_refresh_state` + tier/cadence/budget config |
| Rate job | `scripts/ops/recompute_consumption_rate.py` | hourly blended kWh/h per account → `cc-balance-rate.timer` |
| Scheduler | `scripts/ops/balance_refresh_scheduler.py` | every 5 min: project depletion, pull due accounts → `cc-balance-refresh.timer` |

## Display value (important)
`live_balance_kwh` = **the meter's current SM balance in kWh** (Koios credit ÷ tariff;
ThunderCloud `credit_balance` is already kWh). The plan's reconciliation
`live = CC_credits - koios_consumption` needs Koios *lifetime* credits, which the v1
endpoint does not return — it returns the balance directly. When CC and Koios credits
agree (every payment is pushed to SparkMeter) that formula reduces to exactly the Koios
balance, and the meter only honours its own balance anyway, so the SM balance is the
honest answer. `cc_balance_kwh` is stored beside it for drift visibility.

## Read path
`payments._balance_payload_for_conn` (SMS gateway `/gateway/balance*`, `/api/payments/balance`),
the portal dashboards in `crud.py`, and `low_balance_alerts.py` all go through
`balance_live`:
- Gateway/portal: `get_display_balance_detail(...)` — **activity-triggered**, TTL-gated
  live pull, adds `balance_source` / `balance_stale` to the payload.
- Low-balance alerts: `get_display_balance(..., refresh=False)` — reads whatever the
  scheduler/activity already cached but **never** triggers a fleet-wide pull (budget).

`mark_account_due()` is called on payment ingest (`ingest._sms_ingest_credit_sm`) and on
ticket creation (`tickets.create_ticket`, `om_tickets.create_om_ticket`) to flag the
account for a prompt scheduler pull.

## Tiers (configurable in `system_config`)
`hours_to_depletion = projected_balance / avg_kwh_per_hour`, balance projected forward
from the last reading at the account's rate (so depletion is anticipated *between* pulls):

| hours left | tier | cadence |
|---|---|---|
| > 24 | 0 | none (activity + daily batch only) |
| 12–24 | 1 | every 2h |
| 6–12 | 2 | every 1h |
| 1–6 | 3 | every 15 min |
| ≤ 1 | 4 | every 5 min |
| depleted (≤0) or idle (rate ≈ 0) | 0 | off urgent list (top-up is activity-triggered) |

Config keys: `balance_refresh_tier_hours`, `balance_refresh_tier_cadence_min`,
`balance_refresh_rate_w_recent` / `_recent_hours` / `_window_hours`,
`balance_live_ttl_s`, `balance_refresh_max_per_run`, `balance_refresh_daily_budget`,
`balance_refresh_bootstrap_per_run`. Each country DB (`onepower_cc`, `onepower_bj`) has
its own `system_config`, so LS and BN can be tuned independently.

## Rate-limit budget
Koios is ~30k requests/day shared with imports. The scheduler:
- counts today's live pulls (`account_balance_live` rows pulled today, which also counts
  activity-triggered pulls) and stops at `balance_refresh_daily_budget`;
- caps each 5-min run at `balance_refresh_max_per_run`, most-urgent-first;
- relies on the `balance_live_ttl_s` cache to dedupe activity vs scheduled pulls.

## Multi-country
Both scripts read `DATABASE_URL` (LS) and `DATABASE_URL_BN` (BN) from `/opt/1pdb/.env`
and run per DB. `lookup_sm_balance` routes by site code: LS Koios, BN Koios (BN org
keys), MAK → ThunderCloud — so the BN pulls draw on the BN Koios budget separately.

## Operating
```bash
# dry-run (no writes / no SM calls beyond what each script does)
python scripts/ops/recompute_consumption_rate.py --dry-run
python scripts/ops/balance_refresh_scheduler.py --dry-run
# single country
python scripts/ops/balance_refresh_scheduler.py --country BN

# timers (installed by deploy.yml)
systemctl list-timers 'cc-balance-*'
journalctl -u cc-balance-refresh -n 100 --no-pager
```

## Failure behaviour
A balance check never hangs: SM lookups use a short timeout; any failure falls back to
the last cached value (flagged `stale`, with `as_of`) or to `balance_engine`. If a meter
is offline (e.g. the MAK ThunderCloud outage) the live pull is stale too — we surface
`stale` + `as_of` rather than implying real-time.
