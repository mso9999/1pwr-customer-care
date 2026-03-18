# OpenClaw Incident Note (2026-03-18)

## Summary

- `ai.1pwrafrica.com` became unreachable after DNS drift and local resolver/hosts
  staleness.
- WhatsApp classification degraded because the OpenClaw `customer-care` session
  became poisoned and returned non-JSON refusal text (`**NO.**`, `**STOP.**`).

## Root Cause

1. **Session poisoning**: a long-lived shared OpenClaw session (`customer-care`)
   accumulated hostile context, causing repeated non-JSON responses and bridge
   parser failures.
2. **Name resolution drift**: `ai.1pwrafrica.com` had moved to the current host,
   but stale local resolution still routed some clients to the old IP.

## Recovery Performed

- Verified OpenClaw gateway health on CC Linux host (`127.0.0.1:18789`).
- Backed up the poisoned session and bridge script before changes.
- Deployed bridge hotfix to use rotating session IDs:
  - `customer-care-YYYYMMDD`
- Restarted `whatsapp-cc` in PM2 and verified healthy startup/auth.
- Corrected DNS target for `ai.1pwrafrica.com` to current host.
- Cleared local stale host mapping (remove old IP from `/etc/hosts`).

## Prevention

- Keep session rotation enabled for WhatsApp classification.
- Avoid long-lived global AI sessions for production workflows.
- During DNS cutovers, verify:
  - authoritative answer (`dig +trace`)
  - local resolver answer (`dscacheutil -q host`)
  - browser/system hosts overrides (`/etc/hosts`)
