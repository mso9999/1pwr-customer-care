# SMS / Koios / CC corrections — team brief

*Below: WhatsApp-ready copy. In WhatsApp, surround words with single asterisks for **bold** — e.g. `*bold*`.*

---

## WA — copy/paste

*M-Pesa / Koios / CC manual corrections*

*Why:* Shared phone numbers + old phone-based SMS routing caused wrong or duplicate credits. *New API is Remark-first* — past mistakes still need *manual* fixes.

*Rules*
1. Tie every fix to the *M-Pesa receipt ID* + amount.
2. *Koios and 1PDB must match* — if you reverse in Koios, align CC/1PDB (or Finance-approved exception on file).
3. *Reverse the wrong credit first* — then post to the right account if needed (avoid double money).
4. Log: ticket #, receipt, accounts, approver.

*Steps*
1. Collect: receipt screenshot, who *should* be paid, who *was* paid (Koios/CC).
2. Tech: confirm in `onepower_cc` if the wrong row exists (or only Koios is wrong).
3. *Koios:* reverse incorrect credit (memo: `Correction SMS receipt <id>`).
4. *CC/1PDB:* apply offsetting/adjustment only if a wrong row exists — use approved process, not random deletes.
5. Close ticket + update customer if balance changed.

*Escalate — unclear receipt, same receipt twice in both systems, or large amounts.*

*April 2026 examples — verify receipt before acting.*

• *2525342* — M50 — *0022MAS Lipholo* — sms. Malipholo *0047MAS* same phone *26651805744* — if Koios paid both, reverse wrong side.

• *2454731* M30 + *2506424* M10 — *0029MAS Motlatsi* — sms. Shares *26656555880* with *0032MAS Lehlohonolo*.

• *2529057* M30 — *0012MAS Lehlohonolo* — *koios import* (not SMS row) — reconcile vs Motlatsi before reversing.

• *2497662* M10 — *0116MAS Mantaoleng* — sms. Ticket: paid for *Mone 0114MAS* — verify receipt; 1PDB had only 0116 in query window.

*Going forward:* Apply DB migration *009* when ready (audit fields). Ask customers to put *account in M-Pesa Remark* (e.g. *0252SHG*).

*More context:* `CONTEXT.md` in repo; bridge alerts `docs/whatsapp-customer-care.md`

---

## One-line SQL (tech)

`psql $DATABASE_URL` — replace account + dates:

```sql
SELECT t.id, t.transaction_date, t.transaction_amount, t.account_number, t.source, t.payment_reference, c.first_name, c.last_name
FROM transactions t
JOIN accounts a ON a.account_number = t.account_number
JOIN customers c ON c.id = a.customer_id
WHERE t.account_number = 'XXXXXXX'
  AND t.transaction_date >= '2026-04-10'::timestamptz
  AND t.transaction_date <  '2026-04-14'::timestamptz
ORDER BY t.transaction_date;
```
