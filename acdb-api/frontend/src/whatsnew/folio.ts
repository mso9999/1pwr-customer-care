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
    id: 'sparkmeter-credit-and-country-tariff',
    date: '2026-09-28T23:00:00Z',
    title: 'SparkMeter credit and country tariff',
    blurb: 'Choose whether a 1Meter account also credits its SparkMeter, and edit that country’s tariff next to its fees.',
    pages: [
      {
        heading: 'Billing Priority',
        body:
          'Look up an account, then set SparkMeter credit.\n\n' +
          '- Automatic keeps ThunderCloud on at MAK and LAB. Other 1Meter accounts stay on the CC ledger.\n' +
          '- Always credit forces the SparkMeter send. 1Meter ledger only withholds it while that account bills on a 1Meter.\n' +
          '- The change is recorded in the mutation log.',
      },
      {
        heading: 'Tariffs and fee payments',
        body:
          'Tariffs shows this country’s tariff on the same card as the connection and readyboard fees. Pick the country in the header first. All countries is not an editable rate.\n\n' +
          'A payment that matches the remaining connection balance, the remaining readyboard balance, or both, settles that debt in full. Any other electricity payment still puts at most half toward fee debt.',
      },
    ],
  },
  {
    id: 'site-electricity-billing-hold',
    date: '2026-09-28T22:30:00Z',
    title: 'Free supply until inspection',
    blurb: 'Hold a site so installed meters stay powered without selling electricity, and bill one meter when you need to test.',
    pages: [
      {
        heading: 'Billing Priority',
        body:
          'Hold a site before the regulator allows electricity sales. Meters keep reporting and the relay stays closed. That use is free. Connection and readyboard fees still collect.\n\n' +
          '- Type HOLD and the site code. Clear it later with BILL and the site code.\n' +
          '- Saved electricity payments become units at the tariff from the day they were paid.\n' +
          '- Set one meter to Bill to test real billing while the rest of the site stays free. Hours before that switch stay free.',
      },
    ],
  },
  {
    id: 'account-setup-reset',
    date: '2026-09-28T22:00:00Z',
    title: 'Reset setup data on an issued account',
    blurb: 'O&M can wipe test payments and readings on a customer still being installed, keep the account number, and undo it from Mutations.',
    pages: [
      {
        heading: 'Customer page',
        body:
          'Reset setup data keeps the person, the account number, and the meter. It deletes test payments and readings, then sets connection and readyboard debt to the fees saved for that country.\n\n' +
          '- Type RESET and the account number. Give a reason.\n' +
          '- Only while the account is still being installed.\n' +
          '- Undo it from Mutations. Decommission is a different action and does not do this.',
      },
    ],
  },
  {
    id: 'energy-debt-cutoff-lag',
    date: '2026-09-28T21:45:00Z',
    title: 'Energy used before cutoff is customer debt',
    blurb: 'kWh drawn after credit hits zero, before the relay opens, shows as energy debt. The next MoMo electricity payment pays it down.',
    pages: [
      {
        heading: 'Customer Data',
        body:
          'When a meter keeps running after credit is gone, that kWh is energy debt on Customer Data.\n\n' +
          '- It is the negative balance, kept as debt instead of hidden at 0.0.\n' +
          '- A MoMo payment that buys electricity fills this hole before new credit shows.\n' +
          '- It is not charged a second time.',
      },
    ],
  },
  {
    id: 'relay-auto-cutoff-switch',
    date: '2026-09-28T21:30:00Z',
    title: 'Automatic power cutoff by country',
    blurb: 'O&M and finance can turn 1Meter zero-credit cutoff on or off from Billing Priority. The change is written to the mutation log.',
    pages: [
      {
        heading: 'Where to set it',
        body:
          'Open Billing Priority and pick Lesotho, Benin, or Zambia in the sidebar. All countries cannot save.\n\n' +
          '- Superadmin can save any country.\n' +
          '- O&M and finance can save only their own country.\n' +
          '- The mutation log records who changed it, the old value, the new value, and when.',
      },
      {
        heading: 'What customers see',
        body:
          'When cutoff is on, a 1Meter-primary account loses power at zero credit. A payment that restores credit turns power back on.\n\n' +
          '- Customers see one sentence on My Dashboard. They cannot change the switch.\n' +
          '- Gateways below firmware 1.1.74 are skipped.',
      },
    ],
  },
  {
    id: 'sms-inbox-bridge-ledger',
    date: '2026-09-28',
    title: 'SMS inbox, WhatsApp QR, and ledger corrections',
    blurb: 'Inbound texts have their own page, the WhatsApp QR is an admin page, and Customer Data no longer pretends a raw row is a payment.',
    pages: [
      {
        heading: 'SMS inbox',
        body:
          'Commerce → SMS Inbox shows texts for the country in the sidebar. All countries asks you to pick one.\n\n' +
          '- Failed rows go to SMS Formats if you edit formats, otherwise to Record Payment.\n' +
          '- Replay stays on SMS Formats. It does not credit from the inbox.',
      },
      {
        heading: 'Payments and the WhatsApp phone',
        body:
          '- Tariffs can set an installation threshold. Amounts at or above it are connection fees unless that account is exempt.\n' +
          '- Customer Data → Ledger correction is for superadmin and O&M only. It does not change the balance.\n' +
          '- System → WhatsApp bridge shows the QR for this country. It does not use another country’s phone.',
      },
    ],
  },
  {
    id: 'ptb-on-install-and-assign',
    date: '2026-09-25',
    title: 'Pole boxes (PTBs) are confirmed, not assumed',
    blurb: 'Field install and Assign Meter ask before creating a PTB, and in-service 1Meters with no PTB get a warning.',
    pages: [
      {
        heading: 'You decide when the PTB is created',
        body:
          '- Field install: if the pole has no PTB, CC asks whether to create it now. Cancel records the binding only, for units not installed yet.\n' +
          '- Assign Meter: when an online 1Meter is bound to a customer, CC asks to record it in its PTB (created if needed). Cancel only for a bench test.\n' +
          '- Meters page and customer page: a 1Meter serving a customer with recent transactions or consumption but no PTB in uGridPlan shows a warning with a Create PTB button.',
      },
    ],
  },
  {
    id: 'sites-only-from-pr',
    date: '2026-09-25',
    title: 'New sites come only from PR',
    blurb: 'Site codes are created in PR and reach Customer Care automatically; the Site Registry now only activates and retires sites.',
    pages: [
      {
        heading: 'One master site list',
        body:
          'Create new sites in PR (Admin → Reference Data → Sites), opened from Nexus. They reach Customer Care for their country automatically, and a nightly refresh catches anything missed.\n\n' +
          '- New sites arrive inactive. Engineering/IS&T activates them in Site Registry at commissioning.\n' +
          '- Customer Care can no longer create site codes itself.\n' +
          '- uGridPlan maps each site by its code, so there is no manual mapping step.',
      },
    ],
  },
  {
    id: '1meter-rollout-install-assign',
    date: '2026-09-25',
    title: '1Meter rollout: record every pole and assign every meter',
    blurb: 'The walkthrough now covers pole installs and meter assignment after commissioning, the Meters page flags anything missed, and the 1Meter screens are in French.',
    pages: [
      {
        heading: 'Two new walkthrough steps',
        body:
          'Provisioning → Operator walkthrough now ends with:\n' +
          '- Step 12: record every installed gateway on its pole (Field install).\n' +
          '- Step 13: assign every reporting meter to its customer (Assign Meter).\n\n' +
          'Each step lists the gateways or meters still outstanding for the selected site.',
      },
      {
        heading: 'Warnings on the Meters page',
        body:
          'A 1Meter only appears on the Meters page and map once it is assigned to a customer. An amber banner now lists, per site, gateways online without a pole record, meters reporting but not assigned, and sites with equipment in the field before the walkthrough is complete. Each line links to the screen that fixes it.',
      },
      {
        heading: 'En français',
        body:
          'Le parcours opérateur, Installation terrain, les conseils d’Attribuer un compteur et l’aide Provisionnement sont maintenant en français. Field install lists only your country’s sites instead of defaulting to MAK.',
      },
    ],
  },
  {
    id: 'nexus-skips-login-chooser',
    date: '2026-09-28',
    title: 'Nexus opens Customer Care as staff',
    blurb: 'Launching Customer Care from Nexus signs you in as an employee and skips the customer / employee / committee chooser.',
    pages: [
      {
        heading: 'From Nexus you are already staff',
        body:
          'Open Customer Care from Nexus and you land in the tool as an employee. The customer, employee, and committee sign-in page is for opening this site directly.\n\n' +
          'Sign out if you want that page. It is also at the Customer Care address when you have not come from Nexus.',
      },
    ],
  },
  {
    id: 'fleet-map-install-offline-firmware',
    date: '2026-09-22',
    title: 'Meter map shows install date, offline since, and firmware',
    blurb: 'Click a meter on the map for install date, offline since, and firmware. Recolor the map by firmware, by install date, or with a status fill and an install-date border.',
    pages: [
      {
        heading: 'More on each meter dot',
        body:
          'Open Meters and switch to the map. Click a dot.\n\n' +
          '- Installed: the connection date on the meter\n' +
          '- Offline since: the last report, or “never reported” if it has not checked in\n' +
          '- FW: the firmware the meter last reported, or the version stored at provisioning\n\n' +
          'The map still opens green for online and red for installed-but-offline. The legend switches the coloring:\n\n' +
          '- Firmware: one color per version\n' +
          '- Installed: heat map from oldest (blue) to newest (red)\n' +
          '- Hybrid: green or red fill for online or offline, and the border is the install-date heat',
      },
    ],
  },
  {
    id: 'analytics-kwh-by-category',
    date: '2026-09-21',
    title: 'Average kWh per month by customer category',
    blurb: 'Analytics now opens with popular comparisons, including average monthly kWh by customer category or for all customers together.',
    pages: [
      {
        heading: 'Popular analyses',
        body:
          'Open Analytics. Two shortcuts sit at the top and use the last six complete months, plus the country, sites, and customer types in the filters.\n\n' +
          '- Average kWh per month by customer category\n' +
          '- Average kWh per month, all customers\n\n' +
          'HH1, HH2, and HH3 are grouped as HH. Switch Split between Aggregated and By customer category, and Average over between customers with use and all connected customers, then run again.\n\n' +
          'Other comparisons still use the metric picker below. The date range now applies to every view.',
      },
    ],
  },
  {
    id: 'site-registry-pr-refresh',
    date: '2026-09-21',
    title: 'Site Registry follows the PR / Nexus master list',
    blurb: 'Sites created in PR or uGridPLAN now update Customer Care, including the uGridPlan connection picker. Refresh from PR / Nexus if a site is missing.',
    pages: [
      {
        heading: 'One master site list',
        body:
          'Open Admin → Site Registry. New 3-letter sites from PR (or from uGridPLAN via PR) appear here automatically, staged inactive until you activate them at commissioning.\n\n' +
          '- The uGridPlan connection picker uses the same list, so SIN / SAM / GBO no longer need a manual Discover\n' +
          '- If a site is missing, tap Refresh from PR / Nexus on this lane\n' +
          '- Still create sites in PR first — the local Create button is emergency-only',
      },
    ],
  },
  {
    id: 'fleet-live-multi-meter',
    date: '2026-09-14',
    title: 'Fleet live lists every meter on a gateway',
    blurb: 'A 1Meter gateway can carry several meters on one RS-485 string. Fleet live now shows each serial and its last sample — not just one.',
    pages: [
      {
        heading: 'Several meters, one gateway',
        body:
          'Open Provisioning → Fleet live. Each gateway row now lists every meter that is reporting through it, with that meter’s last sample and power.\n\n' +
          '- No customer account or load is required — newly wired meters appear as soon as the gateway discovers them\n' +
          '- The summary counts gateways and meters separately\n' +
          '- If a serial is still missing, the meter has not been acquired yet (check RS-485 A/B/GND, then long-press PRG to re-scan)',
      },
    ],
  },
  {
    id: 'commission-ugp-link-required',
    date: '2026-09-09',
    title: 'Commissioning: link the uGridPLAN connection before generating the contract',
    blurb: 'Generate Contract & SMS now explains why it was blocked — you must link the customer\'s pole/PTB on the Details step.',
    pages: [
      {
        heading: 'Why Generate was greyed out',
        body:
          'The last step used to disable Generate Contract & SMS with no on-screen reason if the uGridPLAN connection (pole/PTB) was not linked. That looked like a broken button.\n\n' +
          '- On Details, tap "Link uGridPlan Connection" and pick the customer\'s pole/PTB — it is now marked required\n' +
          '- Next will not advance until that link is set\n' +
          '- If you reach Review without it, Generate sends you back to Details with the explanation\n' +
          '- An existing accounts.survey_id binding is filled in automatically',
      },
    ],
  },
  {
    id: 'unmetered-service-billing',
    date: '2026-08-28',
    title: 'Unmetered service: track connected customers awaiting a meter',
    blurb: 'Customers connected without a meter can now be enrolled in unmetered service — a flat monthly service fee (50 LSL default) is recorded against the account and paid down automatically from their top-ups.',
    pages: [
      {
        heading: 'A formal record for connected-but-unmetered customers',
        body:
          'Some customers are connected to the grid before a meter is installed. They now get a formal status instead of an informal arrangement:\n\n' +
          '- Enroll from the new Unmetered Service page (Commerce menu) with the account number\n' +
          '- The monthly service fee accrues automatically on the 1st of each month\n' +
          '- When the customer tops up, part of the payment pays down their service-fee balance first\n' +
          '- The customer page shows an amber "Unmetered service" strip with what they owe\n\n' +
          'The fee amount is configurable per country under Tariff Management → Country fees.',
      },
      {
        heading: 'Automatic exit when the meter arrives',
        body:
          'No manual cleanup needed: when a meter is assigned to the account (or the customer is commissioned), the enrollment ends automatically and monthly fees stop accruing.\n\n' +
          '- The enrollment record and full payment ledger stay available under the Ended filter\n' +
          '- Opening arrears can be recorded at enrollment for customers who already owe for past months',
      },
    ],
  },
  {
    id: 'fleet-map-linked-offline-meters',
    date: '2026-08-24',
    title: 'Fleet map now flags every linked 1Meter — even before it first reports',
    blurb: 'Meters assigned to a gateway via a pole/PTB now show as 1Meter-linked on the fleet map right away, instead of looking unlinked until the unit first reports.',
    pages: [
      {
        heading: 'Linked meters no longer hide on the map',
        body:
          'When you assign a meter to a pole/PTB, the fleet map now marks it as 1Meter-linked immediately — with the thicker border and "1Meter linked" label — even if the gateway has not reported yet.\n\n' +
          '- Previously these meters looked like ordinary unlinked dots until first telemetry\n' +
          '- The Thing name fills in automatically once the unit reports\n' +
          '- Use "Find meter / account / gateway" to jump straight to any of them',
      },
    ],
  },
  {
    id: 'registration-confirmation-sms',
    date: '2026-08-20',
    title: 'Customers now get an SMS the moment they are registered',
    blurb: 'Every new registration texts the customer their account number automatically — no more waiting for the weekly transcription cycle.',
    pages: [
      {
        heading: 'Instant registration confirmation',
        body:
          'When a customer is registered in CC (office or committee tablet), they now immediately receive an SMS with their new account number and the payment-reference instruction.\n\n' +
          '- Automatic whenever the customer record has a phone number\n' +
          '- Sesotho for Lesotho, French for Benin\n' +
          '- The success screen shows when the SMS was sent\n\n' +
          'This completes the MGD061 onboarding commitment: register → instant account number → instant SMS.',
      },
    ],
  },
  {
    id: 'committee-registrar-logins',
    date: '2026-08-17',
    title: 'Committee logins — register customers on tablets in the field',
    blurb: 'Village committee members can now sign in to CC with their own username and password and register customers directly — paper ledger becomes the backup.',
    pages: [
      {
        heading: 'Registration moves to the field',
        body:
          'Committee members no longer need a 1PWR employee account or the monthly staff PIN. Admins issue each committee registrar a username and password (Admin → Roles → Field Registrars), optionally bound to a single site.\n\n' +
          '- Committee login lives on the sign-in page under the new **Committee** tab\n' +
          '- Registrars land directly in the New Customer wizard and can only register customers — no other CC access\n' +
          '- Registrations are attributed to the registrar in the audit log\n' +
          '- Accounts can be deactivated instantly from the admin page\n\n' +
          'Paper ledgers remain the backup when there is no connectivity; transcribe them into CC the same week.',
      },
    ],
  },
  {
    id: 'registration-rooms-signature',
    date: '2026-08-17',
    title: 'Registration now matches the paper ledger — rooms + signature',
    blurb: 'The New Customer wizard captures everything the MGF018 paper ledger did: number of rooms and an optional draw-with-finger customer signature.',
    pages: [
      {
        heading: 'Paper-ledger parity at registration',
        body:
          'Registering a customer in CC now captures the two items that previously existed only on the MGF018 paper ledger:\n\n' +
          '- **Number of rooms** (optional) — a new field in the Service Details step\n' +
          '- **Customer signature** (optional) — a new Signature step where the customer draws with their finger on the phone, or you can upload a photo of the ledger signature\n\n' +
          'Both are stored on the customer record; the signature image appears on the customer detail page.',
      },
    ],
  },
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
