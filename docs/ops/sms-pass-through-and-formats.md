# SMS gateways as pass-through, parsing on CC (SMS Formats)

**Status 2026-09-27:** code ready, not deployed. See the cutover checklist below.

## What changes

| Before | After |
|---|---|
| Gateway PHP (`receive.php`) matched SMS against `smstypes`/`smselements` word positions, wrote typed files, and a cron file watcher credited SparkMeter / sent balance replies. CC parsed the forwarded copy with code (`mpesa_sms.py`, `momo_bj.py`). | Gateway acknowledges the Android app and **forwards the raw payload to CC**, with retries. CC decides what each SMS is using **SMS Formats** (editable in the portal), then the built-in parsers. |
| Forward: fire-and-forget, 3 s timeout, failure only written to `LOGIN.TXT`. | Forward: 15 s after the app has been answered; on failure the payload is queued in `/home/npower5/cc_outbox/<host>/` and retried on later requests and by cron `cc_forward_retry.php`. |
| `/api/sms/incoming` accepted any POST. | Gateway sends `X-Gateway-Key`; CC checks it (`SMS_INGEST_GATEWAY_KEY_MODE` = `off` / `warn` (default) / `enforce`). |
| Any SMS that looked like a payment was credited, whoever sent it. | Optional **trusted senders** list per country (`off` / `warn` / `enforce`), e.g. `MTN MoMo`, `MPESA`, `199`. |
| New provider or changed template = code change + deploy. | Admin / IT / O&M add a format in **CC → System → SMS Formats**, test it on a real SMS, preview its effect on the last 30 days, then enable it. |

## SMS Formats page (`/admin/sms-formats`)

Access: CC roles `superadmin` or `onm_team` (IS&T departments map to `onm_team`), or Nexus `administer_cc`. Everything is audited in `sms_format_audit`.

- **Template** (recommended): paste a real SMS and replace the variable parts, e.g.
  `Paiement {amount}F de {*} ({phone}) {*} Message:{account} {*} ID:{txn_id}`.
  `{amount}` is required for payments; `{account}`, `{phone}`, `{txn_id}`, `{remark}` optional; `{*}` skips text.
- **Regex**: Python regex with the same named groups.
- **Sender must match** (optional regex on the gateway `from`): a format with this counts as a trusted sender.
- **Samples**: real SMS with the expected amount / account. Required to enable, and re-checked on every save.
- **Test** runs one SMS through the draft and through the whole pipeline, including which account it would credit.
- **Preview** compares the last 30 days of `sms_inbound_log` before/after the draft (newly recognised / read differently / no longer recognised).
- **Unprocessed SMS** tab lists `unparsed` / `no_account` / `untrusted_sender` SMS with how they would parse now. **Replay** credits them through the normal path. It skips SMS already credited (same receipt) and flags a similar non-SMS payment (e.g. entered on Record Payment) as a possible duplicate, which is skipped unless explicitly allowed.
- **Balance requests**: add a format of type *Balance request* (e.g. `Balance {account}` / `Solde {account}`) and turn on *Customer Care replies to balance requests* in Settings. Without an account in the SMS, CC replies for all accounts on the sender's phone. Uses the existing gateway balance rate limits.

Formats are tried in priority order (lowest first), before the built-ins. A new format applies within 30 s (per-process cache).

## Cutover checklist

Do in this order; each step is reversible.

1. **Deploy CC** (migration `072_sms_formats.sql` + code). Nothing changes for customers: no formats, trusted senders `off`, balance replies off, gateway key `warn`.
2. **Gateway key.** Pick a new random key (the old default `1pwr-sms-gateway-2026` is in git; treat it as public). Set it as `SMS_GATEWAY_KEY` in `/opt/1pdb/.env` and `/opt/1pdb-bn/.env` (different keys per country are fine), restart the APIs, and put the same value in `/home/npower5/cc_gateway_key.php` on the cPanel host: `<?php return 'THE_KEY';` (chmod 600). Both the forwarder and the Lesotho balance helper `sparkmeter/cc_1pdb_gateway.php` read that file, so deploy the updated helper in the same step. If Lesotho and Benin get different keys, Benin's gateway needs its own env `CC_GATEWAY_KEY` instead of the shared file.
3. **Deploy gateways** (manual cPanel; archive first — `docs/ops/sms-gateway-cpanel-deploy.md`):
   - Lesotho: `receive.php`, `cc_forward.php`, `cc_forward_retry.php` (SMSComms).
   - Benin: same three files (SMSComms-BN). **Pushing SMSComms-BN `main` auto-deploys** via its webhook.
   - Add cron (every 5 min) per host: `cd <docroot> && php cc_forward_retry.php <host>`.
   - Check `cc-api` logs: `SMS incoming accepted without a valid X-Gateway-Key` should stop. Then set `SMS_INGEST_GATEWAY_KEY_MODE=enforce`.
4. **Benin legacy watcher** (`sparkmeter_Benin`, on `sparkmeter`-side host): deploy `env.php` + `new_file_watcher.php` with `$LEGACY_FILE_WATCHER_CREDIT_ENABLED = false`. Diff against the live files first — the GitHub copy is from 2024. Before that, check Koios for BN SparkMeter customers (SAM / OCE / UEF) credited twice for the same MoMo ID (watcher + CC); if found, that is a live double-credit to reconcile.
5. **Trusted senders**: SMS Formats → Settings → *Senders seen in the last 30 days*. Add the provider senders (BN: `MTN MoMo` — the legacy watcher's `$PERMITTED_SENDERS`; LS: M-Pesa / EcoCash sender ids as seen), mode `warn`, watch logs for a week, then `enforce`.
6. **Balance replies to CC** (per country, same hour): add the balance-request format(s), enable CC replies, then switch the gateway off: `SMS_LEGACY_TYPED_FILES=0` for the Lesotho gateway PHP, `$LEGACY_BALANCE_REPLIES_ENABLED = false` for Benin. Check `sms_outbound_log` for one reply per request.
7. After a clean week, the `smstypes` / `smselements` config pages and the file watchers can be retired.

## Benin, 2026-09-27

MTN merchant SMS `Paiement 10F de … Message:0001KOT Solde:10563519F ID:12985` was left `unparsed` (built-in only accepted `FCFA|XOF|CFA`). Fixed in `momo_bj.py` (bare `F` after a payment keyword; `Solde:` never read as the amount; 10-digit `01…` numbers kept). That payment was recorded manually by Nils; **do not replay log id for `ID:12985`** unless the manual row is removed — the replay screen flags it as a possible duplicate.

Unknown: the "SMS reçus" dashboard (Total / Traités / Échoués, Code client, Exporter Excel) in Nils's screenshot is not `smsbn.1pwrafrica.com` (old PHP login page) and is not in any onepowerLS repo. Find which host it runs on and confirm the Benin gateway phones POST to `smsbn.1pwrafrica.com/receive.php` (or that the dashboard forwards to CC the same way).
