import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

// ---------------------------------------------------------------------------
// Section data
// ---------------------------------------------------------------------------

interface Section {
  id: string;
  title: string;
  content: React.ReactNode;
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-gray-700 leading-relaxed mb-3">{children}</p>;
}
function Bold({ children }: { children: React.ReactNode }) {
  return <strong className="font-semibold text-gray-900">{children}</strong>;
}
function Code({ children }: { children: React.ReactNode }) {
  return <code className="px-1.5 py-0.5 bg-gray-100 rounded text-xs font-mono text-blue-700">{children}</code>;
}
function PageLink({ to, children }: { to: string; children: React.ReactNode }) {
  return <Link to={to} className="text-blue-600 hover:underline font-medium">{children}</Link>;
}
function Ol({ children }: { children: React.ReactNode }) {
  return <ol className="list-decimal list-inside text-sm text-gray-700 space-y-1.5 mb-3 ml-1">{children}</ol>;
}
function Ul({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc list-inside text-sm text-gray-700 space-y-1.5 mb-3 ml-1">{children}</ul>;
}
function SubHead({ children }: { children: React.ReactNode }) {
  return <h4 className="text-sm font-bold text-gray-800 mt-5 mb-2">{children}</h4>;
}
function Tip({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation(['help']);
  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800 mb-3">
      <span className="font-semibold">{t('help:tip')}</span> {children}
    </div>
  );
}
function Warning({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation(['help']);
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800 mb-3">
      <span className="font-semibold">{t('help:important')}</span> {children}
    </div>
  );
}

const SECTIONS: Section[] = [
  {
    id: 'overview',
    title: 'Overview',
    content: (
      <>
        <P>
          The <Bold>1PWR Customer Care (CC) Portal</Bold> is a web-based application for managing mini-grid customer operations.
          It replaces the former ACCDB-based database system. All operations are performed through a web browser — no
          RDP, desktop software, or file shares are required.
        </P>
        <P>Access the portal at <Bold>cc.1pwrafrica.com</Bold>. It works on desktops, tablets, and phones.</P>
        <SubHead>Feature Map</SubHead>
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-gray-100 text-left text-gray-600">
                <th className="px-3 py-2 font-semibold">Category</th>
                <th className="px-3 py-2 font-semibold">Feature</th>
                <th className="px-3 py-2 font-semibold">Page</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {[
                ['Customer Mgmt', 'Search & browse customers', '/customers'],
                ['Customer Mgmt', 'Register new customer', '/customers/new'],
                ['Customer Mgmt', 'Customer profile & detail', '/customers/:id'],
                ['Customer Mgmt', 'Customer data & transactions', '/customer-data'],
                ['Customer Mgmt', 'Commission customer', '/commission'],
                ['Metering', 'View & search meters', '/meters'],
                ['Metering', 'Assign meter to customer', '/assign-meter'],
                ['Metering', 'Check meter comparison', '/check-meters'],
                ['Payments', 'Record missed payment', '/record-payment'],
                ['Payments', 'Payment verification', '/payment-verification'],
                ['Financing', 'Product templates & agreements', '/financing'],
                ['Financing', 'Extend credit (from customer page)', '/customers/:id'],
                ['Reports', 'O&M quarterly report', '/om-report'],
                ['Reports', 'Financial analytics (ARPU)', '/financial'],
                ['Reports', 'Onboarding pipeline', '/pipeline'],
                ['Data', 'Accounts / Transactions / Tables', '/accounts'],
                ['Data', 'Export to CSV / XLSX', '/export'],
                ['Admin', 'Tariff management', '/tariffs'],
                ['Admin', 'Role management', '/admin/roles'],
                ['Admin', 'Audit trail', '/mutations'],
                ['Admin', 'UGridPlan sync', '/sync'],
              ].map(([cat, feat, page], i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-gray-500">{cat}</td>
                  <td className="px-3 py-1.5 font-medium text-gray-800">{feat}</td>
                  <td className="px-3 py-1.5"><Code>{page}</Code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    ),
  },
  {
    id: 'login',
    title: 'Login & Roles',
    content: (
      <>
        <SubHead>Employee Login</SubHead>
        <Ol>
          <li>Navigate to <Bold>cc.1pwrafrica.com</Bold>.</li>
          <li>Enter your <Bold>Employee ID</Bold> and <Bold>password</Bold>.</li>
          <li>Click <Bold>Sign In</Bold>. You will be redirected to the Dashboard.</li>
        </Ol>

        <SubHead>Customer Self-Service</SubHead>
        <P>Customers can register and log in with their customer ID. The customer view shows their personal dashboard with balance, consumption history, and profile information.</P>

        <SubHead>Roles</SubHead>
        <Ul>
          <li><Bold>superadmin</Bold> — Full access, including role management and system configuration.</li>
          <li><Bold>onm_team</Bold> — Operations & maintenance features, commissioning, meter management.</li>
          <li><Bold>finance_team</Bold> — Financial reporting, payment verification, financing management.</li>
          <li><Bold>generic</Bold> — Basic read access to customer data and reports.</li>
        </Ul>
      </>
    ),
  },
  {
    id: 'customers',
    title: 'Customer Management',
    content: (
      <>
        <SubHead>Search & Browse (<PageLink to="/customers">/customers</PageLink>)</SubHead>
        <P>The customer list supports text search across names, account numbers, and IDs. Click any customer row to open their detail page.</P>

        <SubHead>Register New Customer (<PageLink to="/customers/new">/customers/new</PageLink>)</SubHead>
        <Ol>
          <li>Click <Bold>+ New Customer</Bold> or navigate to <Code>/customers/new</Code>.</li>
          <li>Fill in: first name, last name, national ID, phone number, site/concession, customer type.</li>
          <li>Click <Bold>Save</Bold>. An account number is assigned automatically.</li>
        </Ol>

        <SubHead>Customer Detail (<Code>/customers/:id</Code>)</SubHead>
        <P>Shows all fields from the customer record with edit capability. Action buttons include:</P>
        <Ul>
          <li><Bold>Edit</Bold> — Inline field editing</li>
          <li><Bold>View Data</Bold> — Jump to customer data / transaction view</li>
          <li><Bold>Commission</Bold> — Start the commissioning wizard</li>
          <li><Bold>Extend Credit</Bold> — Open the financing wizard (for commissioned customers)</li>
          <li><Bold>Assign Meter</Bold> — Assign a meter to this customer</li>
          <li><Bold>Decommission</Bold> — Terminate service (preserves all history)</li>
        </Ul>

        <SubHead>Customer Data Lookup (<PageLink to="/customer-data">/customer-data</PageLink>)</SubHead>
        <P>Enter an account number (e.g., <Code>0045MAK</Code>) to see:</P>
        <Ul>
          <li><Bold>Balance</Bold> — Current kWh balance and currency equivalent</li>
          <li><Bold>Avg Consumption</Bold> — kWh per day</li>
          <li><Bold>Estimated Recharge Time</Bold> — Days until balance runs out at current rate</li>
          <li><Bold>Last Payment</Bold> — Most recent payment amount and date</li>
          <li><Bold>Active Financing</Bold> — Debt summary with progress bars (if applicable)</li>
          <li><Bold>Transaction History</Bold> — Sortable table with inline editing</li>
          <li><Bold>Consumption Charts</Bold> — 24h, 7-day, 30-day, and 12-month views</li>
        </Ul>
      </>
    ),
  },
  {
    id: 'commission',
    title: 'Commissioning',
    content: (
      <>
        <P>The <PageLink to="/commission">commission page</PageLink> provides a multi-step wizard to finalize a customer's service connection.</P>
        <Ol>
          <li><Bold>Look up</Bold> the customer by account number or customer ID.</li>
          <li><Bold>Verify/update</Bold> details: name, national ID, phone, GPS coordinates, customer type, service phase, ampacity.</li>
          <li><Bold>Capture signature</Bold> — the customer signs on the tablet/phone canvas.</li>
          <li><Bold>Generate contracts</Bold> — bilingual (English/Sesotho) PDFs are generated and stored.</li>
          <li><Bold>Send SMS</Bold> — the contract download link is sent to the customer automatically.</li>
        </Ol>

        <SubHead>Commissioning Steps</SubHead>
        <P>The system tracks seven steps per customer. These can be updated individually or in bulk:</P>
        <Ol>
          <li>Connection fee paid</li>
          <li>Readyboard fee paid</li>
          <li>Readyboard tested</li>
          <li>Readyboard installed</li>
          <li>Airdac connected</li>
          <li>Meter installed</li>
          <li>Customer commissioned</li>
        </Ol>
        <Tip>Use the <PageLink to="/pipeline">Onboarding Pipeline</PageLink> page to see how many customers are at each stage.</Tip>
      </>
    ),
  },
  {
    id: 'payments',
    title: 'Payments',
    content: (
      <>
        <SubHead>Record Missed Payment (<PageLink to="/record-payment">/record-payment</PageLink>)</SubHead>
        <P>When a payment is missed by the SMS gateway (e.g., gateway phone offline), record it manually:</P>
        <Ol>
          <li>Enter the <Bold>account number</Bold> (e.g., <Code>0045MAK</Code>).</li>
          <li>Enter the <Bold>amount</Bold> in Maloti.</li>
          <li>Optionally specify a meter ID and note.</li>
          <li>Click <Bold>Record Payment</Bold>.</li>
        </Ol>
        <P>The system converts the currency to kWh at the current tariff rate, credits the customer's balance, and credits SparkMeter.</P>
        <Warning>
          If the customer has active financing, the payment is automatically split between electricity and debt repayment. An indicator shows the split on the result screen.
        </Warning>

        <SubHead>Payment Verification (<PageLink to="/payment-verification">/payment-verification</PageLink>)</SubHead>
        <P>Connection fees and readyboard fees require finance team verification.</P>
        <Ol>
          <li>Open the Payment Verification page — defaults to <Bold>Pending</Bold> status.</li>
          <li>Filter by payment type or status as needed.</li>
          <li>Select payments using checkboxes (select all available).</li>
          <li>Optionally add a note.</li>
          <li>Click <Bold>Verify</Bold> or <Bold>Reject</Bold>.</li>
        </Ol>
        <P>Use the <Bold>Export XLSX</Bold> button to download the current view for the finance team's records.</P>
      </>
    ),
  },
  {
    id: 'financing',
    title: 'Customer Financing',
    content: (
      <>
        <P>The financing system (<PageLink to="/financing">/financing</PageLink>) allows extending credit to customers for assets like readyboards, refrigerators, or solar lanterns. The debt is tracked separately from electricity balance so prepaid meter relay cutoff continues to function normally.</P>

        <SubHead>Product Templates</SubHead>
        <P>Go to <Bold>Financing → Product Templates</Bold> tab to define reusable templates:</P>
        <Ul>
          <li><Bold>Name</Bold> — e.g., "Readyboard", "Refrigerator"</li>
          <li><Bold>Default Principal</Bold> — Standard financed amount</li>
          <li><Bold>Interest Rate</Bold> — e.g., 0.10 for 10%</li>
          <li><Bold>Setup Fee</Bold> — Administration fee</li>
          <li><Bold>Repayment Fraction</Bold> — Portion of each payment diverted to debt (e.g., 0.20 = 20%)</li>
          <li><Bold>Penalty Rate</Bold> — Applied to overdue balance</li>
          <li><Bold>Grace Days / Interval</Bold> — How long before penalty, how often it recurs</li>
        </Ul>

        <SubHead>Extending Credit to a Customer</SubHead>
        <P>From the customer detail page, click <Bold>Extend Credit</Bold>. The 4-step wizard:</P>
        <Ol>
          <li><Bold>Product</Bold> — Select a template (pre-fills terms) or choose custom.</li>
          <li><Bold>Terms</Bold> — Adjust principal, interest, fees, repayment fraction, penalty terms. The total owed is computed automatically.</li>
          <li><Bold>Signature</Bold> — Customer signs on the screen to acknowledge the terms.</li>
          <li><Bold>Review &amp; Confirm</Bold> — Summary of all terms, then click Create Agreement.</li>
        </Ol>
        <P>A signed bilingual PDF financing agreement is generated and attached to the customer's records.</P>

        <SubHead>Payment Splitting</SubHead>
        <Warning>
          Once a customer has an active financing agreement, <Bold>every payment</Bold> is automatically split:
        </Warning>
        <Ul>
          <li><Bold>Regular payments</Bold> — Split per the repayment fraction. E.g., M100 with 20% fraction → M20 to debt, M80 to electricity.</li>
          <li><Bold>Dedicated debt payments</Bold> — If the amount ends in digit <Bold>1</Bold> or <Bold>9</Bold> (e.g., M51, M101, M79), the <Bold>entire</Bold> amount goes to debt.</li>
          <li><Bold>Multiple agreements</Bold> — Payments apply to the oldest (FIFO) first.</li>
        </Ul>

        <SubHead>Agreements Table</SubHead>
        <P>The <Bold>Agreements</Bold> tab shows all agreements. Filter by status: Active, Paid Off, Defaulted, Cancelled. Click any row to see the full ledger of payments, penalties, and adjustments.</P>

        <SubHead>Automatic Penalties</SubHead>
        <P>The system runs a daily penalty check. If no payment has been received within the <Bold>grace days</Bold>, a penalty of <Bold>penalty rate × outstanding balance</Bold> is added. Penalties repeat at the configured interval.</P>
      </>
    ),
  },
  {
    id: 'meters',
    title: 'Meters',
    content: (
      <>
        <SubHead>Meter Registry (<PageLink to="/meters">/meters</PageLink>)</SubHead>
        <P>Browse and search all meters. Each record shows: meter ID, account number, community/site, status, and type.</P>

        <SubHead>Assign Meter (<PageLink to="/assign-meter">/assign-meter</PageLink>)</SubHead>
        <P>Assign a meter to a customer account or reassign between accounts. History of meter assignments is tracked.</P>

        <SubHead>Check Meter Comparison (<PageLink to="/check-meters">/check-meters</PageLink>)</SubHead>
        <P>Compares SparkMeter (SM) production readings against 1Meter (1M) check meter readings:</P>
        <Ul>
          <li>Hourly kWh time series with configurable time range (7 / 14 / 30 days).</li>
          <li>Per-meter deviation statistics: total %, mean %, standard deviation.</li>
          <li>Fleet-wide total deviation summary across all check meters.</li>
          <li>Meter health indicators: online (green), stale (yellow), offline (red).</li>
        </Ul>

        <SubHead>Meter Lifecycle</SubHead>
        <P>Meters follow a lifecycle: <Code>active</Code> → <Code>inactive</Code> → <Code>decommissioned</Code> → <Code>maintenance</Code>. All status changes are logged in the mutation audit trail.</P>
      </>
    ),
  },
  {
    id: 'reports',
    title: 'Reports & Analytics',
    content: (
      <>
        <SubHead>O&M Quarterly Report (<PageLink to="/om-report">/om-report</PageLink>)</SubHead>
        <P>Interactive charts matching the SMP O&M quarterly report format:</P>
        <Ul>
          <li>Customer statistics per site (total, active, new per quarter)</li>
          <li>Quarterly customer connection growth</li>
          <li>Consumption and revenue per site per quarter</li>
          <li>Generation vs consumption</li>
          <li>Average consumption per customer trends</li>
          <li>Consumption by customer tenure</li>
        </Ul>

        <SubHead>Financial Analytics (<PageLink to="/financial">/financial</PageLink>)</SubHead>
        <P>Revenue and ARPU analytics including monthly revenue by site, ARPU trends, payment type breakdown, and revenue growth comparisons.</P>

        <SubHead>Onboarding Pipeline (<PageLink to="/pipeline">/pipeline</PageLink>)</SubHead>
        <P>A funnel visualization showing customer progress through commissioning stages. Includes drop-off percentages, site filtering, summary cards (total registered, fully commissioned, conversion rate), and a detailed table.</P>
      </>
    ),
  },
  {
    id: 'export',
    title: 'Data Export',
    content: (
      <>
        <P>The <PageLink to="/export">Export page</PageLink> lets you download any database table as CSV or XLSX.</P>
        <Ol>
          <li>Select the table to export (customers, accounts, meters, transactions, etc.).</li>
          <li>Optionally search/filter the data.</li>
          <li>Select format: CSV or Excel (XLSX).</li>
          <li>Click <Bold>Export</Bold> — the file downloads to your browser.</li>
        </Ol>
        <Tip>The Payment Verification page also has its own <Bold>Export XLSX</Bold> button for finance team records.</Tip>
      </>
    ),
  },
  {
    id: 'tariffs',
    title: 'Tariff Management',
    content: (
      <>
        <P>The <PageLink to="/tariffs">Tariffs page</PageLink> manages electricity tariff rates per site/concession.</P>
        <Ul>
          <li>View current tariff rates for each site.</li>
          <li>Update rates — changes take effect for future payments immediately.</li>
          <li>Country-specific tariff configuration is supported.</li>
        </Ul>
        <Warning>Changing a tariff rate affects how future payments are converted to kWh. Existing transactions are not recalculated.</Warning>
      </>
    ),
  },
  {
    id: 'admin',
    title: 'Administration',
    content: (
      <>
        <SubHead>Role Management (<PageLink to="/admin/roles">/admin/roles</PageLink>)</SubHead>
        <P>Available to <Bold>superadmin</Bold> users only:</P>
        <Ul>
          <li>View all users and their current roles.</li>
          <li>Assign or change roles (superadmin, onm_team, finance_team, generic).</li>
          <li>Activate or deactivate user accounts.</li>
        </Ul>

        <SubHead>Mutation Audit Trail (<PageLink to="/mutations">/mutations</PageLink>)</SubHead>
        <P>Every data change (create, update, delete) is logged with timestamp, user, table/record affected, and old/new values. Changes can be reviewed and reverted if needed.</P>

        <SubHead>UGridPlan Sync (<PageLink to="/sync">/sync</PageLink>)</SubHead>
        <P>The portal integrates with UGridPlan (<Bold>ugp.1pwrafrica.com</Bold>) via API for customer data synchronization, O&M ticket creation, and survey/connection binding. The sync page shows recent operation status.</P>

        <SubHead>Raw Table Browser (<PageLink to="/tables">/tables</PageLink>)</SubHead>
        <P>For advanced users: browse any database table directly with sorting, filtering, and inline editing capabilities.</P>
      </>
    ),
  },
  {
    id: 'accdb-diff',
    title: 'Differences from Old ACCDB System',
    content: (
      <>
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-gray-100 text-left text-gray-600">
                <th className="px-3 py-2 font-semibold">Old (ACCDB)</th>
                <th className="px-3 py-2 font-semibold">New (CC Portal)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {[
                ['Windows RDP required', 'Web browser from any device'],
                ['VBA forms in Access database', 'Modern React web application'],
                ['Dropbox file paths for imports/exports', 'In-browser data entry and download'],
                ['Spreadsheet-based bulk registration', 'Web forms + UGridPlan sync'],
                ['Spreadsheet-based payment verification', 'In-portal verification queue with bulk actions'],
                ['Reports exported to Dropbox directory', 'Interactive charts + CSV/XLSX export'],
                ['No financing capability', 'Full asset financing with contract generation'],
                ['Manual kWh balance tracking', 'Automated balance engine'],
                ['No meter comparison', 'Check meter deviation analysis'],
                ['No real-time data', 'Live SparkMeter + 1Meter data'],
                ['Single-user at a time', 'Multi-user concurrent access'],
                ['No audit trail', 'Full mutation logging with revert capability'],
                ['No customer self-service', 'Customer login with personal dashboard'],
              ].map(([old, nw], i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-red-700 line-through opacity-70">{old}</td>
                  <td className="px-3 py-1.5 text-green-700 font-medium">{nw}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    ),
  },
];

const SECTION_TITLE_KEYS: Record<string, string> = {
  overview: 'help:sections.overview',
  login: 'help:sections.loginRoles',
  customers: 'help:sections.customerManagement',
  commission: 'help:sections.commissioning',
  payments: 'help:sections.payments',
  financing: 'help:sections.financing',
  meters: 'help:sections.metering',
  reports: 'help:sections.reporting',
  export: 'help:sections.dataExport',
  tariffs: 'help:sections.tariffs',
  admin: 'help:sections.systemAdmin',
};

// ---------------------------------------------------------------------------
// Help Page Component
// ---------------------------------------------------------------------------

export default function HelpPage() {
  const { t } = useTranslation(['help', 'common']);
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const [searchQuery, setSearchQuery] = useState('');
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id);
            break;
          }
        }
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: 0 }
    );

    for (const section of SECTIONS) {
      const el = sectionRefs.current[section.id];
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) => {
    const el = sectionRefs.current[id];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setActiveSection(id);
    }
  };

  const filteredSections = searchQuery
    ? SECTIONS.filter(s =>
        s.title.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : SECTIONS;

  return (
    <div className="flex gap-6 items-start">
      {/* Sidebar TOC - desktop only */}
      <aside className="hidden lg:block w-56 shrink-0 sticky top-20">
        <div className="bg-white rounded-xl border p-4">
          <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">{t('help:tableOfContents')}</h3>
          <nav className="space-y-0.5">
            {SECTIONS.map(s => (
              <button
                key={s.id}
                onClick={() => scrollTo(s.id)}
                className={`block w-full text-left px-2.5 py-1.5 rounded-lg text-sm transition ${
                  activeSection === s.id
                    ? 'bg-blue-50 text-blue-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800'
                }`}
              >
                {SECTION_TITLE_KEYS[s.id] ? t(SECTION_TITLE_KEYS[s.id]) : s.title}
              </button>
            ))}
          </nav>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 min-w-0 max-w-4xl">
        <div className="flex items-center justify-between mb-6 gap-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">{t('help:title')}</h1>
            <p className="text-sm text-gray-500 mt-0.5">{t('help:subtitle')}</p>
          </div>
          <div className="relative">
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={t('help:searchPlaceholder')}
              className="pl-9 pr-3 py-2 border rounded-lg text-sm w-48 focus:ring-2 focus:ring-blue-400 focus:outline-none"
            />
            <svg className="w-4 h-4 text-gray-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
        </div>

        {/* Mobile TOC */}
        <div className="lg:hidden mb-4">
          <details className="bg-white rounded-xl border">
            <summary className="px-4 py-3 text-sm font-medium text-gray-700 cursor-pointer">{t('help:tableOfContents')}</summary>
            <div className="px-4 pb-3 space-y-0.5">
              {SECTIONS.map(s => (
                <button
                  key={s.id}
                  onClick={() => { scrollTo(s.id); }}
                  className="block w-full text-left px-2.5 py-1.5 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
                >
                  {SECTION_TITLE_KEYS[s.id] ? t(SECTION_TITLE_KEYS[s.id]) : s.title}
                </button>
              ))}
            </div>
          </details>
        </div>

        <div className="space-y-6">
          {filteredSections.map((section, idx) => (
            <div
              key={section.id}
              id={section.id}
              ref={el => { sectionRefs.current[section.id] = el; }}
              className="bg-white rounded-xl border p-5 sm:p-6 scroll-mt-20"
            >
              <div className="flex items-center gap-3 mb-4">
                <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold shrink-0">
                  {idx + 1}
                </span>
                <h2 className="text-lg font-bold text-gray-800">{SECTION_TITLE_KEYS[section.id] ? t(SECTION_TITLE_KEYS[section.id]) : section.title}</h2>
              </div>
              {section.content}
            </div>
          ))}
        </div>

        <div className="mt-8 text-center text-xs text-gray-400 pb-8">
          1PWR Customer Care Portal — Revision February 2026 — Administered by OnePower Lesotho
        </div>
      </div>
    </div>
  );
}
