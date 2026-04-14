# Local secrets (not committed)

This directory is gitignored. **Do not commit private keys.**

## CC EC2 SSH key (`EOver.pem`)

The team key for the Customer Care Linux host is often named **`EOver.pem`**. It is **not** in the repository.

**Canonical location (primary dev Mac, Dropbox):**  
`/Users/mattmso/Dropbox/AI Projects/PEMs/EOver.pem`

1. Copy from there (or another secure copy) into this folder when working in a cloud workspace:

   ```bash
   cp "/Users/mattmso/Dropbox/AI Projects/PEMs/EOver.pem" .secrets/EOver.pem
   chmod 600 .secrets/EOver.pem
   ```

2. Resolve the current host from AWS inventory or your `EC2_LINUX_HOST` secret (do not rely on stale IPs in old docs).

3. SSH example:

   ```bash
   ssh -i .secrets/EOver.pem ubuntu@<current-cc-linux-host>
   ```

## Email Overlord / other “AI Projects” paths

Those folders (e.g. Dropbox `AI Projects`, legacy Email Overlord) exist on **developer machines**, not in this cloud workspace. If you need a file from there, copy it into `.secrets/` or the repo path your tooling can read.
