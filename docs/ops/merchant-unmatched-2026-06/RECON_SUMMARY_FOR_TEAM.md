# M-Pesa Merchant Payments — Reconciliation Summary for the Team

**Date:** 11 June 2026 · **Scope:** Lesotho, merchant-line (PayMerchant) payments, April–June 2026
**Prepared for:** O&M / Customer Care team

---

## 1. What this was about

Customers can pay us two ways on M-Pesa: the normal **pay-bill SMS flow** (which CC captures
automatically) and the **merchant line** ("sent to One Power Lesotho Merchant"). Merchant-line
payments do **not** reach our SMS system — they only enter CC when we import the monthly
merchant statements. Two problems came together (found via Tankiso Potsane, **0231MAK**):

1. Payments made **before** the customer's account existed in CC could not be matched, and
   were silently dropped.
2. The merchant statement import had not been run since 12 May, so newer payments were missing.

## 2. What has been recovered — no action needed

All recovered payments are booked with their **original payment dates and M-Pesa receipt
numbers**, fees are verified and fee-debts cleared, and balances are unaffected (historical
records only).

| Recovery step | Payments | Notes |
|---|---:|---|
| Missed payments from statement re-import | 19 (M5,888) | incl. 0231MAK's M501 connection fee + M30 electricity |
| Matched to existing accounts (bulk claim) | 40 | references cited accounts that already exist |
| Site-code typo fixes (e.g. 0034SEM → 0034SEH) | 2 (M1,000) | TORIKI HULANE's connection + readyboard fee pair |
| Matched by payer phone (unique match) | 10 | e.g. 8 × M200 to 0084SEH, M450 to 0098SEH |
| **Total restored to customer accounts** | **71** | |
| Confirmed already booked (no double credit) | 111 | verified against SMS/portal records |
| Internal 1PWR treasury transfers — fenced off | 8 (M720,000) | not customer money; excluded from all customer data |

## 3. What changed in the system (permanent fixes)

- **Register-with-known-account-number** is live in the New Customer wizard (Location step).
- **Unmatched merchant payments are no longer dropped** — they are parked, and the moment an
  account with the referenced number is registered, the payment **attaches automatically**
  (fees verify and clear fee-debt by themselves).
- Internal treasury transfers are permanently excluded from customer datasets.

**New best practice:** when a customer says they paid on the merchant line but you can't see
the payment — register/verify their account number first; if the payment is in our parked
queue it will attach by itself. If still missing, escalate with the M-Pesa receipt number.

## 4. What we need from you — 2 short tasks

### Task A — 3 payments with unclear account references (10 minutes)

These references don't match any account, and the typo isn't obvious enough to fix safely.
Please confirm the correct account (call the customer if needed) and send it back; we'll
re-point the payment.

| Reference written | Payer name | Phone | Amount | Paid on | Likely intended |
|---|---|---|---:|---|---|
| 0110MAP | PHOMOLO NKHAULI | 26656169247 | M20 | 20 May | 0110 at MAK / MAS / MAT? |
| 0287MAT | lineo korotla | 26657153248 | M50 | 9 Apr | 0287MAT doesn't exist — digit typo? |
| 1101MAS | TIISETSO MAHLEHLA | 26657469985 | M200 | 15 May | 0101MAS? 0110MAS? |

### Task B — 139 payments with no account in the reference (M12,144)

These customers typed only their name (or nothing) as the M-Pesa reference, and their phone
number doesn't match any phone we have on file (or matches several customers). Largest first:

| Payer name | Phone | Payments | Total | Dates |
|---|---|---:|---:|---|
| moeketsi muso | 26658960278 | 1 | M1,500 | 4 Apr |
| Thakabanna Nyokana | 26658881214 | 2 | M1,000 | 5 May |
| RESITSUOE MOKOSHOKA | 26657670936 | 1 | M1,000 | 7 May |
| MAKHAHLISO KHONYANE | 26658088812 | 2 | M1,000 | 11 May |
| Phallang Moletsane | 26659456772 | 2 | M1,000 | 13 May |
| Kananelo Potsane | 26659467293 | 2 | M647 | 1 Apr – 8 May |
| ZABALESE MOHLEKOA | 26658044650 | 1 | M501 | 24 May |
| MOREMOHOLO MONAMOLELI | 26658355570 | 1 | M499 | 19 May |
| Monyane Thoola | 26657044835 | 1 | M300 | 22 May |
| MATSEPO THIBINYANE | 26659522045 | 1 | M300 | 28 May |
| …and 94 more payers | | 129 total | M12,144 total | full list: `no_reference_payments.csv` |

**How to action:** identify the customer's account from the name (site staff usually know
them). Then the **fastest fix is to add/update the phone number on the customer's profile
in CC** — once the phone is on file, tell us and we'll re-run the matcher, which will book
all of that payer's parked payments automatically. Alternatively send us
`receipt → account` pairs and we'll book them directly.

> ⚠ Note for the team: M501 / M499 amounts in this list are likely **connection /
> readyboard fees** from customers who may not be registered in CC at all yet — same
> situation as 0231MAK. Those should be prioritised, as the customer is probably waiting
> for a connection.

## 5. Customer-facing guidance (reduce future cases)

When guiding customers to pay on the merchant line, ask them to put **only their account
number** (e.g. `0231MAK`) in the M-Pesa reference — no names, no extra words, and to double-
check the site letters. Wrong/missing references are the single cause of all of the above.

---

*Files: `registration_worklist.csv` (Task A), `no_reference_payments.csv` (Task B), both in
`docs/ops/merchant-unmatched-2026-06/`. Engineering contact: MSO. Technical details in
`SESSION_LOG.md` (2026-06-11 entries).*
