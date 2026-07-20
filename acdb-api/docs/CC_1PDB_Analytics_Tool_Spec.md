# CC/1PDB Analytics Tool Specification

## Purpose
Build a reusable analytics layer on top of the cc.1pwrafrica.com / 1PDB platform that can produce investor-grade data products (Zafiri workbook sheets 04 & 05, board pack KPIs, lender DD responses) on demand — not as one-off extractions.

## Current State

### Known API Endpoints
| Endpoint | Method | Returns | Status |
|----------|--------|---------|--------|
| `/api/sites` | GET | `{sites: [{concession, customer_count, country}], total_sites}` | Working |
| `/api/transactions` | GET | — | 404 (not yet exposed) |
| `/api/consumption` | GET | — | 404 |
| `/api/payments` | GET | — | 404 |
| `/api/customers` | GET | — | 404 |
| `/api/meters` | GET | — | 404 |

### Underlying Data Systems
- **SparkMeter**: Prepaid metering platform — stores customer accounts, transactions, energy sales (kWh), tariff plans, connection/disconnection events per site
- **1PDB**: Internal project database (cc.1pwrafrica.com) — stores site metadata, customer counts, concession info
- **Odoo** (Benin): ERP for Benin operations — customer billing, invoiced (non-prepaid) customers
- **O&M Quarterly Reports**: PDF reports (MGR009_SMP_QX_YYYY) — manually compiled from SparkMeter exports + site technician logs

### Data Gap
The cc.1pwrafrica.com API currently only exposes `/api/sites` (aggregate customer counts). To produce sheets 04 & 05 automatically, we need transaction-level and consumption-level data that lives in SparkMeter but is not yet exposed through the cc.1pwrafrica.com API.

---

## Required Data Products

### Product 1: Operating Asset Register (Zafiri Sheet 04)

**Output**: One row per energised site with:
- Site name & abbreviation (e.g., "Ha Makebe (MAK)")
- Country, Region/State
- Status (Operational / Energising / Construction / Pipeline)
- Commissioning date
- Solar PV (kWp)
- Battery (kWh)
- Thermal/genset (kW)
- **Total connections** (from `/api/sites` customer_count)
- **Active connections** (customers with a transaction in the last 90 days)
- **of which residential** (HH tariff plan customers)
- **of which SME** (SME tariff plan customers)
- **of which C&I/anchor** (clinic/institutional customers — invoiced, not prepaid)
- **Avg tariff (USD/kWh)** (from SparkMeter tariff plans)
- Concession/permit & expiry (static metadata, maintained in 1PDB)
- Metering / payment tech (e.g., "SparkMeter prepaid" or "Odoo invoiced")

**Data Sources**:
- Site metadata (name, country, region, status, commissioning, PV/battery/genset): 1PDB site records or static config
- Connection counts: `/api/sites` (already available)
- Active vs total, customer type split: SparkMeter API (needs integration)
- Tariff: SparkMeter tariff plan per site
- Concession/permit: 1PDB document registry (static)

**SparkMeter Data Needed**:
- Customer account list per site with: account status (active/inactive), tariff plan name, connection date, last transaction date
- Customer type classification: residential (LSL 5/kWh prepaid), SME (LSL 5/kWh prepaid, business flag), C&I/anchor (invoiced clinics at LSL 5.40/kWh)

### Product 2: Operating KPIs (Zafiri Sheet 05)

**Output**: 8-quarter time series with:
- **Total connections (cumulative)**: Count of all customer accounts per quarter end
- **Active connections**: Accounts with ≥1 transaction in the quarter
- **New connections added**: Accounts first activated in the quarter
- **Disconnections / churn**: Accounts deactivated or zero consumption for 90+ days
- **Energy sold (kWh)**: Total kWh consumed (prepaid + invoiced) per quarter
- **Revenue (USD)**: Total revenue (prepaid sales + invoiced collections) in USD
- **ARPU (USD/connection/month)**: Revenue / active connections / months
- **Average tariff (USD/kWh)**: Blended tariff across customer types
- **Collection rate (%)**: For prepaid ~100%; for invoiced, collections/revenue billed
- **System availability / uptime (%)**: Site-level uptime (from SCADA / site logs — may need separate integration)
- **Productive-use share (% of energy)**: SME + C&I kWh / total kWh
- **Opex per connection (USD/yr)**: From financial system (Odoo / manual)
- **EBITDA (USD)**: From financial system
- **EBITDA per connection (USD/yr)**: Derived
- **CAPEX deployed (USD)**: From project financial model / asset register
- **CAPEX per connection (USD)**: Cumulative capex / total connections

**Data Sources**:
- Connection metrics: SparkMeter account lifecycle data (connection dates, deactivation dates)
- Energy & revenue: SparkMeter transaction log (kWh sold, revenue collected) + Odoo invoiced revenue
- ARPU, tariff: Derived from above
- Availability: Site SCADA / inverter log data (SMA / Victron) — separate integration
- Opex/EBITDA/Capex: Financial system (Odoo / Excel books) — not in SparkMeter

**SparkMeter Data Needed**:
- Transaction log per site: timestamp, kWh, amount (LSL), customer account, tariff plan
- Account lifecycle: account ID, site, connection date, status changes, tariff plan
- Aggregatable by: site, quarter, customer type, tariff plan

---

## Technical Architecture

### Phase 1: SparkMeter Integration (Minimum Viable)

```
SparkMeter API → ETL worker → cc.1pwrafrica.com API → Analytics endpoints
```

1. **SparkMeter API client**: Authenticate to SparkMeter's API (each site has a SparkMeter hub). Pull:
   - `GET /api/v1/accounts` — customer accounts per site
   - `GET /api/v1/transactions` — prepaid sales transactions
   - `GET /api/v1/tariff-plans` — tariff configurations

2. **ETL worker** (scheduled, e.g., nightly):
   - Pull incremental data from SparkMeter for each site
   - Normalize to common schema (site_code, account_id, timestamp, kWh, revenue_local, currency, customer_type, tariff_plan)
   - Store in cc.1pwrafrica.com database (PostgreSQL or similar)

3. **New cc.1pwrafrica.com API endpoints**:
   - `GET /api/sites/{concession}/customers` — customer list with type, status, connection date
   - `GET /api/sites/{concession}/transactions` — transaction log (paginated, date-filterable)
   - `GET /api/kpis?period=quarter&start=Q2-2024&end=Q1-2026` — aggregated KPI series
   - `GET /api/sites/{concession}/kpis?period=quarter` — per-site KPI breakdown

### Phase 2: Invoiced Revenue (Benin Clinics)

Benin clinic/institutional customers are invoiced (not prepaid). Need:
- Odoo API integration for invoiced revenue
- Or manual CSV upload pipeline for clinic consumption data

### Phase 3: Availability & Financial Metrics

- **Availability**: Integrate with inverter/SCADA systems (SMA Sunny Portal, Victron VRM) for site uptime
- **Opex/EBITDA**: Integrate with Odoo financial reports or accept manual input
- **CAPEX**: Pull from project financial model (FM workbook) or asset register

### Phase 4: On-Demand Data Product Generator

```
Template engine (Jinja2/openpyxl) + cc.1pwrafrica.com API → Excel/PDF output
```

- Define workbook templates (Zafiri sheets 04, 05; board pack KPI tables; lender DD responses)
- Pull live data from `/api/kpis` and `/api/sites/{concession}/customers`
- Generate populated Excel sheets on demand
- Schedule periodic exports (quarterly board pack auto-population)

---

## Data Schema (Proposed)

### `sites` table (existing, extend)
| Field | Type | Source |
|-------|------|--------|
| concession | string PK | 1PDB |
| country | string (LS/BN/ZM) | 1PDB |
| region | string | 1PDB |
| status | enum | 1PDB |
| commissioning_date | date | 1PDB |
| pv_kwp | float | 1PDB |
| battery_kwh | float | 1PDB |
| thermal_kw | float | 1PDB |
| tariff_usd_kwh | float | SparkMeter |
| concession_expiry | date | 1PDB |
| metering_tech | string | 1PDB |

### `customers` table (new)
| Field | Type | Source |
|-------|------|--------|
| account_id | string PK | SparkMeter |
| concession | string FK | SparkMeter |
| customer_type | enum (HH/SME/C_I) | SparkMeter tariff plan |
| tariff_plan | string | SparkMeter |
| connection_date | date | SparkMeter |
| status | enum (active/inactive/disconnected) | SparkMeter |
| last_transaction_date | date | Derived |

### `transactions` table (new)
| Field | Type | Source |
|-------|------|--------|
| id | string PK | SparkMeter |
| account_id | string FK | SparkMeter |
| concession | string FK | SparkMeter |
| timestamp | datetime | SparkMeter |
| kwh | float | SparkMeter |
| amount_local | float | SparkMeter |
| currency | string (LSL/XOF) | SparkMeter |
| amount_usd | float | Derived (FX conversion) |
| customer_type | string | Derived from account |

### `kpis_quarterly` view (derived)
| Field | Type | Derivation |
|-------|------|-------------|
| quarter | string (e.g., "Q1-2026") | — |
| concession | string (or "ALL" for portfolio) | — |
| total_connections | int | COUNT customers where connection_date <= quarter_end |
| active_connections | int | COUNT customers where last_transaction in quarter |
| new_connections | int | COUNT customers where connection_date in quarter |
| disconnections | int | COUNT customers where status changed to disconnected in quarter |
| energy_kwh | float | SUM transactions.kwh in quarter |
| revenue_usd | float | SUM transactions.amount_usd in quarter |
| arpu_usd_month | float | revenue_usd / active_connections / 3 |
| avg_tariff_usd_kwh | float | revenue_usd / energy_kwh |
| productive_use_share | float | SUM kwh WHERE customer_type IN (SME, C_I) / total kwh |

---

## Site Mapping

### Lesotho (SMP) — 11 minigrids + 7 clinics
| Code | Site Name | Type | Country |
|------|-----------|------|---------|
| MAK | Ha Makebe | Minigrid | LS |
| MAS | Mashai | Minigrid | LS |
| SHG | Sehonghong | Minigrid | LS |
| LEB | Lebakeng | Minigrid | LS |
| SEH | Sehlabathebe | Minigrid | LS |
| MAT | Matsoaing | Minigrid | LS |
| TLH | Tlhanyaku | Minigrid | LS |
| TOS | Tosing | Minigrid | LS |
| SEB | Sebapala | Minigrid | LS |
| RIB | Ribaneng | Minigrid | LS |
| KET | Ketane | Minigrid | LS |
| NKU | Nkau | Clinic | LS |
| MET | Methalaneng | Clinic | LS |
| BOB | Bobete | Clinic | LS |
| MAN | Manamaneng | Clinic | LS |
| LSB | Lesotho broad (unassigned) | Other | LS |

### Benin (Mionwa) — sites in development
| Code | Site Name | Type | Country |
|------|-----------|------|---------|
| GBO | Gbegbowele (?) | Minigrid | BN |
| SAM | Samionta | Minigrid | BN |

### Customer Type Classification Rules
- **Residential (HH)**: Tariff plan = standard prepaid LSL 5.00/kWh (LS) or ~270 CFA/kWh (BN)
- **SME**: Tariff plan = prepaid with business flag or separate SME plan
- **C&I/Anchor (Clinic)**: Invoiced (not prepaid), tariff LSL 5.40/kWh (LS clinics), institutional accounts
- **Unknown (UNK)**: Accounts not yet classified — flag for review

---

## FX Conversion
- **Lesotho**: LSL to USD at period-average rate (~17.5 LSL/USD as of 2026)
- **Benin**: XOF to USD at period-average rate (~600 XOF/USD)
- Store FX rates in a `fx_rates` table with effective dates

---

## Priority Deliverables for Agent

1. **SparkMeter API integration**: Build a Python client that authenticates and pulls accounts + transactions per site hub. Store in cc.1pwrafrica.com database.
2. **KPI aggregation service**: Build the `kpis_quarterly` view that aggregates transactions into quarterly metrics per site and portfolio-wide.
3. **New API endpoints**: Expose `/api/kpis`, `/api/sites/{concession}/customers`, `/api/sites/{concession}/transactions`.
4. **Excel export tool**: Python script using openpyxl that calls the API and populates Zafiri workbook templates (sheets 04, 05) on demand.
5. **Customer type classifier**: Map SparkMeter tariff plans to HH/SME/C&I categories.

## Non-SparkMeter Data (Manual Input Required)

These metrics cannot be derived from SparkMeter and need separate data sources:
- **System availability/uptime**: From SCADA/inverter logs (SMA Sunny Portal, Victron VRM)
- **Opex per connection**: From Odoo financial reports or manual accounting
- **EBITDA**: From financial statements (SMP management accounts)
- **CAPEX deployed**: From project financial model (Corporate Raise FM workbook)
- **Concession/permit expiry dates**: From 1PDB document registry (legal documents)

## File Paths (Reference)
- Zafiri workbook: `docs/Zafiri/ZAFIRI_Information_Request_1PWR_DRAFT.xlsx`
- Corporate Raise FM: `/Users/mattmso/Dropbox/1PWR/1PWR PIM/3) Corporate Raise/2) FM/251021 1PWRA_FM_v2.xlsx`
- O&M Reports: `/Users/mattmso/Dropbox/1PWR/1PWR OM TEAM/2. Mini-Grids and Off-Grid Combined Reports/3. Quarterly Reports/`
- Org Chart (latest): `/Users/mattmso/Dropbox/1PWR/1PWR HR/16 - Org chart - C-Level controls editable version/D041V22 1PWR Org Chart (2026-03).pptx`
- Company Profile (latest): `/Users/mattmso/Dropbox/1PWR/1PWR GAIA/7) Presentations/1) Company Profile/D027V26 1PWR Company Profile.pdf`
- Grant Agreements (LS): `/Users/mattmso/Dropbox/1PWR/1PWR PIM/2) 1PWR Projects/1) 1PWR LS MG/5) LS Grant Agreements/`
- Grant Agreements (BN): `/Users/mattmso/Dropbox/1PWR/1PWR PIM/2) 1PWR Projects/2) 1PWR BN MG/5) BN Grant Agreements/`
- PIDG DD Tracker: `/Users/mattmso/Dropbox/1PWR/1PWR PIM/3) Corporate Raise/3) DD/PIDG/260127 PIDG DD RFI Tracker.xlsx`
