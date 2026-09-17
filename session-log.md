## 2026-09-17 — Cursor — BN kWh discrepancy: live writer confirmed + 1PDB fix
- Host `import_hourly_bn.py` matches 1PDB: `DO NOTHING`, skip days before `MAX(reading_hour)`, window `$WEEK_AGO`→today. Not reactive energy.
- 1PDB `fix/bn-hourly-completeness`: completeness gate, `DO UPDATE`, 45d→yesterday. Push to 1PDB `main` deploys `/opt/1pdb/services`.
- CC: `docs/ops/bn-koios-cc-consumption-gap-2026-09.md` + CONTEXT BN pipeline pointer. Did not commit `onemeter_validation.py`.

## 2026-09-13 — Cursor — Nexus ownership map registered
- Merged onepowerLS/nexus-portal PR #10 (`5957ca6`) at 20:01 UTC. Docs-only: `docs/CANONICAL_DATA_OWNERSHIP.md` (+11/−4).
- Side effects: Nexus `main` push queued Deploy Nexus Portal run 34779465041 (hosting + rules + safe functions + EC2 frontend of current main). No TDL/functions source in the PR. Did not run `firebase deploy`.
- Left the local Nexus checkout on `fix/tdl-departed-members`; its dirty TDL files were not committed.

## 2026-09-13 — Cursor — Forecast integration-key series (uncommitted)
- Cleaned stale unmetered-branch dirty tree; local `main` now matches `origin/main` (`4d3b404`). Closed April draft PRs #2/#5/#7/#8. Left #15/#16.
- Added read-only `GET /api/integration/forecast/{sites,connections,consumption,collections}` on `X-CC-Integration-Key`. Month-end `customer_commissioned` stock; kWh/connection; billed vs collected; no ARPU. Existing integration payloads unchanged.
- Docs: `docs/FORECAST_INTEGRATION.md`, updated `FORECAST_PROGRAMME_REMAINING.md`, CONTEXT.md, and Nexus `CANONICAL_DATA_OWNERSHIP.md` (other repo, local only).
- Side effects: none in production (not committed/deployed). Closed four stale GitHub PRs.
- Key files: `acdb-api/integration_forecast.py`, `acdb-api/tests/test_integration_forecast.py`, `acdb-api/customer_api.py` (router include only).
- Follow-ups: commit/push when ready (deploys CC); wire uGridPREDICT; enroll unmetered accounts on the host; commit station Repoint Wi-Fi separately.

## 2026-09-09 — Cursor — Commission Generate UX shipped; unmetered PR rebased
- Shipped PR #17 (`3fdf069`) to main. Deploy run 34346560020 succeeded (frontend + backend) ~11:37 UTC. Production now requires/explains the UGP pole/PTB link instead of silently greying Generate.
- Rebased PR #14 (unmetered service) onto that main; folio conflict resolved; tsc + 24 billing tests pass; force-pushed to `cursor/unmetered-service-billing`. MERGEABLE, not merged (new billing product).
- Side effects: cc.1pwrafrica.com frontend+backend updated from `2409e75` to `3fdf069`.
- Key files: commission page/i18n/folio; unmetered module remains on PR #14
- Follow-ups: tell CC to refresh and retry (link pole/PTB on Details). Merge PR #14 when ready to enroll unmetered accounts. PR #16 SW and OTA leftovers left alone.

## 2026-09-09 — Cursor — Unmetered service shipped to production
- Merged PR #14 (`12f8567`) to main. Deploy run 34346995952 succeeded (frontend + backend) ~11:42 UTC.
- Production changed from `3fdf069` (commission UGP UX only) to `12f8567` (unmetered service ledger + UI + monthly accrual timer).
- Side effects: cc.1pwrafrica.com live; migration `068_unmetered_service.sql` applied by backend deploy; `cc-unmetered-accrual` timer should now be installed.
- Follow-ups: ops must enroll known unmetered-connected accounts; first accrual is 1st of next month 02:30 UTC; confirm timer with `systemctl list-timers | grep unmetered` on the CC host.

## 2026-09-09 — Cursor — Help + Tutorial for UGP commission and unmetered service
- Merged PR #18 (`85a5e39`). Push event did not start Actions; dispatched Deploy CC Portal run 34348001262 on main.
- Help: Unmetered Service section, feature-map row, payment-split + tariff notes. Tutorial: lifecycle UGP step, new unmetered walkthrough, Commerce/Payments mentions.
- Side effects: cc.1pwrafrica.com frontend+backend redeployed from `12f8567` to `85a5e39` (docs/i18n). Run 34348001262 succeeded.

## 2026-09-09 — Cursor — Commission 422 GPS type mismatch
- LS CC 0241MAS: Generate worked after UGP link, then 422 `gps_lat/gps_lng: Input should be a valid string`. Customer GPS stored as numeric; JSON sent numbers; Pydantic Optional[str] rejected them.
- Fix PR #19: coerce GPS to string on execute + lookup + frontend submit. Tests pass.
- Side effects: deploy run 34352428607 succeeded. Production now accepts numeric GPS on `/api/commission/execute`.

## 2026-09-08 — Cursor — Forecast remaining-work instructions
- Added `docs/FORECAST_PROGRAMME_REMAINING.md`. CC must expose `customer_commissioned` (and named siblings) plus consumption/collections to uGridPREDICT via a non-interactive integration key. Employee JWT is not usable by the forecast service.
- Side effects: none (docs only).
