# RCA — `transactions.current_balance` unit drift (kWh vs currency)

**Date:** 2026-05-07
**Author:** CC engineering
**Severity:** Low (cosmetic / data-quality)
**Status:** Resolved going forward; historical rows left untouched
**Trigger:** Discovered while wiring the connection / readyboard fee classifier and `account_advances` ledger (session 202605070030).

---

## Summary

The `transactions.current_balance` column is a per-row, denormalised "balance after this transaction" snapshot. **Different writers have been writing different units into it since 2026-02 without anyone noticing**, because the canonical user-facing balance (the big stat card on the customer page, the auto-cutoff guard, the dashboards) is computed from full history by `balance_engine.get_balance_kwh()` and never reads `current_balance`.

| Writer | File | Unit written | Introduced |
|---|---|---|---|
| `record_payment_kwh` | `acdb-api/balance_engine.py` | **kWh** | 2026-02-19 |
| `record_fee_transaction` | `acdb-api/balance_engine.py` | kWh (no change) | 2026-05-07 |
| SMS ingest (M-Pesa / MoMo) | `acdb-api/ingest.py` | **currency** (legacy ACCDB-era convention) | 2026-02-18 |
| Manual CRUD form | `acdb-api/crud.py` (UPSERT) | user-supplied (no unit) | n/a |
| Balance-seed reconciliation | `scripts/ops/audit_bn_balances.py` | **`0`** literal placeholder | 2026-04 |

So a single customer's transaction history can — and in production already does — show `current_balance` as kWh in one row and currency in the next.

---

## Root cause

The kWh balance engine (`balance_engine.py`) was introduced **one day after** the SMS ingest path (`ingest.py`). The new module wrote `current_balance` in kWh to match the auto-cutoff hook (`new_balance <= 0` only makes sense in kWh, since a customer with negative currency and positive kWh should not be cut off). The SMS path was never refactored to match — the original ACCDB-style "running currency total" was preserved verbatim, and it has never been touched since.

The drift was masked because:

1. **`current_balance` is never read by `get_balance_kwh()`.** The canonical balance for the customer page Stat card, the relay auto-cutoff hook, the financial reports, and the audit scripts all recompute from full history (`SUM(kwh_value WHERE is_payment) - consumption`). The only self-referential read of `current_balance` is `ingest.py` itself — a closed loop that is internally consistent in currency.
2. **The UI column was labelled "Balance" with no unit.** Both the Transactions page and the Customer Data history table render the raw column as `txn.balance.toFixed(1)`. With no unit suffix, a 87.50 LSL row and an 87.50 kWh row look identical.
3. **No business logic depends on it.** Auto-cutoff, OM reports, financial dashboards, monthly_transactions rollups — none read `current_balance`.

This is the textbook "denormalised cache nobody validates" failure mode.

---

## Impact assessment

| Surface | Affected? | Why |
|---|---|---|
| Customer-facing kWh balance Stat card (`CustomerDataPage`) | **No** | Reads `balance_kwh` from `get_balance_kwh()` |
| Relay auto-cutoff (`maybe_auto_open_relay`) | **No** | Uses kWh-denominated `new_balance` returned by `record_payment_kwh` |
| Financial / OM reports | **No** | Recompute from `transaction_amount`, `kwh_value`, `hourly_consumption` |
| BN audit / Koios reconciliation | **No** | Recomputes balances from scratch |
| Transactions list table (`TransactionsPage`) | **Cosmetic** | Showed mixed units in the "Balance" column |
| Customer history table (`CustomerDataPage`) | **Cosmetic** | Showed mixed units in the "Balance" column |

**No billing, cutoff, or reconciliation correctness was at risk.** The visible symptom would have been an operator looking at the "Balance" column on a single customer and seeing values that don't add up cleanly between rows.

---

## Fix

**Going-forward (deployed in this commit):**

1. **`acdb-api/ingest.py`** — SMS path now reads `prev_balance` via `get_balance_kwh()` and writes `prev_balance + kwh` (kWh) instead of `prev_balance + amount` (currency). All four cascading INSERT branches share the now-kWh `new_balance`.
2. **`acdb-api/balance_engine.py`** — `record_payment_kwh` docstring updated to explicitly state that `current_balance` is a kWh-denominated denormalised snapshot, never read by the canonical balance code, and that all writers must keep it consistent in kWh.
3. **Frontend i18n (`en` + `fr`)** — Column header changed from `"Balance"` to `"Balance (kWh)"` in `transactions.json` and `customerData.json` so the unit is unambiguous.

**Historical data — left as-is, deliberately.**

- Backfilling 3 months of mixed-unit rows would require recomputing the running kWh balance for every account from inception, in chronological order, in a single transaction — high blast radius, low payoff (the column is cosmetic).
- The "step jump" from currency-balance rows to kWh-balance rows on the deploy date is more honest than a backfilled rewrite, and it's clearly explained by the column re-labelling.
- No reconciliation script reads `current_balance`, so the historical mixed values cannot leak into ledgers or billing.

---

## Why we did *not* drop the column

`current_balance` is the only column on `transactions` that records a per-row snapshot. It survives because:

1. **Operator UX** — the Transactions page lets ops scan a customer's history and verify "balance after each event is what I expect"; recomputing per-row from scratch would be expensive on the SQL side.
2. **Audit immutability** — the snapshot at write-time is tamper-evident; a recomputed balance would change if upstream rows are corrected.
3. **Manual override path** — the CRUD form lets ops backdate a `current_balance` value when ingesting historical statements; removing it would break the existing balance-seed workflow used by `audit_bn_balances.py`.

Keeping the column but enforcing a single unit is the right balance.

---

## Followups (non-blocking)

- [ ] Add a CI lint that flags any new `INSERT INTO transactions … current_balance` outside `balance_engine.py` so future contributors can't reintroduce the drift.
- [ ] Consider adding a `current_balance_unit` enum column (`kwh` / `currency` / `seed`) for full backfill clarity. Low priority — the unit is now uniform from this commit forward.
- [ ] When the BN-LS audit scripts next run, they will see kWh in the snapshot column for new SMS rows; confirm the audit doesn't accidentally start parsing it (spot-checked: it doesn't).

---

## Related work

- Migration 019 (`account_advances`, `account_advance_ledger`) introduced the connection / readyboard fee classifier and advance ledger in the same session. Those flows always wrote kWh-denominated `current_balance` (via `record_payment_kwh` / `record_fee_transaction`), so they were already correct. Discovering this RCA was a side effect of auditing the SMS path while wiring the advance split into ingest.py.
- `scripts/ops/accrue_advance_fees.py` runs monthly and never touches `current_balance`; advances accrue into `account_advance_ledger` only.
