# Benin SMS → 1PDB — what’s done vs missing

## Verified (no cPanel needed)

| Layer | Status |
|--------|--------|
| **CC API** (`1pdb-api-bn`, `COUNTRY_CODE=BN`) | `POST /api/sms/incoming` + MoMo parsing (`momo_bj`) implemented. |
| **Public URL** | `https://cc.1pwrafrica.com/api/bn/sms/incoming` responds (Caddy strips `/api/bn` → backend `/api/sms/incoming` on **:8101**). |
| **1PDB `onepower_bj`** | **`source = sms_gateway` count = 0** — no payment has been recorded via this pipe yet. |

## Missing (outside this Git repo)

The gap is **not** FastAPI code on the CC host; it is the **Benin SMS gateway** mirroring the same JSON to CC that Lesotho does.

1. **`onepowerLS/SMSComms-BN`** (host: **smsbn.1pwrafrica.com**) — `receive.php` (and any helpers) must **HTTP POST** the same **Medic Mobile Gateway JSON** to:

   `https://cc.1pwrafrica.com/api/bn/sms/incoming`  

   (same shape as LS: `{"messages":[{"id","from","content","sms_sent","sms_received",...}]}`).

2. **Parity with Lesotho** — In `SMSComms` (LS), `receive.php` already integrates with the gateway + file watcher; the **BN** tree was **not** updated in the same commit when LS was switched to CC. Until that mirror exists, **only** legacy paths (e.g. local files → Koios) run — **1PDB never sees the SMS**.

3. **Phones / Medic app** — Handsets must still POST to **smsbn** `receive.php` (GET with `User-Agent: medic-gateway` returns `{"medic-gateway":true}` — same as LS). If the app points elsewhere, fix the **Custom Web Service URL** in the gateway app config.

4. **Secrets / firewall** — If the PHP host uses outbound IP allowlists, allow **`cc.1pwrafrica.com`** (or the CC host egress). If the mirror uses a shared secret header, align it with whatever CC expects (LS may use none for JSON body-only).

## What to do in cPanel / repo

- Open **SMSComms-BN** `receive.php` (and any `forward` / `curl` block).
- Compare to **SMSComms** `receive.php` on **sms.1pwrafrica.com** where the **CC mirror** was added.
- Add the **mirror POST to** `https://cc.1pwrafrica.com/api/bn/sms/incoming` after local processing (or in parallel, same as LS).
- Deploy PHP and test with one real MoMo SMS; then **`SELECT COUNT(*) FROM transactions WHERE source = 'sms_gateway'`** on `onepower_bj` should increase.

---

*This repo does not contain the SMSComms-BN PHP sources or cPanel logins; use GitHub `onepowerLS/SMSComms-BN` + hosting panel.*
