# Onboarding workbook field mapping

Source workbook: `1PWR OM TEAM/20. Dashboards/Customer Onboarding Data_Jan-01, 2026.xlsx`

## Customer Records

| Workbook column | 1PDB target |
|-----------------|-------------|
| Concession | `customers.community` |
| Village | `customers.village` (if present) or notes |
| Registration Date | `customers.registration_date` / `created_at` |
| First name / Last name | `customers.first_name`, `customers.last_name` |
| ID number | `customers.national_id` |
| Phone Number | `customers.phone_number` |
| Customer ID | `accounts.account_number` |
| PLOT NUMBERS | `accounts.survey_id` |
| GPS Coordinates | `customers.gps_lat`, `customers.gps_lon` |
| Connection Fee Amount / Date Paid_CF / Transaction ID_CF | `connection_fee_paid`, `connection_fee_paid_date`; `payment_verifications` + `transactions.payment_reference` |
| Readyboard Payment Amount / Date Paid_RB / Transaction ID_RB | `readyboard_fee_paid`, `readyboard_fee_paid_date`; `payment_verifications` |
| Readyboard (Y/N) / Date Installed | `readyboard_installed`, `readyboard_installed_date` |
| Readyboard Test (Pass/Fail) / Date of Test | `readyboard_tested`, `readyboard_tested_date` |
| House Wiring Test (Pass/Fail) / Date of Test_HW | `house_wiring_test_passed`, `house_wiring_test_date` |
| Airdac (Y/N) / Date Installed_AD | `airdac_connected`, `airdac_connected_date` |
| Smartmeter (Y/N) / Date Installed_SM / Smartmeter number | `meter_installed`, `meter_installed_date`; `meters.meter_serial` |
| Commisioned?(Y/N) / Commissioning Date | `customer_commissioned`, `customer_commissioned_date` |
| Notes | `customers.notes` |

## MAK records

| Workbook column | 1PDB target |
|-----------------|-------------|
| Customer Code | `accounts.account_number` |
| Date of payment of Connection fee | `connection_fee_paid` + date |
| Date of Readyboard Payment | `readyboard_fee_paid` + date |
| Contract / proof Dropbox links | `payment_proofs.external_url` or note |

## Mak plot numbers

| Workbook column | 1PDB target |
|-----------------|-------------|
| Customer Code | `accounts.account_number` |
| Plot No | `accounts.survey_id` |
| Meter Code | `meters.meter_serial` |

Import tag: `customers.onboarding_import_tag = onboarding_import_2026-01` on rows touched by `import_onboarding_workbook.py`.
