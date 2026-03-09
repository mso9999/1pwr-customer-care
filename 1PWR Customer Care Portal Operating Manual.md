# 1PWR Customer Care Portal — Operating Manual

**Revision Date:** February 2026
**Portal URL:** https://cc.1pwrafrica.com
**Administered by:** OnePower Lesotho

---

## Introduction

The 1PWR Customer Care (CC) Portal is a web-based application that replaces the former ACCDB-based database system. All operations are performed through a web browser — no RDP, VBA forms, or Dropbox paths are required.

This manual details the step-by-step guidelines for using the portal to perform the following actions. Access to specific functions depends on the user's assigned role.

### List of Actions

| Category | Feature | Portal Page |
|----------|---------|-------------|
| Authentication | Employee login | `/login` |
| Authentication | Customer self-service login | `/login` |
| Customer Management | View/search customers | `/customers` |
| Customer Management | Register new customer | `/customers/new` |
| Customer Management | Customer detail & profile | `/customers/:id` |
| Customer Management | Customer data & transactions | `/customer-data` |
| Customer Management | Commission customer | `/commission` |
| Metering | View/search meters | `/meters` |
| Metering | Assign meter to customer | `/assign-meter` |
| Metering | Check meter comparison | `/check-meters` |
| Payments | Record missed payment | `/record-payment` |
| Payments | Payment verification | `/payment-verification` |
| Financing | Product templates & agreements | `/financing` |
| Reports | O&M quarterly report | `/om-report` |
| Reports | Financial analytics | `/financial` |
| Reports | Onboarding pipeline | `/pipeline` |
| Data | View accounts | `/accounts` |
| Data | View transactions | `/transactions` |
| Data | Browse raw tables | `/tables` |
| Data | Export to CSV/XLSX | `/export` |
| Administration | Tariff management | `/tariffs` |
| Administration | Role management | `/admin/roles` |
| Administration | Mutation audit log | `/mutations` |
| Administration | UGridPlan sync | `/sync` |

---

## 1. Login

Navigate to https://cc.1pwrafrica.com. The login screen presents two options:

### Employee Login
- Enter your **Employee ID** and **password**.
- Click **Sign In**.
- You will be redirected to the Dashboard.

### Customer Self-Service Login
- Customers can register and log in with their customer ID.
- The customer view provides a personal dashboard with balance, consumption history, and profile.

### Roles
The portal uses role-based access control:
- **superadmin** — Full access including role management.
- **onm_team** — Operations & maintenance features.
- **finance_team** — Financial reporting and payment verification.
- **generic** — Basic read access.

---

## 2. Dashboard

The main dashboard (`/dashboard`) provides an overview of the system with key metrics. From here, the top navigation bar provides access to all portal features.

### Navigation Bar
The horizontal navigation bar at the top of every page includes:
- **Dashboard** — Home overview
- **O&M Report** — Quarterly operational metrics
- **Financial** — Revenue and ARPU analytics
- **Check Meters** — SparkMeter vs 1Meter comparison
- **Customers** — Customer registry
- **Meters** — Meter registry
- **Accounts** — Account registry
- **Transactions** — Transaction browser
- **Customer Data** — Per-customer lookup
- **Tables** — Raw table browser
- **Export** — Data export (CSV/XLSX)
- **Tariffs** — Tariff rate management
- **Financing** — Asset financing management
- **Record Payment** — Manual payment entry
- **Verify Payments** — Payment verification queue
- **Pipeline** — Onboarding funnel
- **Mutations** — Audit trail
- **Sync** — UGridPlan sync status

---

## 3. Customer Registration

### Individual Registration
1. Navigate to `/customers/new`.
2. Fill in required fields: first name, last name, national ID, phone number, site/concession, customer type.
3. Click **Save**. The customer is created and assigned an account number automatically.

### Bulk Registration
Bulk registration is handled through the data import features or the UGridPlan integration at `/sync`.

---

## 4. Commission Customer

The commission page (`/commission`) provides a multi-step wizard to finalize a customer's service connection:

1. **Look up** the customer by account number or customer ID.
2. **Verify/update** customer details: name, national ID, phone, GPS coordinates, customer type, service phase, ampacity.
3. **Capture signature** — the customer signs on a tablet canvas.
4. **Generate contracts** — bilingual (English/Sesotho) PDF contracts are generated automatically and stored.
5. **Send SMS** — the contract download link is sent to the customer via SMS.

### Commissioning Steps
The system tracks seven commissioning steps per customer:
1. Connection fee paid
2. Readyboard fee paid
3. Readyboard tested
4. Readyboard installed
5. Airdac connected
6. Meter installed
7. Customer commissioned

These can be updated individually or in bulk from the commission page.

---

## 5. Record Missed Payment

Page: `/record-payment`

When a payment is missed by the SMS gateway (e.g., the gateway phone was offline), record it manually:

1. Enter the **account number** (e.g., `0045MAK`).
2. Enter the **amount** in Maloti.
3. Optionally specify a meter ID and note.
4. Click **Record Payment**.

The system will:
- Convert the currency amount to kWh at the current tariff rate.
- Credit the customer's electricity balance.
- Credit SparkMeter (if configured).
- If the customer has active financing, automatically split the payment per the financing terms (see Section 9).

**Financing Split Indicator:** If the payment amount ends in digit **1** or **9** (e.g., M51, M101, M79), the entire amount is treated as a dedicated debt payment rather than being split.

---

## 6. Payment Verification

Page: `/payment-verification`

Connection fees and readyboard fees require verification by the finance team.

1. Open the **Payment Verification** page.
2. The default view shows **Pending** verifications.
3. Use filters to narrow by payment type or status.
4. Select one or more payments using checkboxes.
5. Optionally add a note.
6. Click **Verify** or **Reject**.

### Filtering
- **Status:** Pending, Verified, Rejected, All
- **Type:** Connection Fee, Readyboard Fee, Electricity, Uncategorized

---

## 7. Reports

### O&M Quarterly Report (`/om-report`)
Generates operational metrics matching the SMP O&M quarterly report format:
- Customer statistics per site (total, active, new)
- Customer connection growth (quarterly)
- Consumption per site per quarter (kWh)
- Revenue per site per quarter (LSL)
- Generation vs consumption
- Average consumption per customer trends
- Consumption by customer tenure

### Financial Analytics (`/financial`)
Revenue and ARPU analytics:
- Monthly revenue by site
- ARPU (Average Revenue Per User) trends
- Payment type breakdown
- Revenue growth comparisons

### Onboarding Pipeline (`/pipeline`)
A funnel visualization showing how many customers are at each stage of the commissioning process:
- **Registered** → Connection Fee Paid → Readyboard Fee Paid → Readyboard Tested → Readyboard Installed → Airdac Connected → Meter Installed → **Commissioned**

Features:
- Filter by site/community.
- Drop-off percentages between each stage.
- Summary cards: total registered, fully commissioned, conversion rate, in-progress count.
- Tabular breakdown with percentages.

### Check Meter Comparison (`/check-meters`)
Compares SparkMeter (SM) production meter readings against 1Meter (1M) check meter readings:
- Hourly kWh time series (configurable time range).
- Per-meter deviation statistics (%, mean, std dev).
- Fleet-wide total deviation summary.
- Meter health indicators (online/stale/offline).

---

## 8. Customer Data Lookup

Page: `/customer-data`

Enter an account number to view comprehensive customer data:

- **Balance** — Current kWh balance and currency equivalent.
- **Average Consumption** — kWh/day.
- **Estimated Recharge Time** — Based on current balance and consumption rate.
- **Last Payment** — Most recent payment amount and date.
- **Total Consumption & Purchases** — All-time totals.
- **Active Financing** — If the customer has financing agreements, a summary card shows total outstanding debt with progress bars per agreement.
- **Transaction History** — Sortable table of all transactions (payments and consumption).
- **Consumption Charts** — 24h, 7-day, 30-day, and 12-month consumption visualizations.

---

## 9. Customer Financing

Page: `/financing`

The financing system allows extending credit to customers for assets like readyboards, refrigerators, or solar lanterns.

### Product Templates (Financing > Product Templates tab)
Define reusable financing product templates with default terms:
- **Name** — e.g., "Readyboard", "Refrigerator"
- **Default Principal** — Standard financed amount
- **Interest Rate** — e.g., 10%
- **Setup Fee** — Administration fee
- **Repayment Fraction** — % of each electricity payment diverted to debt (e.g., 20%)
- **Penalty Rate** — Applied when payments are overdue
- **Grace Days** — Days before penalty applies
- **Penalty Interval** — How often penalty recurs

### Creating a Financing Agreement
From the customer detail page or the financing management page:
1. Select a product template (pre-fills terms) or set custom terms.
2. Set the principal, interest, fees, repayment fraction, and penalty parameters.
3. The **total owed** is computed: principal + interest + fees.
4. Capture the customer's signature.
5. A signed financing agreement PDF is generated and attached to the account.

### Payment Splitting
When a customer with active financing makes a payment:
- **Regular payments:** Split between electricity and debt per the repayment fraction.
  - Example: M100 payment with 20% fraction → M20 to debt, M80 to electricity.
- **Dedicated debt payments:** If the amount's ones digit is **1** or **9** (e.g., M51, M101, M79), the **entire** amount is applied to debt.
- **FIFO ordering:** If multiple agreements exist, payments are applied to the oldest first.
- The electricity portion is credited to SparkMeter normally.
- The debt portion is recorded in the financing ledger.

### Agreements Table (Financing > Agreements tab)
View all financing agreements across customers:
- Filter by status: Active, Paid Off, Defaulted, Cancelled.
- Click any agreement to view its full ledger (payments, penalties, adjustments).

### Automatic Penalties
The system automatically applies penalties to overdue agreements:
- If no payment is received within the **grace days**, a penalty equal to **penalty rate × outstanding balance** is added.
- Penalties recur at the configured interval until a payment is made.
- Penalty entries appear in the agreement's ledger.

### Financing on Customer Data Page
When viewing a customer in `/customer-data`, if they have active financing:
- An amber-highlighted **Active Financing** section appears.
- Shows total outstanding debt and per-agreement progress bars.
- Debt repayment progress is visible at a glance.

---

## 10. Meters

### Meter Registry (`/meters`)
Browse and search all meters in the system. Each meter record includes:
- Meter ID, account number, community/site, status, type.

### Assign Meter (`/assign-meter`)
Assign a meter to a customer account or reassign between accounts.

### Meter Lifecycle
Meters follow a lifecycle: active → inactive → decommissioned → maintenance. Status changes are logged in the mutation audit trail.

---

## 11. Data Export

Page: `/export`

Export data tables to CSV or XLSX format:
1. Select the table to export.
2. Apply filters (site, date range, etc.).
3. Click **Export**.
4. The file downloads to your browser.

Available tables include: customers, accounts, meters, transactions, hourly_consumption, and more.

---

## 12. Tariff Management

Page: `/tariffs`

Manage electricity tariff rates per site/concession:
- View current tariff rates.
- Update rates (changes take effect for future payments).
- Country-specific tariff configuration.

---

## 13. User Management

### Role Assignment (`/admin/roles`)
Available to superadmin users only:
- View all users and their current roles.
- Assign or change roles (superadmin, onm_team, finance_team, generic).
- Activate or deactivate user accounts.

### Password Management
Users manage their own passwords. Superadmins can reset passwords for other users.

---

## 14. Mutation Audit Trail

Page: `/mutations`

All data modifications (creates, updates, deletes) are logged with:
- Timestamp
- User who made the change
- Table and record affected
- Old and new values

This provides a complete audit trail and supports reverting changes if needed.

---

## 15. UGridPlan Integration

Page: `/sync`

The CC portal integrates with UGridPlan (https://ugp.1pwrafrica.com) via API:
- Customer data synchronization.
- O&M ticket creation.
- Survey/connection binding.

The sync page shows the status of recent synchronization operations.

---

## Key Differences from ACCDB System

| Old (ACCDB) | New (CC Portal) |
|-------------|-----------------|
| Windows RDP connection required | Web browser from any device |
| VBA forms in Access database | React web application |
| Dropbox file paths for imports/exports | In-browser data entry and download |
| Spreadsheet-based bulk registration | Web forms + UGridPlan sync |
| Spreadsheet-based payment verification | In-portal verification queue |
| Reports exported to Dropbox directory | In-portal interactive charts + CSV/XLSX export |
| No financing capability | Full asset financing system |
| Manual kWh balance tracking | Automated balance engine |
| No meter comparison | Check meter deviation analysis |
| No real-time data | Live SparkMeter + 1Meter data |
| Single-user at a time | Multi-user concurrent access |
| No audit trail | Full mutation logging |
| No customer self-service | Customer login with personal dashboard |

---

## Appendix: System Architecture

- **Frontend:** React + TypeScript + Vite (deployed to Linux EC2)
- **Backend:** FastAPI + Python (deployed to Linux EC2 via GitHub Actions)
- **Database:** PostgreSQL (`onepower_cc` on EC2)
- **SparkMeter Integration:** Koios API + ThunderCloud API
- **1Meter Integration:** AWS IoT Core → DynamoDB → prototype_sync → PostgreSQL
- **Deployment:** Push to `main` branch → automatic deploy to cc.1pwrafrica.com
