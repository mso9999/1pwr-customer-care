# SMS gateway PHP vs CC (`mpesa_sms.py`) — what matches what

**Repo:** [onepowerLS/SMSComms](https://github.com/onepowerLS/SMSComms) (Lesotho gateway, `sms.1pwrafrica.com`).

## `read_payment_file.php` — **not** SMS body parsing

`read_payment_file.php` and `sparkmeter/read_payment_file.php` read **CSV-like file contents** (comma-separated fields) produced by the **legacy payment-file pipeline**, not the raw SMS string.

- Last field **`MPESA`** → M-Pesa branch: fields are `trans_id`, amount, payer phone, sender id.
- Last field **`199`** → EcoCash branch: same idea; `payment_provider = "ecocash"`.

So “EcoCash” in PHP here means **row shape + trailing sender token `199`**, analogous to **`PERMITTED_SENDERS = ["MPESA", "199"]`** in `env.php`. It does **not** implement templates like *“You have received M25 … for 0118mat”*.

## CC mirror path — **natural language** SMS

`receive.php` mirrors JSON to **`POST /api/sms/incoming`** on CC. **`acdb-api/mpesa_sms.py`** parses **`content`** and **`from`** with regexes (`parse_ls_sms_payment` → `parse_mpesa_sms`, then EcoCash-specific patterns, sender **199** hint, etc.). Account tokens use **`candidate_accounts_from_text`** / `ACCOUNT_TOKEN_RE` (Remark-first, then full body, then phone fallback in `resolve_sms_account`).

**Recovery from git:** The SMSComms PHP above does **not** contain extra NL SMS templates to “port back” into Python; the gap templates (e.g. Econet *You have received M… for …mat*) are added only on the CC side.

## `util.php` — account token in logs

`$ACCOUNT_NUMBER_PATTERN = "/[0-9]{4}[A-Z\s]{3}/"` is used when scraping **LOGIN.TXT** JSON for account codes in legacy flows. CC uses a related but not identical pattern (`\d{3,4}` + site letters) for SMS text.

## Operational summary

| Layer | Role |
|-------|------|
| SMSComms `read_payment_file.php` | Parse **structured files** for old SparkMeter file watcher (if still deployed). |
| CC `ingest.py` + `mpesa_sms.py` | Parse **mirrored SMS JSON**, write 1PDB, `credit_sparkmeter` (Koios / ThunderCloud). |

Disable double-crediting: do not run legacy PHP payment-file credit to SparkMeter alongside CC for the same payment — see `CONTEXT.md` and `docs/ops/sms-gateway-cpanel-deploy.md`.
