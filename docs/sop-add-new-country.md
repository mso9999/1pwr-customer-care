# SOP: Adding a new country to Customer Care (e.g. Zambia)

This procedure extends the **multi-country architecture** described in `CONTEXT.md`: one PostgreSQL database and one FastAPI process per country, same codebase, unified frontend at `cc.1pwrafrica.com`. Use it when onboarding a new operating country (example: **Zambia, `ZM`**).

**Principles**

- **1PDB** remains the single source of truth **per country**; do not merge unrelated countries into one DB for convenience.
- **No shared backend code paths** that silently mix currencies or payment providers—configure explicitly.
- **Per-country data isolation is mandatory**: every job that writes into a country DB must resolve credentials/org **by country** and **filter by that country's sites** (see next section). This is the rule that prevents cross-DB pollution.
- **uGridPlan** and other integrations stay **HTTP-only**; no shared libraries across repos.

---

## Cross-country data isolation (read first — prevents cross-DB pollution)

**Why this section exists:** In 2026-06 the Benin SparkMeter-credit mirror imported ~660 **Lesotho** Koios accounts (1,766 txns, ~77k kWh) into the **Benin** database `onepower_bj`. Root cause: a per-country job read a **single global** `KOIOS_ORG_ID` (and `KOIOS_WEB_EMAIL`/`KOIOS_WEB_PASSWORD`) instead of the Benin-specific org, **and** the importer had no site guard, so it pulled the LS org's payments and wrote them into the BN DB every 15 min. See `SESSION_LOG` 2026-06-08 and the fix in `scripts/ops/import_sm_manual_credits.py`. Two non-negotiable rules avoid this for every new country:

### Rule 1 — Resolve credentials / org / DB PER COUNTRY (never a bare global)

Any SparkMeter / Koios / DB resolver inside a multi-country job must select by the job's country code `cc`, preferring a **country-suffixed** env var and using `country_config` as the authority. Pattern:

```python
from country_config import get_country
org_id = (
    os.environ.get(f"KOIOS_ORG_ID_{cc}")
    or get_country(cc).koios_org_id
    or os.environ.get("KOIOS_ORG_ID", "")   # bare global: single-country hosts only
)
```

Per-country env conventions (suffix = ISO code, e.g. `_ZM`):

| Purpose | Country-suffixed var (required on shared host) | Fallback (single-country only) |
|---|---|---|
| DB DSN | `DATABASE_URL_{CC}` | `DATABASE_URL` (the active country) |
| Koios org id | `KOIOS_ORG_ID_{CC}` | `country_config.koios_org_id` |
| Koios read key / secret | `KOIOS_API_KEY_{CC}` / `KOIOS_API_SECRET_{CC}` | global read key |
| Koios manage key / secret | `KOIOS_MANAGE_API_KEY_{CC}` / `_SECRET_{CC}` | read key |
| Koios write key / secret | `KOIOS_WRITE_API_KEY_{CC}` / `_SECRET_{CC}` | global write key |
| Koios web login (scrape) | `KOIOS_WEB_EMAIL_{CC}` / `KOIOS_WEB_PASSWORD_{CC}` | `KOIOS_WEB_EMAIL` / `KOIOS_WEB_PASSWORD` |

A bare global fallback is only safe on a **single-country** host. On the shared multi-country host, **always** set the suffixed vars so a fallback can never silently resolve to another country's org/DB.

### Rule 2 — Site guard before every insert

Any importer that writes accounts/transactions into a country DB MUST skip rows whose site code is not one of that country's sites (`country_config.get_country(cc).site_abbrev`). Defense-in-depth: even a mis-set org or credential then cannot cross-insert. Example (now in `import_sm_manual_credits.fetch_koios_credits`):

```python
allowed_sites = {s.upper() for s in get_country(cc).site_abbrev}
# ... per row, after resolving the account ...
if allowed_sites and _site_code(account) not in allowed_sites:
    continue  # not a customer of this country's sites — never cross-insert
```

### Multi-country jobs that MUST honor both rules

- `scripts/ops/import_sm_manual_credits.py` (SM→CC credit mirror) — fixed 2026-06 (per-country org + site guard).
- `scripts/ops/run_sm_credit_mirror_incremental.py` — the per-country job list is **hardcoded** (`LS`, `BN`). Adding a country requires adding e.g. `("ZM", "koios", db_zm)` and a `DATABASE_URL_ZM` in `/opt/1pdb/.env`.
- `scripts/ops/audit_ls_balances.py` / `audit_bn_balances.py` — one audit per country; the new country's audit must target **its** DB + org only (don't reuse another country's `ORG_ID`/web creds).
- `/opt/1pdb/services` importers (`backfill_transactions.py`, `import_hourly_*.py`, `import_koios_report.py`): confirm each connects to the country's DB and filters by the country's sites before go-live.

### Verify isolation (after enabling imports, and ideally in the daily audit)

Run against the new country DB — it must return **0** for foreign-site accounts:

```sql
-- onepower_zm: any non-ZM-site accounts present? (expect 0)
SELECT count(*) AS foreign_rows, count(DISTINCT account_number) AS foreign_accts
FROM transactions
WHERE account_number !~ '(SITE1|SITE2|SITE3)$';  -- = this country's site suffixes
```

Add this check to the country's balance-audit timer so any future cross-DB pollution is caught the next day rather than months later.

---

## 1. Preconditions

- [ ] **Business**: Sites, tariffs, and account numbering rules agreed (how account strings encode site, e.g. last 3 letters).
- [ ] **SparkMeter / Koios**: Org ID, service areas, site UUIDs for Koios v2 (and ThunderCloud if used).
- [ ] **Mobile money / payments**: Which operator(s), SMS confirmation format samples, settlement currency (**ISO 4217**).
- [ ] **Infrastructure**: Host capacity for another API process + DB (or new VM), TLS routing, secrets store.
- [ ] **People**: Customer Care WhatsApp / bridge ownership per country (separate tracker group).

---

## 2. Database (1PDB)

1. Create a dedicated PostgreSQL database (e.g. `onepower_zm`) from the canonical **1PDB** schema/migrations in the `1PDB` repo—same evolution path as Lesotho/Benin, not a one-off dump.
2. Run all applicable migrations through the current head (including CC-specific tables used by this API).
3. Configure connection string for **this country only** (e.g. `DATABASE_URL` in a country-specific `.env`).

---

## 3. Backend: `country_config.py`

1. Add a **`CountryConfig`** dataclass instance (copy **BENIN** or **LESOTHO** as template) with:
   - `code` — ISO 3166-1 alpha-2 (e.g. `ZM`)
   - `name`, `currency`, `currency_symbol`, `dial_code`
   - `koios_org_id`, `timezone`, `utc_offset_hours`, `default_tariff_rate`
   - `site_abbrev`, `site_districts`, `koios_sites` (site code → Koios site UUID)
   - `payment_regex_id` — label for SMS/payment parsing (implement or stub parsers before go-live)
2. Register it in **`_REGISTRY`**.
3. **`_SITE_TO_COUNTRY`** is built automatically from `_REGISTRY`; add explicit overrides only if needed (see **MAK** → LS).

Restart the API with **`COUNTRY_CODE=ZM`** (or the new code) and verify **`GET /api/config`** returns the expected country metadata.

---

## 4. Payments, SMS, and ingestion

| Area | Action |
|------|--------|
| **SparkMeter credit** | Confirm `sparkmeter_credit.py` routing for new site codes (Koios vs ThunderCloud, org keys). Add **write-capable** Koios credentials in env if applicable. |
| **SMS / MoMo** | Collect real confirmation SMS samples. Add parsers (e.g. alongside `mpesa_sms.py`) and wire **`POST /api/sms/incoming`** or a country-specific route if templates differ materially. Document **account resolution** (free-text field vs phone fallback). |
| **Tariff / balance** | Confirm `system_config` / `get_tariff_rate_for_site()` behaviour for all new site codes. |
| **Receipts / dedup** | Align `payment_reference` and idempotency rules with the operator’s receipt format. |

Do **not** assume Lesotho M-Pesa regexes apply to Zambia—validate against samples.

---

## 5. WhatsApp bridge (Customer Care alerts)

1. Run a **dedicated** Node bridge process for the country (separate `AUTH_DIR`, tracker **JID**, optional **`BRIDGE_INBOUND_PORT`**).
2. On the API host for this country, set:
   - **`CC_BRIDGE_NOTIFY_URL_ZM`** = `http://127.0.0.1:<port>/notify` (example for Zambia)
   - **`CC_BRIDGE_SECRET_ZM`** = shared secret with that bridge  
   If unsuffixed `CC_BRIDGE_NOTIFY_URL` / `CC_BRIDGE_SECRET` are set, they act as **fallback** when country-specific vars are absent (typical for a single-country server).

`cc_bridge_notify.bridge_credentials()` resolves `CC_BRIDGE_NOTIFY_URL_<CC>` / `CC_BRIDGE_SECRET_<CC>` automatically—no code change per country beyond env.

---

## 6. Frontend (`acdb-api/frontend`)

1. **`CountryContext.tsx`**
   - Add **`COUNTRY_ROUTES`**: e.g. `ZM: '/api/zm'`.
   - Extend **`FALLBACK_COUNTRIES`** (or rely on PR/Firebase `fetchPortfolios()` once the org exists) with display name, flag, `baseCurrency`.
2. **Reverse proxy (Caddy / nginx)** on `cc.1pwrafrica.com`: route `/api/zm` (and `/api/zm/*`) to the new backend listener (same pattern as `/api/bn`).
3. **i18n**: If the country uses a primary language other than English, extend `useEffect` language defaults (see Benin `fr` pattern) and add locale files as needed.
4. Run **`npx tsc -b --noEmit`** before merge.

---

## 7. Deployment & systemd

1. **systemd unit** for the new API (mirror `1pdb-api-bn`): same codebase path, different `.env`, different `WorkingDirectory`/`EnvironmentFile`, different **bind port** (e.g. 8102 for ZM if 8100=LS, 8101=BN).
2. **GitHub Actions / deploy**: Extend `deploy.yml` (or equivalent) to **rsync** backend and **restart** the new service; add a **health check** job for `https://cc.1pwrafrica.com/api/zm/health` (pattern matches existing `/api/bn/health`).
3. Document **server firewall** and **Caddy** snippets in internal runbooks.

---

## 8. Secrets & environment checklist

Copy the Lesotho/Benin `.env` as a template and set at minimum:

- `COUNTRY_CODE`
- `DATABASE_URL` (this country's API process) **and** `DATABASE_URL_{CC}` (so multi-country jobs target the right DB — see "Cross-country data isolation")
- **Country-suffixed** Koios/ThunderCloud credentials and org id — set ALL that apply, do not rely on bare-global fallbacks on the shared host:
  - `KOIOS_ORG_ID_{CC}` (or rely on `country_config.koios_org_id`)
  - `KOIOS_API_KEY_{CC}` / `KOIOS_API_SECRET_{CC}` (read), `KOIOS_MANAGE_API_KEY_{CC}` / `_SECRET_{CC}` (manage), `KOIOS_WRITE_API_KEY_{CC}` / `_SECRET_{CC}` (write)
  - `KOIOS_WEB_EMAIL_{CC}` / `KOIOS_WEB_PASSWORD_{CC}` if any web-scrape job (mirror/audit) is used — and confirm that login can see **this** org
- `CC_BRIDGE_NOTIFY_URL_<CC>` / `CC_BRIDGE_SECRET_<CC>` (or shared fallback pair)
- SMS gateway URL/secrets if mirroring payment SMS into the API
- Any country-specific keys already used in `customer_api.py` / `payments.py`

Never point a **production** `DATABASE_URL` at another country’s DB. On the shared multi-country host, **set the suffixed vars** above so a job can never fall back to another country's org/DB (this is what caused the 2026-06 LS→BN pollution).

---

## 9. Verification checklist (pre go-live)

- [ ] `GET /api/.../health` (routed) returns 200 with correct country hint if exposed.
- [ ] `GET /config` matches expected currency, sites, org.
- [ ] Test **login** and **country selector** switching to the new country (API fan-out).
- [ ] **Record payment** (or sandbox) posts to SparkMeter with correct org/site.
- [ ] **Bridge notify** test POST to `/notify` with `X-Bridge-Secret` delivers to the **right** WhatsApp group.
- [ ] **SMS ingest** (if live): one real payment end-to-end with correct account and balance.
- [ ] **Cross-country isolation**: after the SM mirror / importers have run at least once, the isolation query (see "Cross-country data isolation") returns **0** foreign-site accounts in the new country's DB. Re-run after 24h.

---

## 10. Documentation

- Update **`CONTEXT.md`**: Data Sources row, multi-country diagram if ports/routes change.
- Add a one-line **SESSION_LOG** entry when the country goes live.
- Keep **this SOP** updated when you add a new recurring step (e.g. new env var).

---

## Reference: Zambia placeholder

`CONTEXT.md` already lists a **Zambia API** placeholder (`cc-api-zm`, ZMW, Airtel/MTN, TBD metering). The codebase now ships a stub `ZAMBIA` `CountryConfig` in [`acdb-api/country_config.py`](../acdb-api/country_config.py) with `active=False`, empty `site_abbrev` / `koios_sites`, and `payment_regex_id="momo_zm"`. To go live:

1. Populate `site_abbrev` / `site_districts` / `koios_sites` with the commissioned sites.
2. Confirm tariff and metering platform; update `default_tariff_rate` and `koios_org_id`.
3. Implement `momo_zm` SMS parser alongside `mpesa_sms.py` once SMS samples are available.
4. Stand up `onepower_zm` DB (the deploy workflow already applies incremental migrations to it if the DB exists; same is true for the optional `1pdb-api-zm` systemd unit and the `/api/zm/health` health check).
5. Add a Caddy route for `/api/zm/*` and `/api/zm/health` to the new service.
6. **SM→CC mirror & isolation:** set `DATABASE_URL_ZM` + the suffixed Koios vars (`KOIOS_ORG_ID_ZM`, `KOIOS_WEB_EMAIL_ZM`/`PASSWORD_ZM`, key/secret pairs) in `/opt/1pdb/.env`; add the `("ZM", "koios", db_zm)` job to `scripts/ops/run_sm_credit_mirror_incremental.py`. The mirror already resolves org per country and applies a site guard, so with ZM sites populated in `country_config` it will only mirror ZM accounts. Then run the isolation query against `onepower_zm` (expect 0 foreign-site rows).
7. Flip `active=True` in `country_config.py` so the country selector starts surfacing Zambia.
8. Tick the checklist above (including **Cross-country isolation**).

The Odyssey Standard API ([`docs/odyssey-standard-api.md`](odyssey-standard-api.md)) is country-agnostic — once `1pdb-api-zm` is up, the same `programs` / `program_memberships` migration (`017_*.sql`) wires UEF/ZEDSI customers into the API without code changes.

---

## Related files

| Topic | Location |
|-------|----------|
| Country constants | `acdb-api/country_config.py` |
| Bridge notify + env | `acdb-api/cc_bridge_notify.py` |
| Frontend routing | `acdb-api/frontend/src/contexts/CountryContext.tsx` |
| SMS (Lesotho example) | `acdb-api/mpesa_sms.py`, `acdb-api/ingest.py` |
| Deploy | `.github/workflows/deploy.yml` |
| Architecture | `CONTEXT.md` — Multi-Country Architecture |
