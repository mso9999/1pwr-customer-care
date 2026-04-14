# 1Meter firmware release and OTA helpers

Scripts restored from `scripts/archive/2026-03-worktree-cleanup/1meter/legacy_helpers/` for operational use.

**Documentation:** `docs/1meter-ota-runbook.md` (AWS identifiers, flow, constraints).

**Typical usage:** run on the **firmware build host** (`/opt/1meter-firmware`) or after copying this directory there. Publishing and `create_ota_update` require **AWS credentials** (IAM role / SSO); nothing in this folder stores secrets.
