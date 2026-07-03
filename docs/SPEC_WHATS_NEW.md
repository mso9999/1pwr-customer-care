# What's New login primer — system spec

## Purpose

Keep staff informed of feature changes without forcing them to read a changelog.
When an employee logs in and there are **feature updates shipped since their
last visit**, CC shows a dismissable, multipage "What's new" primer. If there is
nothing new since their last visit, no popup appears. Historical primers are
archived and reviewable alongside the Help / Tutorial guide.

## Behavior

- **Trigger:** on app load (inside the authenticated `Layout`), the
  `WhatsNewGate` component compares the folio against the employee's
  `whats_new_seen_at` timestamp (returned by `GET /api/auth/me`). Entries with
  a `date` strictly newer than `seen_at` are shown.
- **No updates → no popup.** If there are zero unseen entries, the gate renders
  nothing.
- **First-time user** (`seen_at` is null): the gate silently initializes
  `seen_at = now` and shows **no** popup. The primer is for *updates since last
  login*; new joiners learn features from the Help guide / Tutorial, not from a
  dump of the entire historical folio.
- **Multipage:** an entry may contain multiple `pages` (slides). All unseen
  entries' pages are flattened into one slide deck with Back / Next, progress
  dots, and a "Got it" button on the last slide. `Esc` and arrow keys also work.
- **Dismissable:** closing the modal (X, "Got it", or `Esc`) calls
  `POST /api/auth/whats-new/seen`, which stamps `seen_at = now`. The popup will
  not reappear until a newer folio entry ships.
- **Employee-only.** Customers do not see the primer.
- **Cross-device:** `seen_at` is stored server-side (`cc_employee_whats_new`
  table in `cc_auth.db`), so acknowledgment persists across browsers/devices.

## Data model

- Frontend folio: `acdb-api/frontend/src/whatsnew/folio.ts` — an append-only,
  typed array `WHATS_NEW_FOLIO` (newest first). Each entry:
  `{ id, date (ISO 8601 UTC), title, blurb, pages: [{ heading, body }] }`.
  `body` is plain text: blank lines separate paragraphs; lines beginning with
  `- ` render as bullets.
- Backend table `cc_employee_whats_new(employee_id PK, seen_at, updated_at)`
  in `cc_auth.db` (`db_auth.py`: `get_whats_new_seen`, `mark_whats_new_seen`).
- Endpoints:
  - `GET /api/auth/me` → includes `whats_new_seen_at` (ISO string or null) for
    employees.
  - `POST /api/auth/whats-new/seen` → stamps `seen_at = now` (employee-only).

## Archive

The full folio is rendered as a Help section — **Help → "What's New"**
(`helpSections.tsx`, id `whats-new`). It lists every entry historically with
date, blurb, and full page content, so any past primer can be revisited at any
time. This is the companion to the Guide / Tutorial.

## Commit-time process (MANDATORY)

> This is the rule that makes the system actually work. It is also encoded in
> `.cursorrules`.

When committing a change that constitutes a **novel feature** or a
**reconfigured feature** (something a user would notice and benefit from being
told about), the author MUST, in the same PR/commit:

1. Append a new entry to `WHATS_NEW_FOLIO` in
   `acdb-api/frontend/src/whatsnew/folio.ts` (newest first).
2. Set `id` to a stable, unique slug (never reuse); set `date` to the ship/deploy
   date (UTC ISO 8601); write a user-facing `title`, a one-line `blurb`, and one
   or more concise `pages` describing what changed and where to find it.
3. Keep it user-facing and scannable — not an engineering changelog. Pure
   refactors, dependency bumps, and internal fixes that don't change user-facing
   behavior do **not** require an entry.

Because the popup is gated on `date > seen_at`, simply adding the entry is
enough — every employee with an earlier `seen_at` will see the primer on their
next login automatically. No separate "trigger" flag exists or is needed.

### What counts as "novel or reconfigured"?

- A new module / page / workflow (e.g. LPG Tracking).
- A meaningful change to an existing flow, role/permission model, or data shown
  to users (e.g. HR becoming the department source; IT → onm_team mapping).
- A new self-service capability or a fix that changes an outcome users were
  hitting (e.g. deferred Benin credits now auto-delivering).

### What does NOT require an entry

- Internal refactors, type fixes, test additions, lint cleanup.
- Dependency/version bumps with no UX change.
- Bug fixes invisible to users.
- Pure data backfills / migrations.

When in doubt, add a short entry — a quiet, one-slide primer is cheap; a user
missing a workflow change is not.
