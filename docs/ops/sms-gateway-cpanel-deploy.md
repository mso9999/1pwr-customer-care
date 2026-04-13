# SMS gateways — manual cPanel deploy (Lesotho + Benin)

There are **two production gateway stacks**, deployed **separately**:

| Country | Public host | Source in repo | Notes |
|---------|-------------|----------------|--------|
| **Lesotho (LS)** | **sms.1pwrafrica.com** (PHP) + **sparkmeter.1pwrafrica.com** (file watcher / TC helpers) | [SMSComms](https://github.com/onepowerLS/SMSComms) repo root + `sparkmeter/` | `receive.php` mirrors JSON to `https://cc.1pwrafrica.com/api/sms/incoming`. Legacy crediting guard: `sparkmeter/env.php` (`$LEGACY_FILE_WATCHER_CREDIT_ENABLED`). |
| **Benin (BN)** | **smsbn.1pwrafrica.com** | [SMSComms-BN](https://github.com/onepowerLS/SMSComms-BN) **or** `smsbn/` tree inside some SMSComms checkouts | `receive.php` mirrors to `https://cc.1pwrafrica.com/api/bn/sms/incoming`. |

**Auto-deploy:** Repos may include `deploy.php` webhooks (GitHub → `git pull` → `copy`). In practice, releases are often applied **manually** in cPanel. Do not assume a push to `main` updates production without verification.

**Agents / automation cannot log into cPanel.** Operators upload via **File Manager** or **SFTP**, or run commands in **Terminal** if enabled on the host.

---

## Before you overwrite: archive

Never blindly replace live PHP. For each target directory:

1. In cPanel **File Manager**, open the docroot (e.g. `public_html` or the subdomain folder for `sms.1pwrafrica.com`).
2. Create a folder such as `archive/manual-YYYY-MM-DD/` (use today’s date).
3. **Copy** (not move) the files you are about to replace into that folder — at minimum `receive.php`, and for LS also `sparkmeter/env.php` and `sparkmeter/new_file_watcher.php` when those change.
4. Optionally **Compress** the archive folder to a `.zip` for download/off-site backup.
5. Upload the **new** files from git into the same paths as production.

If you use SSH:

```bash
cd /path/to/sms.1pwrafrica.com   # example; real path is on the host
mkdir -p archive/manual-$(date +%Y%m%d)
cp receive.php sparkmeter/env.php sparkmeter/new_file_watcher.php archive/manual-$(date +%Y%m%d)/ 2>/dev/null || true
# then upload or rsync new files from your machine
```

---

## Lesotho (two docroots)

1. **SMS app (Medic → JSON):** files from repo **root** — especially `receive.php` (mirror to CC).
2. **SparkMeter helper site:** files from **`sparkmeter/`** — especially `env.php`, `new_file_watcher.php` (legacy payment crediting guard), plus whatever your cron invokes.

`deploy.php` in the repo lists which files the **webhook** copies to which paths; use it as a **checklist** for manual uploads even when not using the webhook.

---

## Benin

Deploy from the **Benin** repo (or `smsbn/` tree) to **smsbn.1pwrafrica.com** only. Do not copy Lesotho `receive.php` onto Benin or vice versa (mirror URLs differ).

---

## After deploy

- Smoke-test: send a test payment SMS path or confirm `LOGIN.TXT` / CC logs show `mirror_to_1pdb` success.
- If Customer Care ingest is healthy, keep **`$LEGACY_FILE_WATCHER_CREDIT_ENABLED = false`** on LS `sparkmeter/env.php` to avoid double SparkMeter credits (see `CONTEXT.md`).
