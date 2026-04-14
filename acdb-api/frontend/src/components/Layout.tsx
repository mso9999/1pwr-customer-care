import { useState } from 'react';
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useCountry } from '../contexts/CountryContext';

interface NavItemDef {
  to: string;
  labelKey: string;
  icon: string;
  accent?: boolean;
  superadminOnly?: boolean;
}

interface NavSectionDef {
  headingKey: string;
  items: NavItemDef[];
}

const EMPLOYEE_NAV: NavSectionDef[] = [
  {
    headingKey: '',
    items: [
      { to: '/dashboard', labelKey: 'nav.dashboard', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4' },
    ],
  },
  {
    headingKey: 'nav.operations',
    items: [
      { to: '/om-report', labelKey: 'nav.omReport', icon: 'M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z', accent: true },
      { to: '/financial', labelKey: 'nav.financial', icon: 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z', accent: true },
      { to: '/check-meters', labelKey: 'nav.checkMeters', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', accent: true },
      { to: '/tickets', labelKey: 'nav.maintenanceLog', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4', accent: true },
    ],
  },
  {
    headingKey: 'nav.customerData',
    items: [
      { to: '/customers', labelKey: 'nav.customers', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z' },
      { to: '/meters', labelKey: 'nav.meters', icon: 'M13 10V3L4 14h7v7l9-11h-7z' },
      { to: '/accounts', labelKey: 'nav.accounts', icon: 'M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z' },
      { to: '/transactions', labelKey: 'nav.transactions', icon: 'M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4' },
      { to: '/customer-data', labelKey: 'nav.customerDataNav', icon: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4' },
    ],
  },
  {
    headingKey: 'nav.commerce',
    items: [
      { to: '/tariffs', labelKey: 'nav.tariffs', icon: 'M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z' },
      { to: '/financing', labelKey: 'nav.financing', icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4', accent: true },
      { to: '/record-payment', labelKey: 'nav.recordPayment', icon: 'M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z' },
      { to: '/payment-verification', labelKey: 'nav.verifyPayments', icon: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' },
      { to: '/pipeline', labelKey: 'nav.pipeline', icon: 'M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z' },
    ],
  },
  {
    headingKey: 'nav.system',
    items: [
      { to: '/tables', labelKey: 'nav.tables', icon: 'M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z' },
      { to: '/export', labelKey: 'nav.export', icon: 'M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
      { to: '/mutations', labelKey: 'nav.mutations', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' },
      { to: '/sync', labelKey: 'nav.sync', icon: 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15', accent: true },
      { to: '/help', labelKey: 'nav.help', icon: 'M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
      { to: '/tutorial', labelKey: 'nav.tutorial', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253' },
      { to: '/admin/roles', labelKey: 'nav.roles', icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z', superadminOnly: true },
    ],
  },
];

const CUSTOMER_NAV: NavItemDef[] = [
  { to: '/my/dashboard', labelKey: 'nav.dashboard', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0h4' },
  { to: '/my/profile', labelKey: 'nav.myAccount', icon: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z' },
];

function NavIcon({ d }: { d: string }) {
  return (
    <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

export default function Layout() {
  const { user, logout, isEmployee, isCustomer, isSuperadmin } = useAuth();
  const { country, setCountry, countries, portfolio, setPortfolio } = useCountry();
  const { t, i18n } = useTranslation('common');
  const currentCountry = countries.find((c) => c.code === country);
  const countryPortfolios = currentCountry?.portfolios ?? [];
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const currentLang = i18n.language?.startsWith('fr') ? 'fr' : 'en';

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const isActive = (to: string) =>
    location.pathname === to || location.pathname.startsWith(to + '/');

  const renderLink = (item: NavItemDef) => {
    if (item.superadminOnly && !isSuperadmin) return null;
    const active = isActive(item.to);
    const cls = item.accent
      ? active
        ? 'bg-blue-600 text-white'
        : 'text-blue-700 bg-blue-50/80 hover:bg-blue-100'
      : active
        ? 'bg-blue-50 text-blue-700 font-semibold'
        : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900';
    return (
      <Link
        key={item.to}
        to={item.to}
        onClick={() => setSidebarOpen(false)}
        className={`flex items-center gap-2.5 px-3 py-1.5 rounded-md text-[13px] font-medium transition-colors ${cls}`}
      >
        <NavIcon d={item.icon} />
        {t(item.labelKey)}
      </Link>
    );
  };

  const sidebarContent = (
    <>
      {/* Logo */}
      <div className="px-4 pt-4 pb-3">
        <Link to="/" className="flex items-center gap-2.5" onClick={() => setSidebarOpen(false)}>
          <img src="/1pwr-logo.png" alt="1PWR" className="h-8 w-auto" />
          <span className="text-base font-bold text-blue-700 whitespace-nowrap">{t('appName')}</span>
        </Link>
      </div>

      {/* Country / Portfolio selectors */}
      {isEmployee && (
        <div className="px-3 pb-3 space-y-1.5">
          <select
            value={country}
            onChange={(e) => { setCountry(e.target.value); setPortfolio(null); }}
            className="w-full text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-blue-400 focus:outline-none"
          >
            {countries.map((c) => (
              <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
            ))}
          </select>
          {countryPortfolios.length > 0 && (
            <select
              value={portfolio?.id ?? ''}
              onChange={(e) => {
                const p = countryPortfolios.find((x) => x.id === e.target.value) ?? null;
                setPortfolio(p);
              }}
              className="w-full text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-blue-400 focus:outline-none truncate"
            >
              <option value="">{t('allPortfolios')}</option>
              {countryPortfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}
        </div>
      )}

      <div className="border-t border-gray-200" />

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-3">
        {isEmployee && EMPLOYEE_NAV.map((section) => (
          <div key={section.headingKey || '__root'}>
            {section.headingKey && (
              <p className="px-3 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                {t(section.headingKey)}
              </p>
            )}
            <div className="space-y-0.5">
              {section.items.map(renderLink)}
            </div>
          </div>
        ))}
        {isCustomer && (
          <div className="space-y-0.5">
            {CUSTOMER_NAV.map(renderLink)}
          </div>
        )}
      </nav>

      {/* User footer */}
      {user && (
        <div className="border-t border-gray-200 p-3">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-xs font-bold text-blue-700">
              {(user.name || user.user_id || '?')[0].toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-gray-700 truncate">{user.name || user.user_id}</p>
              {isEmployee && (
                <span className="inline-block px-1.5 py-0.5 bg-blue-100 text-blue-700 text-[10px] font-medium rounded-full">
                  {user.role}
                </span>
              )}
            </div>
          </div>
          {/* Language toggle */}
          <div className="flex rounded-lg bg-gray-100 p-0.5 mb-2">
            <button
              onClick={() => i18n.changeLanguage('en')}
              className={`flex-1 py-1 text-xs font-semibold rounded-md transition ${currentLang === 'en' ? 'bg-white shadow text-blue-700' : 'text-gray-400 hover:text-gray-600'}`}
            >
              EN
            </button>
            <button
              onClick={() => i18n.changeLanguage('fr')}
              className={`flex-1 py-1 text-xs font-semibold rounded-md transition ${currentLang === 'fr' ? 'bg-white shadow text-blue-700' : 'text-gray-400 hover:text-gray-600'}`}
            >
              FR
            </button>
          </div>
          <button
            onClick={handleLogout}
            className="w-full text-sm text-red-600 hover:text-red-800 hover:bg-red-50 px-3 py-1.5 border border-red-200 rounded-lg transition-colors text-center font-medium"
          >
            {t('logout')}
          </button>
        </div>
      )}
    </>
  );

  return (
    <div className="min-h-screen bg-gray-50 lg:flex">
      {/* ── Desktop sidebar (lg+) ── */}
      <aside className="hidden lg:flex lg:flex-col lg:w-56 lg:fixed lg:inset-y-0 bg-white border-r border-gray-200 z-30">
        {sidebarContent}
      </aside>

      {/* ── Mobile overlay backdrop ── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── Mobile slide-out sidebar ── */}
      <aside
        className={`fixed inset-y-0 left-0 w-64 bg-white border-r border-gray-200 z-50 flex flex-col transform transition-transform duration-200 ease-in-out lg:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {sidebarContent}
      </aside>

      {/* ── Main area ── */}
      <div className="flex-1 lg:pl-56 flex flex-col min-h-screen">
        {/* Mobile top bar */}
        <header className="lg:hidden bg-white border-b border-gray-200 sticky top-0 z-30">
          <div className="flex items-center justify-between h-12 px-4">
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-2 -ml-2 rounded-md text-gray-500 hover:bg-gray-100 focus:outline-none"
              aria-label="Open menu"
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <Link to="/" className="flex items-center gap-2" onClick={() => setSidebarOpen(false)}>
              <img src="/1pwr-logo.png" alt="1PWR" className="h-7 w-auto" />
              <span className="text-base font-bold text-blue-700">{t('appName')}</span>
            </Link>
            <div className="w-10" />
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 px-4 sm:px-6 lg:px-8 py-4 sm:py-6 max-w-7xl w-full mx-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
