# Merchant unmatched-payments worklist — 2026-06-11

Outcome of the merchant-export re-run + holding-queue triage (see `SESSION_LOG.md`
2026-06-11 entries). State of the queue after automated claiming:

| Bucket | Rows | Amount | Action |
|---|---|---:|---|
| Claimed automatically (existing accounts, no duplicate) | 40 | — | DONE — booked with original dates/receipts |
| Resolved as duplicates (already booked via SMS/other channel) | 100 | — | DONE — marked resolved, nothing booked |
| Treasury transfers (ring-fenced) | 8 | M720,000 | none — internal money movements |
| **Account-reference typos** (`registration_worklist.csv`) | **9 refs / 10 payments** | **M2,690** | **O&M: verify & fix** |
| **No usable account reference** (`no_reference_payments.csv`) | **155** | **M15,694** | O&M: resolve by phone/name |

## `registration_worklist.csv` — likely site-code typos, NOT new registrations

Customers mistyped the site code in the M-Pesa reference. Most are obvious:

- `0027SGH` → almost certainly **0027SHG** (LETHUSANG QAMO)
- `0034SEM`, `0094SEM` → **SEH** (TORIKI HULANE, Maretsepile Tsiu)
- `0065KTE`, `0114KTE`, `0170KTE` → **KET**
- `0110MAP` → MAT? (PHOMOLO NKHAULI)
- `0287MAT` → exists? (lineo korotla — verify against MAT roster)
- `1101MAS` → 0101MAS? 0110MAS? (TIISETSO MAHLEHLA)

**Process per row:** confirm the real account from the payer name/phone (call if needed),
then either (a) if the real account exists in CC, book via Customer Data → Add Transaction
with the original date + receipt, or ask engineering to re-point the parked row; or (b) if
the customer truly isn't in CC, register them via the New Customer wizard **using the
corrected account number** — the parked payment will NOT auto-claim under a typo'd ref, so
flag engineering to re-reference it.

## `no_reference_payments.csv` — no account in the reference

155 payments where the M-Pesa reference has a name/phone but no account number
(e.g. "PayMerchant from 2665... - Name -"). Resolve via payer phone → customer lookup in
CC; many phones map to a unique account. Engineering can bulk-match by phone on request.

## Mechanics reminder

- Parked payments auto-claim when an account with the **exact referenced number** is
  registered (live since 2026-06-11).
- Claims book fees through the full fee path (verification + debt) and electricity as
  ledger-only history (no kWh credit) — they cannot disturb re-anchored balances.
- Treasury rows are category='treasury' and can never be claimed.
