# 1PWR Customer Care Portal — Operating Manual

**Revision Date:** April 2026  
**Portal URL (production):** https://cc.1pwrafrica.com  
**Administered by:** OnePower Lesotho (with multi-country backends)

---

## How to use this manual

| Resource | Purpose |
|----------|---------|
| **This document** | Full printable / shareable reference; keep in sync with product releases. |
| **In-app Help** (`/help`, also **Help** in the navigation bar) | Same topics in an interactive layout with search and quick links to portal pages. |

**Roles:** Most procedures assume an **employee** login. **Customer self-service** is documented separately; paths begin with `/my/`.

**Environments:** Production is `cc.1pwrafrica.com`. If your organization provides a **sandbox** or **staging** URL for training, use it for the [Sandbox tutorial](#22-sandbox-tutorial-safe-practice-environment) below. Never use production accounts for destructive experiments.

---

## 1. Introduction

The 1PWR Customer Care (CC) Portal is a web application for minigrid customer operations: registration, metering, payments, financing, reporting, and audit. It replaces the former ACCDB-based system. All employee workflows run in a browser (desktop, tablet, or phone); no RDP or Access is required.

### 1.1 Feature index (by page)

| Category | Feature | Path | Typical roles |
|----------|---------|------|----------------|
| Help | User guide (this content in-app) | `/help` | All employees |
| Dashboard | KPIs, sites, table counts | `/dashboard` | All employees |
| Reports | O&M quarterly report | `/om-report` | All employees |
| Reports | Financial analytics (ARPU, revenue) | `/financial` | All employees |
| Reports | Check meters (SM vs 1M) | `/check-meters` | All employees |
| Customers | Search and list | `/customers` | All employees |
| Customers | Register new customer | `/customers/new` | superadmin, onm_team |
| Customers | Customer profile | `/customers/:id` | All employees |
| Commission | Commissioning wizard | `/commission` | superadmin, onm_team |
| Metering | Meter registry | `/meters` | All employees |
| Metering | Assign meter | `/assign-meter` | superadmin, onm_team |
| Data | Customer data (balance, charts) | `/customer-data` | All employees |
| Data | Accounts browser | `/accounts` | All employees |
| Data | Transactions browser | `/transactions` | All employees |
| Data | Raw tables | `/tables`, `/tables/:name` | All employees |
| Data | CSV/XLSX export | `/export` | All employees |
| Payments | Record missed payment | `/record-payment` | All employees |
| Payments | Payment verification | `/payment-verification` | All employees (finance primary) |
| Financing | Products, agreements, ledger | `/financing` | All employees (finance primary) |
| Reports | Onboarding pipeline funnel | `/pipeline` | All employees |
| Admin | Tariff management | `/tariffs` | All employees |
| Admin | Mutation audit log | `/mutations` | All employees |
| Admin | uGridPlan sync | `/sync` | All employees |
| Admin | Role management | `/admin/roles` | superadmin only |
| Customer | Self-service dashboard | `/my/dashboard` | Customer login |
| Customer | My profile | `/my/profile` | Customer login |

---

## 2. Login and authentication

### 2.1 Employee login

1. Open https://cc.1pwrafrica.com (or your sandbox URL).
2. Enter **Employee ID** and **password**.
3. Click **Sign In**. You land on the **Dashboard** (or home redirect).

### 2.2 Customer self-service login

Customers use **customer ID** (and credentials as configured). After login they see **My Dashboard** and **My Account** — not the full staff navigation.

### 2.3 Roles (RBAC)

| Role | Scope |
|------|--------|
| **superadmin** | Full access, including `/admin/roles` and all write workflows. |
| **onm_team** | Operations: registration, commissioning, meter assignment, field-oriented workflows. |
| **finance_team** | Finance-heavy pages (verification, financing, reports). |
| **generic** | Read-focused access; may be restricted from some write actions. |

Exact permissions for a given deployment are enforced server-side. If an action fails with “forbidden,” ask a **superadmin** to adjust your role.

---

## 3. Multi-country and portfolio selection

The header may show a **country** selector (e.g. Lesotho, Benin) and, where configured, a **portfolio** filter. The selected country determines which backend database and currency apply. **Always confirm the country** before creating customers or recording payments.

---

## 4. Dashboard (`/dashboard`)

- Summary of **sites/concessions** with customer counts (from live data).
- **Energy and revenue** summaries where APIs provide them.
- **Database table** inventory and **record completeness** indicators (when enabled).

Use the Dashboard as the daily entry point; deep work happens on feature pages.

---

## 5. End-to-end customer lifecycle (typical flow)

This is the “happy path” many teams follow; adapt to local SOPs.

1. **Register** the customer (`/customers/new`) — capture identity, phone, **site/concession**, customer type.
2. **Fees and steps** — connection / readyboard fees often flow through M-PESA and the **payment verification** queue (`/payment-verification`).
3. **Commission** (`/commission`) — verify details, signature, contracts, SMS links.
4. **Meter** — assign or confirm meter (`/assign-meter`, `/meters`).
5. **Ongoing** — **Customer Data** (`/customer-data`) for balance and history; **Record Payment** if the SMS gateway missed a payment.
6. **Financing** (optional) — extend credit from customer detail or `/financing`.

Track funnel position on **Pipeline** (`/pipeline`).

---

## 6. Customers

### 6.1 Search and list (`/customers`)

- Text search across names, IDs, account numbers, and related fields.
- Click a row to open **Customer detail**.

### 6.2 Register new customer (`/customers/new`)

**Roles:** superadmin, onm_team.

1. Open **Customers → New Customer** or go to `/customers/new`.
2. Complete required fields (names, national ID, phone, **site/concession**, customer type, etc.).
3. Save. An **account number** is generated automatically when applicable.

**Site/concession list:** Dropdowns that list “sites” are often built from **communities that already have at least one customer** in the database. If a valid site code does not appear yet, coordinate with your **data/engineering** team — the canonical site list is configured per country in the backend; the first customer at a new site may require a one-time data entry path agreed with admins.

### 6.3 Customer detail (`/customers/:id`)

- View and edit profile fields.
- Common actions (depending on role and state): **Edit**, **View Data**, **Commission**, **Extend Credit**, **Assign Meter**, **Decommission**.

### 6.4 Sites and concessions (operational notes)

- **Account numbers** encode site as the last segment (e.g. `0045MAK` → site **MAK**).
- **Community / concession** on the customer record should match the operational site.
- **Adding a new site code** to a country is an **engineering/configuration** change (backend country config, Koios IDs where applicable), not something end users toggle in the UI. Operations should request adds through your standard change process; after deployment, new sites appear in config-driven UIs.

---

## 7. Commissioning (`/commission`)

**Roles:** superadmin, onm_team.

1. Look up the customer by **account number** or **customer ID**.
2. Verify or update: name, national ID, phone, GPS, customer type, service phase, ampacity.
3. **Capture signature** on the canvas.
4. **Generate contracts** (e.g. English / Sesotho PDFs) and store them.
5. **Send SMS** with contract download link when supported.

**Seven tracked steps:** connection fee paid → readyboard fee paid → readyboard tested → readyboard installed → airdac connected → meter installed → **customer commissioned**. Update steps individually or in bulk from this flow. Use **Pipeline** to see cohort progress.

---

## 8. Meters

### 8.1 Meter registry (`/meters`)

Browse and search meters: ID, account, community/site, status, type.

### 8.2 Assign meter (`/assign-meter`)

**Roles:** superadmin, onm_team.

Assign or reassign a meter to an account. Meter history is auditable.

### 8.3 Check meters (`/check-meters`)

Compare **SparkMeter** production data to **1Meter** check meters: time series, deviation stats, fleet summary, health (online/stale/offline).

### 8.4 Meter lifecycle

Statuses such as active → inactive → decommissioned → maintenance are logged in the **mutation audit** trail.

---

## 9. Accounts and transactions

- **`/accounts`** — Account registry and filters.
- **`/transactions`** — Transaction browser (payments, vend, etc., per schema).

Use together with **Customer Data** when reconciling a specific account.

---

## 10. Customer data (`/customer-data`)

Enter an **account number** to view:

- **Balance** (kWh and currency equivalent).
- **Average consumption** (kWh/day).
- **Estimated recharge time** (illustrative).
- **Last payment**.
- **Totals** (consumption / purchases — per implementation).
- **Active financing** summary when applicable.
- **Transaction history** (sortable; some inline editing may be available).
- **Consumption charts** (24h, 7d, 30d, 12m).

---

## 11. Payments

### 11.1 Record missed payment (`/record-payment`)

When M-PESA (or other gateway) did not record a payment:

1. Enter **account number** (e.g. `0045MAK`).
2. Enter **amount** in local currency.
3. Optional: meter ID, note.
4. **Record Payment**.

The system converts to kWh at the tariff, credits balance, and integrates with SparkMeter where configured. **Financing:** payments may split between electricity and debt; amounts ending in **1** or **9** in the ones digit can be treated as dedicated debt payments per product rules.

### 11.2 Payment verification (`/payment-verification`)

For fees that require finance approval:

1. Open the queue (defaults to **Pending**).
2. Filter by type/status.
3. Select rows, add notes, **Verify** or **Reject**.
4. Use **Export XLSX** for records if available.

---

## 12. Financing (`/financing`)

Asset financing (readyboards, appliances, etc.):

- **Product templates** — Default principal, interest, fees, repayment fraction, penalties, grace.
- **Agreements** — Create from **customer detail** (**Extend Credit**) or manage here; signed PDFs; ledger per agreement.
- **Splits** — Repayment fraction applies to ordinary payments; **1/9** ones-digit rule for dedicated debt; FIFO across multiple agreements.
- **Penalties** — Automated after grace period per configuration.

---

## 13. Reports

### 13.1 O&M quarterly report (`/om-report`)

Operational metrics: customer stats by site, growth, consumption and revenue by site/quarter, generation vs consumption, average consumption trends, consumption by tenure, etc.

### 13.2 Financial analytics (`/financial`)

Revenue, ARPU, monthly revenue by site, payment mix, comparisons.

### 13.3 Onboarding pipeline (`/pipeline`)

Funnel from registered through commissioned; filters, drop-off rates, summary cards.

---

## 14. Data tools

### 14.1 Raw tables (`/tables`)

Browse database tables with sort/filter; advanced users only — changes affect production data.

### 14.2 Export (`/export`)

Export authorized tables to **CSV** or **XLSX** with filters.

### 14.3 Mutations (`/mutations`)

Audit log of creates/updates/deletes: who, when, what changed; supports accountability and optional revert flows.

---

## 15. Tariffs (`/tariffs`)

View and update **per-site** tariff rates. Changes affect **future** kWh conversion; historical rows are not recalculated automatically.

---

## 16. uGridPlan sync (`/sync`)

Integration with **uGridPlan** (https://ugp.1pwrafrica.com): customer sync, O&M tickets, survey/connection linkage. The page shows sync status and site/project mappings. Bulk registration may also reference UGridPlan workflows.

---

## 17. Administration

### 17.1 Roles (`/admin/roles`)

**superadmin only:** assign roles, activate/deactivate users.

### 17.2 Passwords

Employees change passwords per policy; superadmins may reset others where implemented.

---

## 18. Customer self-service (`/my/dashboard`, `/my/profile`)

Customers see balance-oriented dashboards and profile — not staff menus. Content depends on backend features enabled for customer tokens.

---

## 19. Help (`/help`)

The **Help** page mirrors this manual in a navigable format with section search. When documentation and the app disagree, treat the **deployed application behavior** as authoritative and file a bug or doc fix.

---

## 20. Sandbox tutorial (safe practice environment)

**Purpose:** Train new users without impacting real customers or finance.

### 20.1 When a sandbox exists

If your organization provides a **non-production** URL (staging/sandbox):

1. **Request credentials** from your admin (separate from production).
2. **Use only sandbox URLs** during training; bookmark them clearly.
3. **Run through Section 5** (lifecycle) with synthetic names and test phones.
4. **Record payment** — use small, obviously test amounts and accounts created in sandbox.
5. **Never** copy production passwords or API keys into sandbox notes.

### 20.2 When no dedicated sandbox is available

- Use **production in read-only mode**: Dashboard, reports, Customer Data **lookup**, export where allowed — **no** fake registrations or test payments on real accounts.
- Ask leadership to provision a **staging** environment; training on production creates compliance and data-quality risk.

### 20.3 Suggested first-hour checklist (sandbox)

| Step | Action | Success criterion |
|------|--------|-------------------|
| 1 | Log in as trainee | Dashboard loads |
| 2 | Open `/help` | Sections render; search works |
| 3 | Find a test customer or create one | Customer detail opens |
| 4 | Open Customer Data for a test account | Balance panel loads |
| 5 | Open O&M report | Charts load without error |
| 6 | (Optional) Pipeline | See funnel stages |

---

## Appendix A: Key differences from the ACCDB era

| Old (ACCDB) | New (CC Portal) |
|-------------|-----------------|
| Windows RDP | Web browser |
| Access/VBA forms | React application |
| Dropbox paths for files | Portal export/download |
| Single user | Multi-user concurrent |
| Limited audit | Mutation audit trail |
| No self-service | Customer login |

---

## Appendix B: System architecture (reference)

- **Frontend:** React + TypeScript + Vite (static deploy).
- **Backend:** FastAPI + PostgreSQL (`1PDB`).
- **Integrations:** SparkMeter (Koios / ThunderCloud as applicable), SMS gateways, uGridPlan, optional IoT paths for 1Meter.
- **Deploy:** Push to `main` triggers CI/CD to the CC Linux host (see engineering README).

---

*For technical setup, database ownership, and deployment, see `README.md` and `CONTEXT.md` in the repository.*
