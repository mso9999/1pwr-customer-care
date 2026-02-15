# 1PWR Customer Care - Session Log

> AI session handoffs for continuity across conversations.
> Read the last 2-3 entries at the start of each new session.

---

## Session 2026-02-15 202602151430 (Initial Setup & Financial Analytics)

### What Was Done

1. **Repository consolidation**: Moved CC system code from Email Overlord repo to dedicated `1pwr-customer-care` repo on GitHub (`mso9999/1pwr-customer-care`).

2. **Auto-deploy CI/CD**: Set up GitHub Actions workflow (`.github/workflows/deploy.yml`) with two-job architecture:
   - `deploy-frontend`: GitHub-hosted runner builds Vite frontend, rsyncs to Linux EC2 where Caddy serves static files
   - `deploy-backend`: Self-hosted Windows runner robocopy's Python backend to `C:\acdb-customer-api\`, restarts service
   - Windows runner configured as `LocalSystem` for service management permissions

3. **Financial Analytics page** (`acdb-api/frontend/src/pages/FinancialPage.tsx`):
   - Figure 1: Quarterly ARPU trend (composed bar+line chart)
   - Figure 2: Monthly ARPU trend (bars colored by quarter)
   - Figure 3: Quarterly revenue by site (stacked bars)
   - Figure 4: ARPU by site for latest quarter (bar chart + table)
   - Figure 5: Full revenue breakdown table (per-site, per-quarter)
   - PDF export for individual figures and full report

4. **ARPU calculation fixes** (3 iterations):
   - v1: Connection/termination date matching from `tblcustomer` → produced 0 active customers (broken)
   - v2: Distinct transacting accounts per period → fluctuated wildly (not representative of customer base)
   - v3 (current): **Cumulative distinct accounts** that have ever transacted up through the period → monotonically increasing, matches real customer base growth

5. **Documentation**: Comprehensive README.md, cross-linking docs between CC and uGridPlan repos.

6. **Protocol setup**: Created `.cursorrules`, `CONTEXT.md`, and `SESSION_LOG.md` for AI session continuity.

### Key Decisions
- **Two-job deploy**: Frontend on GitHub-hosted runner (Linux), backend on self-hosted Windows runner. More controllable than single-runner approach.
- **Caddy serves frontend**: Static files from Linux EC2, API proxied to Windows EC2. FastAPI does NOT serve the SPA.
- **ARPU denominator**: Cumulative distinct accounts (not per-period transacting, not connection-date based). User confirmed this matches their expectation of monotonically-increasing customer counts.
- **No staging for CC**: Single `main` branch deploys directly to production. Unlike uGridPlan which has `dev`/`main` split.

### What Next Session Should Know
- The ARPU cumulative counting approach was arrived at after two incorrect iterations. The key insight: "customers" means the total customer base (everyone who has ever purchased), not just those who transacted in a given month.
- The Windows EC2 self-hosted runner has various PowerShell quirks (robocopy exit codes, pip stderr, schtasks permissions). All documented in the deploy workflow.
- The `Email Overlord/scripts/acdb-customer-api/` folder is a legacy copy. The canonical source is now this repo.
- Caddy config is on the Linux EC2 at `/etc/caddy/Caddyfile`. If new API routes are added, they may need a new `handle` block.

### Protocol Feedback
- First session with this protocol in place for the CC repo.
- CONTEXT.md and README.md should provide sufficient orientation for future sessions.
- The `docs/whatsapp-customer-care.md` file is very comprehensive for the WhatsApp bridge subsystem.
