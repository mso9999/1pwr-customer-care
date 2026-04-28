# Gensite credential encryption — setup and rotation

Vendor backend credentials (Victron VRM password, Solarman appSecret, Sinosoar
portal password, SMA Sunny Portal password) are stored in 1PDB's
`site_credentials` table as **Fernet ciphertext** (bytea columns
`secret_ciphertext`, `api_key_ciphertext`). The decryption key lives only in
the CC host environment — never in the database, never in git.

## First-time setup

1. Generate a fresh Fernet key on a workstation you trust:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Add it to `/opt/1pdb/.env` on the CC host:
   ```
   CC_CREDENTIAL_ENCRYPTION_KEY=<key-from-step-1>
   ```
3. Restart both backends so the new env var is loaded:
   ```bash
   sudo systemctl restart 1pdb-api 1pdb-api-bn
   ```
4. Confirm via the API:
   ```bash
   curl -s https://cc.1pwrafrica.com/api/gensite/vendors \
        -H "Authorization: Bearer $TOKEN" | jq .crypto_configured
   # should print: true
   ```

The commission wizard refuses to submit until `crypto_configured` is `true`.

## Rotation

Rotating the master key involves re-encrypting every row in
`site_credentials` under the new key. Do it in a maintenance window:

1. Decide on the new key. Keep the old one for the duration of the rotation.
2. Back up `site_credentials` (the logical Postgres backup timer already
   runs, but snapshot manually just before rotation):
   ```bash
   ssh ubuntu@<cc-host> "sudo -u postgres pg_dump -t site_credentials onepower_cc" \
       > site_credentials_before_rotation.sql
   ```
3. Run a one-off re-encryption script that:
   - reads each row with the **old** key (`Fernet(old_key).decrypt(...)`)
   - writes it back with the **new** key (`Fernet(new_key).encrypt(...)`)
   - commits in a single transaction.
4. Swap `CC_CREDENTIAL_ENCRYPTION_KEY` in `/opt/1pdb/.env` to the new value.
5. Restart `1pdb-api` + `1pdb-api-bn`.
6. Hit `POST /api/gensite/sites/{code}/credentials/{vendor}/{backend}/verify`
   against one credential per vendor — if all return `ok: true`, rotation
   is complete. Delete the old key from any place it was held.

The `/gensite/{code}` dashboard exposes a **Test connection** button per
credential that calls this verify endpoint; a quick post-rotation sanity
check.

## If the key is lost

Ciphertext cannot be decrypted without the key — the stored secrets become
unusable. Recovery:

1. Every credential row has a non-null `username` (plaintext, display-only).
2. Ops re-enters the passwords by commissioning-in-place (use the
   commission wizard against the already-commissioned site; `ON CONFLICT`
   on `(site_code, vendor, backend)` upserts the credential).
3. Alternatively hit `POST /api/gensite/sites/{code}/credentials/{vendor}/{backend}/rotate`
   with the fresh credentials body.

Key loss is recoverable but labour-intensive, so treat
`CC_CREDENTIAL_ENCRYPTION_KEY` as a first-class secret. It belongs in:

- `/opt/1pdb/.env` on the CC host (required)
- the team's shared password manager under the same project as the other
  CC operational secrets (`KOIOS_WRITE_API_KEY`, `TC_AUTH_TOKEN`, etc.)

It does **not** belong in GitHub secrets, in git, or in any Dropbox-synced
location.

## Related

- `acdb-api/gensite/crypto.py` — the Fernet helper itself
- `docs/credentials-and-secrets.md` — global map of where CC secrets live
- `docs/ops/gensite-commissioning.md` — operator flow
