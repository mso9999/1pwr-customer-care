import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useHelpSections } from './helpSections';

const SECTION_TITLE_KEYS: Record<string, string> = {
  overview: 'help:sections.overview',
  login: 'help:sections.loginRoles',
  dashboard: 'help:sections.dashboard',
  sites: 'help:sections.sites',
  customers: 'help:sections.customerManagement',
  commission: 'help:sections.commissioning',
  payments: 'help:sections.payments',
  'balance-adjustments': 'help:sections.balanceAdjustments',
  financing: 'help:sections.financing',
  meters: 'help:sections.metering',
  reports: 'help:sections.reporting',
  'data-browsers': 'help:sections.dataBrowsers',
  export: 'help:sections.dataExport',
  tariffs: 'help:sections.tariffs',
  admin: 'help:sections.systemAdmin',
  'self-service': 'help:sections.selfService',
  sandbox: 'help:sections.sandbox',
  'accdb-diff': 'help:sections.accdbDiff',
};

export default function HelpPage() {
  const location = useLocation();
  const { t, i18n } = useTranslation(['help', 'common']);
  const sections = useHelpSections();
  const [activeSection, setActiveSection] = useState(sections[0].id);
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

    for (const section of sections) {
      const el = sectionRefs.current[section.id];
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, [sections]);

  useEffect(() => {
    const raw = location.hash.replace(/^#/, '');
    if (!raw) return;
    const id = decodeURIComponent(raw);
    if (!sections.some(s => s.id === id)) return;
    setSearchQuery('');
    const frame = requestAnimationFrame(() => {
      const el = sectionRefs.current[id];
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        setActiveSection(id);
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [location.hash, sections]);

  const scrollTo = (id: string) => {
    const el = sectionRefs.current[id];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setActiveSection(id);
    }
  };

  const sectionTitle = (id: string) =>
    SECTION_TITLE_KEYS[id] ? t(SECTION_TITLE_KEYS[id]) : id;

  const q = searchQuery.trim().toLowerCase();
  const filteredSections = q
    ? sections.filter(s => {
        const title = sectionTitle(s.id).toLowerCase();
        const kw = (s.searchKeywords ?? '').toLowerCase();
        return title.includes(q) || kw.includes(q) || s.id.toLowerCase().includes(q);
      })
    : sections;

  return (
    <div className="flex gap-6 items-start">
      {/* Sidebar TOC - desktop only */}
      <aside className="hidden lg:block w-56 shrink-0 sticky top-20">
        <div className="bg-white rounded-xl border p-4">
          <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">{t('help:tableOfContents')}</h3>
          <nav className="space-y-0.5">
            {sections.map(s => (
              <button
                key={s.id}
                onClick={() => scrollTo(s.id)}
                className={`block w-full text-left px-2.5 py-1.5 rounded-lg text-sm transition ${
                  activeSection === s.id
                    ? 'bg-blue-50 text-blue-700 font-medium'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800'
                }`}
              >
                {sectionTitle(s.id)}
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
              {sections.map(s => (
                <button
                  key={s.id}
                  onClick={() => scrollTo(s.id)}
                  className="block w-full text-left px-2.5 py-1.5 rounded-lg text-sm text-gray-600 hover:bg-gray-50"
                >
                  {sectionTitle(s.id)}
                </button>
              ))}
            </div>
          </details>
        </div>

        <div className="space-y-6">
          {filteredSections.map((section, idx) => (
            <div
              key={`${section.id}-${i18n.language}`}
              id={section.id}
              ref={el => { sectionRefs.current[section.id] = el; }}
              className="bg-white rounded-xl border p-5 sm:p-6 scroll-mt-20"
            >
              <div className="flex items-center gap-3 mb-4">
                <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold shrink-0">
                  {idx + 1}
                </span>
                <h2 className="text-lg font-bold text-gray-800">{sectionTitle(section.id)}</h2>
              </div>
              {section.content}
            </div>
          ))}
        </div>

        <div className="mt-8 text-center text-xs text-gray-400 pb-8">
          {t('help:footer')}
        </div>
      </div>
    </div>
  );
}
