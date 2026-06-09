# Admin Guide & Tutorial: Setting up a new country in Customer Care

**Audience:** Customer Care **admins** (superadmin / O&M / finance leads) who are launching CC for a new operating country (next up: **Zambia**).

**What this is:** a plain-language, step-by-step walkthrough of *your* part of bringing a country live, what the engineering team does, and how to verify it. For the deep technical procedure, engineers use the companion **engineering SOP**: [`docs/sop-add-new-country.md`](sop-add-new-country.md).

---

## 1. The mental model (read this first)

Customer Care runs **one country per "lane"**: each country has its **own database**, its **own backend service**, and its **own web route** under the same portal (`cc.1pwrafrica.com`). You switch between countries with the **country selector** (top-left of the portal). Lesotho (`LS`) and Benin (`BN`) are live today; Zambia (`ZM`) is the next lane.

Two jobs, two owners:

- **Engineering builds the lane** — database, service, web route, payment/SMS plumbing, SparkMeter/Koios credentials. (One-time, per country.)
- **Admin configures the business rules** — tariff rates, fees, low-balance alert thresholds, SMS limits, and who on the team can access the country. (Ongoing, in the portal.)

> **Golden rule — data isolation:** each country's data stays in its own database. Never reuse one country's SparkMeter/Koios login or database for another. (In 2026 a mis-set credential silently copied ~660 Lesotho accounts into the Benin database; engineering has since added guardrails and a daily automated check, but admins should still never share credentials across countries.)

---

## 2. Who does what (RACI)

| Step | Admin | Engineering |
|---|---|---|
| Decide sites, tariffs, currency, payment operator, account-number format | **Responsible** | Consulted |
| Provide SparkMeter/Koios org id, site UUIDs, API/web credentials | **Responsible** (obtain from SparkMeter) | Consulted |
| Create the database, service, web route, deploy | Informed | **Responsible** |
| Add the country to `country_config` + SMS/payment parsers | Informed | **Responsible** |
| Set per-country credentials/env (isolation) | Informed | **Responsible** |
| Configure tariff rates, connection/readyboard fees, low-balance thresholds, SMS caps | **Responsible** (in portal) | Support |
| Assign team roles for the country | **Responsible** (superadmin) | — |
| Verify go-live + monitor drift/isolation | **Responsible** (review) | **Responsible** (alerts) |

---

## 3. Prerequisites — the "intake" the admin gathers

Before engineering can build the lane, collect and hand over:

- [ ] **Country code** (ISO 2-letter, e.g. `ZM`) and **currency** (ISO 3, e.g. `ZMW`) + symbol.
- [ ] **Sites/concessions**: the list of site codes and the **account-number format** (CC encodes the site in the **last 3 letters** of the account, e.g. `0042` + `TOS`).
- [ ] **Tariff(s)**: electricity rate(s) (currency per kWh) and any per-site differences.
- [ ] **One-off fees**: connection fee, readyboard fee (amounts in local currency).
- [ ] **Metering platform**: SparkMeter **Koios org id**, the **site UUIDs**, and **API + web credentials** for *that org only*.
- [ ] **Payments**: mobile-money operator(s) and **sample confirmation SMS** texts (engineering needs these to write the parser).
- [ ] **Low-balance policy**: warn threshold and clear threshold (kWh), and max alerts/day.
- [ ] **Team access**: who (by HR identity) gets `superadmin` / `onm_team` / `finance_team` for this country.

> **Tip:** the account-number → site mapping is critical. CC routes balances, tariffs, and SparkMeter credits by the site suffix. Confirm it with a few real account numbers before go-live.

---

## 4. Tutorial — step by step

### Phase A — Hand off the intake (Admin)
Send engineering the completed checklist in §3. Engineering will follow [`docs/sop-add-new-country.md`](sop-add-new-country.md) to create the database, service, route, `country_config` entry, parsers, and per-country credentials.

### Phase B — Engineering builds the lane (Engineering; Admin waits)
Engineering will tell you when:
- the new country appears in the **country selector**, and
- the service health check passes (e.g. `https://cc.1pwrafrica.com/api/zm/health` returns OK).

You don't need to do anything here except confirm the country shows up.

### Phase C — Configure business rules in the portal (Admin)
Log in, then use the **country selector (top-left)** to switch to the new country. Everything below applies **only to the selected country** (each country has independent settings).

1. **Tariff rates** → go to **Tariffs** (`/tariffs`).
   - Set the electricity tariff rate(s) (currency per kWh) for each site. This drives every balance and receipt — get it right before any real payment.
2. **Connection & readyboard fees** → **Tariffs** page, **"Connection & Readyboard Fees"** card.
   - Enter the one-off fee amounts. Setting a fee to `0` disables the auto-classifier for that fee.
3. **Low-balance SMS thresholds** → same **Tariffs** page, **"Low balance SMS (kWh)"** settings.
   - **Warn** when remaining kWh ≤ threshold; **Clear** the warned flag when balance rises back to the clear level (clear must be higher than warn).
   - Set **max low-balance SMS per day** and the **balance-reply caps** (per hour / per day) to control SMS volume.
4. **Team roles** → **Admin → Roles** (superadmin only).
   - Grant `superadmin` (full + role management), `onm_team` (operations, edit customers/transactions), or `finance_team` (transactions, no customer edits) to the right people for this country.

> **Important:** these settings are **per country**. Switch the country selector first, then edit — otherwise you'll change the wrong country's tariffs/fees.

### Phase D — Verify (Admin)
With the new country selected:
- [ ] **Country selector** lists the new country and switching to it loads its dashboard.
- [ ] **Tariffs** page shows your rates, fees, and low-balance thresholds.
- [ ] **Customer Data** lookup for a known account returns the right name, site, and a sensible balance.
- [ ] A **test/sandbox payment** (with engineering) posts to SparkMeter for the correct site and updates the balance.
- [ ] Ask engineering to confirm the **daily isolation check** is green for the new database (no foreign-country accounts) — see §6.

### Phase E — Go-live & monitor (Admin + Engineering)
- Flip the country to **active** (engineering toggles `active=True` so it surfaces to everyone).
- Watch the **daily drift audit** (CC balances vs SparkMeter) and the **daily isolation check** for the first weeks.

---

## 5. Where each setting lives (quick reference)

| Setting | Who sets it | Where |
|---|---|---|
| Country exists / currency / sites / parsers | Engineering | `country_config.py` + service (SOP) |
| Electricity tariff rate (per kWh) | Admin | **Tariffs** page (`/tariffs`) |
| Connection / readyboard fees | Admin (superadmin/O&M/finance) | **Tariffs** → Connection & Readyboard Fees |
| Low-balance warn/clear (kWh), SMS caps | Admin | **Tariffs** → Low balance SMS |
| Team access (roles) | Admin (superadmin) | **Admin → Roles** |
| Database / credentials / web route | Engineering | server `.env`, systemd, Caddy (SOP) |

Behind the scenes the editable values live in each country's `system_config` (one per database), which is why they're independent per country.

---

## 6. Data isolation — what admins must know

Each country's data lives in its own database, and automated jobs (e.g. the SparkMeter→CC credit mirror) are scoped per country. Engineering enforces this in code (per-country credentials + a "site guard"), and a **daily automated check** (`check_db_isolation`) alerts if any country's database ever contains accounts from another country.

As an admin, your part is simple:
- **Never** give a new country another country's SparkMeter/Koios login or database connection.
- If you ever see accounts from the **wrong country** in a lookup or report, flag engineering immediately and reference this guide + the isolation check.

---

## 7. Troubleshooting / FAQ

- **"The new country isn't in the selector."** Engineering hasn't flipped it `active` yet, or the service/route isn't deployed. Confirm the health check with engineering.
- **"Balances look 5× too high/low or in the wrong unit."** Tariff rate is likely wrong or in the wrong unit — SparkMeter stores credit in **currency**, CC shows **kWh** (= currency ÷ tariff). Fix the rate on the **Tariffs** page and re-check; if balances still diverge, engineering can run the drift audit/re-anchor.
- **"A customer paid but the meter shows nothing."** The credit push may be queued because the account isn't commissioned or has no meter attached on SparkMeter. Commission the account / attach the meter, then engineering re-queues the push.
- **"Customers get too many / too few low-balance SMS."** Adjust the warn/clear thresholds and the per-day cap on the **Tariffs** page.
- **"I edited tariffs but the wrong country changed."** You had the wrong country selected. Switch the **country selector** and re-check both countries.

---

## 8. Related docs

| Topic | Doc |
|---|---|
| Engineering procedure (DB, service, route, parsers, credentials) | [`docs/sop-add-new-country.md`](sop-add-new-country.md) |
| Cross-country data isolation rules | [`docs/sop-add-new-country.md`](sop-add-new-country.md) → "Cross-country data isolation" |
| Architecture overview | [`CONTEXT.md`](../CONTEXT.md) → Multi-Country Architecture |
| In-portal help | **Help** page (`/help`) in the portal |
