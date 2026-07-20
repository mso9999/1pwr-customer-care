/**
 * What's New folio — the canonical, append-only log of feature updates shown
 * to staff in the "What's new" login primer.
 *
 * PROCESS (see docs/SPEC_WHATS_NEW.md and .cursorrules):
 *   Whenever a commit ships a *novel* or *reconfigured* feature, append a new
 *   entry to WHATS_NEW_FOLIO (newest first). The login primer auto-shows
 *   entries newer than the user's last-seen timestamp; closing the primer
 *   marks them seen. Historical entries remain viewable in Help → "What's new".
 *
 * Entry shape:
 *   - id        : stable slug (used as React key; never reuse)
 *   - date      : ISO 8601 date the feature shipped (UTC). Drives "since last
 *                 login" gating, so set it to the deploy/ship date.
 *   - title     : short headline
 *   - blurb     : one-line summary (used in the archive list)
 *   - pages     : one or more primer slides. Keep each slide scannable; body
 *                 is plain text — blank lines separate paragraphs, lines that
 *                 start with "- " render as bullets.
 *
 * Keep entries concise and user-facing (what changed + where to find it), not
 * an engineering changelog.
 */

export interface WhatsNewPage {
  heading: string;
  body: string;
}

export interface WhatsNewEntry {
  id: string;
  date: string; // ISO 8601 (UTC) ship date
  title: string;
  blurb: string;
  pages: WhatsNewPage[];
}

export const WHATS_NEW_FOLIO: WhatsNewEntry[] = [
  {
    id: 'investor-analytics',
    date: '2026-07-16',
    title: 'Investor Analytics — portfolio-grade KPIs now available',
    blurb: 'A new page brings investor-grade metrics, multi-country KPIs, SCADA availability, and Excel export to CC.',
    pages: [
      {
        heading: 'Investor-grade analytics, all in one place',
        body:
          'A new Investor Analytics page is now available under Operations in the sidebar.\n\n' +
          '- **Asset Register**: every operational site with PV/battery capacity, connection counts, customer mix (HH/SME/C&I), tariff in USD/kWh, and SCADA availability\n' +
          '- **KPI Time Series**: quarterly or monthly charts for connections, revenue (USD), energy (kWh), and ARPU — plus OPEX, EBITDA, and CAPEX columns\n' +
          '- **Customers & Transactions**: paginated site-level drill-downs with customer type badges and USD conversion\n' +
          '- **Excel Export**: one-click XLSX workbook for investor reporting\n' +
          '- **Data sources**: SparkMeter (Koios/ThunderCloud), Odoo invoiced revenue, SCADA (SMA/Victron), and CAPEX from the financial model\n' +
          '- All revenue is FX-converted to USD using historical rate lookups\n' +
          '- A lightweight summary is also exposed via the mobile BFF for Odyssey integration',
      },
    ],
  },
  {
    id: 'whats-new-primer',
    date: '2026-07-02',
    title: 'Introducing the "What\'s new" primer',
    blurb: 'Feature updates now surface automatically at login — and are archived in Help.',
    pages: [
      {
        heading: 'Stay up to date, automatically',
        body:
          'CC now shows a short "What\'s new" popup at login whenever there are feature updates since your last visit.\n\n' +
          '- Multipage walkthroughs of what changed and where to find it\n' +
          '- Close it anytime (X, "Got it", or Esc) — it won\'t show again until the next update\n' +
          '- If nothing changed since your last login, you see nothing\n' +
          '- Every past update is archived under Help → "What\'s New" so you can revisit it anytime',
      },
    ],
  },
  {
    id: 'hr-canonical-department',
    date: '2026-06-29',
    title: 'HR is now the source of truth for your department',
    blurb: 'Department affiliation now comes from the HR portal — and is shown in CC.',
    pages: [
      {
        heading: 'HR is now the source of truth for your department',
        body:
          'Employee department associations are now read from the HR portal (hr.1pwrafrica.com) instead of the PR system.\n\n' +
          'You can now see your HR department directly in CC — it appears under your role badge in the bottom-left of the sidebar.\n\n' +
          '- Your role (e.g. onm_team, engineering) is still auto-mapped from your department\n' +
          '- If it shows "No HR department", ask an HR admin to set your primary department on your HR profile\n' +
          '- Role changes take effect on your next login',
      },
    ],
  },
  {
    id: 'it-onm-team-access',
    date: '2026-06-29',
    title: 'IS&T department now gets O&M-level access',
    blurb: 'Staff in an IS&T (Information Systems and Technology) department are auto-assigned the onm_team role.',
    pages: [
      {
        heading: 'IS&T department → onm_team access',
        body:
          'IS&T staff now receive the same access as O&M (the onm_team role) automatically.\n\n' +
          'If your HR department is IT, IT Team, IS&T, Information Systems and Technology, Information Technology, TI, or Technologies de l\'information, you\'ll get onm_team access on your next login.\n\n' +
          'Provisioning (1Meter gateways) still requires the Engineering department — a separate role.',
      },
    ],
  },
  {
    id: 'lpg-tracking-module',
    date: '2026-06-26',
    title: 'LPG generator-fuel tracking is live',
    blurb: 'Track LPG stock, generator runs, runway, and costs per site.',
    pages: [
      {
        heading: 'LPG generator-fuel tracking',
        body:
          'A new Operations module — LPG Tracking — lets you log generator-fuel inventory and consumption per site.\n\n' +
          '- Record LPG deliveries (cylinders + price) to enroll a site\n' +
          '- Start and stop generator runs to log consumption\n' +
          '- See days of LPG left at current burn rate, with low-runway alerts\n' +
          '- Set a per-site low-runway warning threshold',
      },
      {
        heading: 'Where to find it',
        body:
          'Open LPG Tracking from the Operations section of the sidebar.\n\n' +
          'The overview lists every tracked site with runway and cost data. Use the site picker to open any site (even one not yet tracking) and record its first delivery.\n\n' +
          'Capturing deliveries and runs requires the onm_team or superadmin role. Other roles can view in read-only mode.',
      },
    ],
  },
  {
    id: 'bn-sm-credit-resilience',
    date: '2026-06-26',
    title: 'More reliable Benin refund/credit delivery to SparkMeter',
    blurb: 'Deferred SparkMeter credits now auto-retry, with auto-commissioning.',
    pages: [
      {
        heading: 'Reliable Benin credit delivery',
        body:
          'Refunds and credits processed in CC that can\'t reach SparkMeter immediately are no longer lost — they queue and retry automatically.\n\n' +
          '- A twice-hourly job drains the retry queue\n' +
          '- Live Benin accounts that weren\'t yet marked commissioned are auto-commissioned from their consumption history\n' +
          '- This fixes refunds that previously showed "Échec du crédit SM (deferred)"',
      },
    ],
  },
];

/** Entries newer than the given ISO timestamp (newest first). */
export function entriesNewerThan(seenAtIso: string | null | undefined): WhatsNewEntry[] {
  if (!seenAtIso) return [];
  const seen = new Date(seenAtIso).getTime();
  if (Number.isNaN(seen)) return [];
  return WHATS_NEW_FOLIO.filter((e) => new Date(e.date).getTime() > seen);
}
