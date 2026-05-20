import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useCountry, COUNTRY_ROUTES, type CountryConfig } from '../contexts/CountryContext';
import {
  getAnalyticsMetrics,
  runAnalyticsQuery,
  type AnalyticsQueryResponse,
  type AnalyticsMetricsCatalog,
} from '../lib/api';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

const COLORS = [
  '#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#9333ea',
  '#0891b2', '#d97706', '#4f46e5', '#db2777', '#65a30d',
];

const BASIS_OPTIONS = [
  { value: 'site', labelKey: 'basis.site', groupBy: 'site' },
  { value: 'customer_type', labelKey: 'basis.customerType', groupBy: 'customer_type' },
  { value: 'time', labelKey: 'basis.time', groupBy: 'month' },
  { value: 'overview', labelKey: 'basis.overview', groupBy: 'none' },
];

// ── Helpers ──

function fmtValue(val: any, format: string): string {
  if (val == null) return '—';
  const n = Number(val);
  if (isNaN(n)) return String(val);
  switch (format) {
    case 'integer':
      return n.toLocaleString();
    case 'decimal2':
      return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'currency':
      return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'percent':
      return n.toFixed(1) + '%';
    default:
      return n.toLocaleString();
  }
}

/** Which group_by values indicate a time-series capable metric */
const TIME_GROUP_BYS = new Set(['month', 'quarter', 'year']);

function metricFitsBasis(metric: { group_by_options: string[] }, basis: string): boolean {
  const opts = metric.group_by_options;
  switch (basis) {
    case 'site':
      return opts.includes('site');
    case 'customer_type':
      return opts.includes('customer_type');
    case 'time':
      return opts.some((g) => TIME_GROUP_BYS.has(g));
    case 'overview':
      return opts.includes('none');
    default:
      return true;
  }
}

// ── MultiSelect dropdown component ──

function MultiSelect({
  label,
  options,
  selected,
  onChange,
  allLabel,
}: {
  label: string;
  options: { code: string; label: string }[];
  selected: string[];
  onChange: (vals: string[]) => void;
  allLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggle = (code: string) => {
    if (selected.includes(code)) {
      onChange(selected.filter((s) => s !== code));
    } else {
      onChange([...selected, code]);
    }
  };

  const selectAll = () => onChange([]);
  const hasSelection = selected.length > 0;

  return (
    <div ref={ref} className="relative">
      <span className="text-xs text-gray-500">{label}</span>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="mt-1 flex items-center justify-between w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm text-left hover:border-gray-400"
      >
        <span className={hasSelection ? 'text-gray-800' : 'text-gray-400'}>
          {hasSelection ? `${selected.length} selected` : allLabel}
        </span>
        <span className="text-gray-400 ml-1">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-full bg-white border border-gray-200 rounded shadow-lg max-h-52 overflow-y-auto">
          <button
            type="button"
            onClick={selectAll}
            className="w-full text-left px-3 py-1.5 text-xs text-blue-600 hover:bg-blue-50 border-b border-gray-100"
          >
            {allLabel}
          </button>
          {options.map((o) => (
            <label
              key={o.code}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selected.includes(o.code)}
                onChange={() => toggle(o.code)}
                className="rounded border-gray-300 text-blue-600"
              />
              {o.label}
            </label>
          ))}
        </div>
      )}
      {hasSelection && (
        <div className="flex flex-wrap gap-1 mt-1">
          {selected.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-xs bg-blue-50 text-blue-700 rounded-full"
            >
              {s}
              <button type="button" onClick={() => toggle(s)} className="text-blue-400 hover:text-blue-600">&times;</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Page ──

export default function AnalyticsPage() {
  const { t } = useTranslation(['analytics', 'common']);
  const { country, config } = useCountry();

  // Catalog
  const [catalog, setCatalog] = useState<AnalyticsMetricsCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);

  useEffect(() => {
    getAnalyticsMetrics()
      .then(setCatalog)
      .catch(() => {})
      .finally(() => setCatalogLoading(false));
  }, []);

  // Filters
  const [filterCountry, setFilterCountry] = useState(country);
  const [filterSites, setFilterSites] = useState<string[]>([]);
  const [filterCustomerTypes, setFilterCustomerTypes] = useState<string[]>([]);
  const [countrySites, setCountrySites] = useState<Record<string, Record<string, string>>>({});
  const [filterDateFrom, setFilterDateFrom] = useState('2020-01-01');
  const [filterDateTo, setFilterDateTo] = useState(
    new Date().toISOString().slice(0, 10),
  );

  // Basis + time granularity
  const [basis, setBasis] = useState('site');
  const [timeGranularity, setTimeGranularity] = useState('month');

  // Derive group_by from basis
  const groupBy = useMemo(() => {
    if (basis === 'time') return timeGranularity;
    return BASIS_OPTIONS.find((b) => b.value === basis)?.groupBy || 'none';
  }, [basis, timeGranularity]);

  useEffect(() => {
    setFilterCountry(country);
  }, [country]);

  // Keep a local cache of site maps by country code so the site picker
  // follows the analytics filter country (not just the global app country).
  useEffect(() => {
    if (!config?.sites || !country) return;
    setCountrySites((prev) => ({ ...prev, [country]: config.sites }));
  }, [country, config]);

  useEffect(() => {
    if (!filterCountry) return;
    if (countrySites[filterCountry]) return;

    const routeBase = COUNTRY_ROUTES[filterCountry] || '/api';
    const token = localStorage.getItem('cc_token') || '';
    const headers: Record<string, string> = token
      ? { Authorization: `Bearer ${token}` }
      : {};

    fetch(`${routeBase}/config`, { headers })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<CountryConfig>;
      })
      .then((cfg) => {
        if (!cfg?.sites) return;
        setCountrySites((prev) => ({ ...prev, [filterCountry]: cfg.sites }));
      })
      .catch(() => {});
  }, [filterCountry, countrySites]);

  // Metric selection
  const [selectedMetrics, setSelectedMetrics] = useState<Set<string>>(new Set());

  // Clear metrics when basis changes
  useEffect(() => {
    setSelectedMetrics(new Set());
  }, [basis]);

  const toggleMetric = (id: string) => {
    setSelectedMetrics((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectCategory = (cat: string) => {
    if (!catalog) return;
    const ids = visibleMetrics.filter((m) => m.category === cat).map((m) => m.id);
    setSelectedMetrics((prev) => {
      const next = new Set(prev);
      const allSelected = ids.every((id) => next.has(id));
      if (allSelected) ids.forEach((id) => next.delete(id));
      else ids.forEach((id) => next.add(id));
      return next;
    });
  };

  // Query
  const [result, setResult] = useState<AnalyticsQueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const runQuery = useCallback(async () => {
    if (selectedMetrics.size === 0) return;
    setLoading(true);
    setError('');
    try {
      const res = await runAnalyticsQuery({
        metrics: Array.from(selectedMetrics),
        filters: {
          country: filterCountry || undefined,
          sites: filterSites.length > 0 ? filterSites : undefined,
          customer_types: filterCustomerTypes.length > 0 ? filterCustomerTypes : undefined,
          // Only send dates for time-series basis
          ...(basis === 'time' ? {
            date_from: filterDateFrom,
            date_to: filterDateTo,
          } : {}),
        },
        group_by: groupBy,
        time_series: true,
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message || 'Query failed');
    } finally {
      setLoading(false);
    }
  }, [selectedMetrics, filterCountry, filterSites, filterCustomerTypes, filterDateFrom, filterDateTo, groupBy, basis]);

  // Chart
  const [chartType, setChartType] = useState<'bar' | 'line'>('bar');

  const chartData = useMemo(() => {
    if (!result?.series || result.series.length === 0) return [];
    const map = new Map<string, Record<string, any>>();
    for (const s of result.series) {
      for (const pt of s.points) {
        if (!map.has(pt.group)) map.set(pt.group, { group: pt.group });
        map.get(pt.group)![s.metric_id] = pt.value;
      }
    }
    return Array.from(map.values());
  }, [result]);

  const tableRows = useMemo(() => {
    if (!result?.metrics) return [];
    const rowMap = new Map<string, Record<string, any>>();
    for (const [mid, mdata] of Object.entries(result.metrics)) {
      for (const row of mdata.data) {
        const gk = row.group_key ?? '—';
        if (!rowMap.has(gk)) rowMap.set(gk, { group_key: gk });
        rowMap.get(gk)![mid] = row.value;
      }
    }
    return Array.from(rowMap.values());
  }, [result]);

  // Filter metrics by current basis
  const visibleMetrics = useMemo(() => {
    if (!catalog) return [];
    return catalog.metrics.filter((m) => metricFitsBasis(m, basis));
  }, [catalog, basis]);

  const visibleCategories = useMemo(() => {
    const cats = new Set(visibleMetrics.map((m) => m.category));
    return Array.from(cats).sort(
      (a, b) => ({ funnel: 0, customer: 1, financial: 2, consumption: 3 }[a] ?? 9)
              - ({ funnel: 0, customer: 1, financial: 2, consumption: 3 }[b] ?? 9)
    );
  }, [visibleMetrics]);

  const selectedMeta = useMemo(() => {
    return visibleMetrics.filter((m) => selectedMetrics.has(m.id));
  }, [visibleMetrics, selectedMetrics]);

  // Site / type options
  const siteOptions = useMemo(() => {
    const sitesForCountry = countrySites[filterCountry] || {};
    return Object.entries(sitesForCountry).map(([code, name]) => ({ code, label: `${code} — ${name}` }));
  }, [countrySites, filterCountry]);

  const customerTypeOptions = useMemo(() => {
    return (catalog?.customer_types || []).map((ct) => ({ code: ct, label: ct }));
  }, [catalog]);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-800">{t('title')}</h1>
        <p className="text-sm text-gray-500 mt-1">{t('subtitle')}</p>
      </div>

      {/* ── Basis selector ── */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-gray-600">{t('basis.label')}</span>
          {BASIS_OPTIONS.map((b) => (
            <button
              key={b.value}
              type="button"
              onClick={() => setBasis(b.value)}
              className={`px-4 py-2 text-sm rounded-lg border-2 transition-colors ${
                basis === b.value
                  ? 'border-blue-500 bg-blue-50 text-blue-700 font-medium'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
              }`}
            >
              {t(b.labelKey)}
            </button>
          ))}
        </div>
        {basis === 'time' && (
          <div className="mt-3 flex items-center gap-2">
            <span className="text-xs text-gray-500">{t('basis.timeGranularity')}</span>
            <select
              className="rounded border-gray-300 text-sm"
              value={timeGranularity}
              onChange={(e) => setTimeGranularity(e.target.value)}
            >
              <option value="month">{t('basis.month')}</option>
              <option value="quarter">{t('basis.quarter')}</option>
              <option value="year">{t('basis.year')}</option>
            </select>
          </div>
        )}
      </div>

      {/* ── Filter Bar ── */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <h2 className="text-sm font-medium text-gray-600 mb-3">{t('filters')}</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
          <label className="block">
            <span className="text-xs text-gray-500">{t('country')}</span>
            <select
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              value={filterCountry}
              onChange={(e) => { setFilterCountry(e.target.value); setFilterSites([]); }}
            >
              <option value="LS">Lesotho</option>
              <option value="BN">Benin</option>
            </select>
          </label>
          <MultiSelect
            label={t('sites')}
            options={siteOptions}
            selected={filterSites}
            onChange={setFilterSites}
            allLabel={t('allSites')}
          />
          <MultiSelect
            label={t('customerTypes')}
            options={customerTypeOptions}
            selected={filterCustomerTypes}
            onChange={setFilterCustomerTypes}
            allLabel={t('allCustomerTypes')}
          />
          {basis === 'time' && (
            <>
              <label className="block">
                <span className="text-xs text-gray-500">{t('dateFrom')}</span>
                <input
                  type="date"
                  className="mt-1 block w-full rounded border-gray-300 text-sm"
                  value={filterDateFrom}
                  onChange={(e) => setFilterDateFrom(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-500">{t('dateTo')}</span>
                <input
                  type="date"
                  className="mt-1 block w-full rounded border-gray-300 text-sm"
                  value={filterDateTo}
                  onChange={(e) => setFilterDateTo(e.target.value)}
                />
              </label>
            </>
          )}
        </div>
      </div>

      {/* ── Metric Selector ── */}
      {catalogLoading ? (
        <div className="text-center py-4 text-gray-400">{t('common:loading')}</div>
      ) : catalog ? (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-600">{t('selectMetrics')}</h2>
            {visibleMetrics.length === 0 && (
              <span className="text-xs text-gray-400">{t('basis.noMetrics')}</span>
            )}
          </div>
          {visibleCategories.map((cat) => {
            const catMetrics = visibleMetrics.filter((m) => m.category === cat);
            const allSelected = catMetrics.length > 0 && catMetrics.every((m) => selectedMetrics.has(m.id));
            return (
              <div key={cat} className="mb-3 last:mb-0">
                <button
                  type="button"
                  onClick={() => selectCategory(cat)}
                  className="text-xs font-semibold text-gray-500 hover:text-gray-700 mb-1.5 flex items-center gap-1"
                >
                  <span className={`inline-block w-3 h-3 rounded border ${allSelected ? 'bg-blue-500 border-blue-500' : 'border-gray-300'}`} />
                  {t(`category.${cat}`)}
                </button>
                <div className="flex flex-wrap gap-2 ml-5">
                  {catMetrics.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => toggleMetric(m.id)}
                      className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                        selectedMetrics.has(m.id)
                          ? 'bg-blue-50 border-blue-300 text-blue-700'
                          : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'
                      }`}
                      title={m.description}
                    >
                      {m.name}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
          <button
            onClick={runQuery}
            disabled={selectedMetrics.size === 0 || loading}
            className="mt-4 px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 disabled:opacity-40 transition-colors"
          >
            {loading ? t('loading') : t('runQuery')}
          </button>
        </div>
      ) : null}

      {/* ── Error ── */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 text-sm rounded">{error}</div>
      )}

      {/* ── Results Table ── */}
      {result && tableRows.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
            <h2 className="text-sm font-medium text-gray-600">{t('results')}</h2>
            <button
              onClick={() => {
                if (!result) return;
                const headers = ['group_key', ...Array.from(selectedMetrics)];
                const csv = [headers.join(','),
                  ...tableRows.map((row) =>
                    headers.map((h) => JSON.stringify(row[h] ?? '')).join(','),
                  ),
                ].join('\n');
                const blob = new Blob([csv], { type: 'text/csv' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = 'analytics-export.csv'; a.click();
                URL.revokeObjectURL(url);
              }}
              className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
            >
              {t('exportCsv')}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="px-4 py-2 font-medium whitespace-nowrap">
                    {BASIS_OPTIONS.find((b) => b.value === basis)?.labelKey
                      ? t(BASIS_OPTIONS.find((b) => b.value === basis)!.labelKey)
                      : groupBy}
                  </th>
                  {selectedMeta.map((m) => (
                    <th key={m.id} className="px-4 py-2 font-medium whitespace-nowrap" title={m.description}>
                      {m.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {tableRows.map((row, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium text-gray-800 whitespace-nowrap">{row.group_key}</td>
                    {selectedMeta.map((m) => (
                      <td key={m.id} className="px-4 py-2 font-mono tabular-nums text-gray-700">
                        {fmtValue(row[m.id], m.value_format)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result && tableRows.length === 0 && (
        <div className="text-center py-12 text-gray-400">{t('noData')}</div>
      )}

      {/* ── Chart ── */}
      {chartData.length > 0 && result?.series && result.series.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-600">{t('chart')}</h2>
            <div className="flex gap-1">
              <button
                onClick={() => setChartType('bar')}
                className={`px-3 py-1 text-xs rounded ${chartType === 'bar' ? 'bg-blue-100 text-blue-700' : 'text-gray-500 hover:bg-gray-100'}`}
              >
                {t('barChart')}
              </button>
              <button
                onClick={() => setChartType('line')}
                className={`px-3 py-1 text-xs rounded ${chartType === 'line' ? 'bg-blue-100 text-blue-700' : 'text-gray-500 hover:bg-gray-100'}`}
              >
                {t('lineChart')}
              </button>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={400}>
            {chartType === 'bar' ? (
              <BarChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="group" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
                <Legend />
                {result.series.map((s, i) => (
                  <Bar
                    key={s.metric_id}
                    dataKey={s.metric_id}
                    fill={COLORS[i % COLORS.length]}
                    radius={[4, 4, 0, 0]}
                    name={s.metric_name}
                  />
                ))}
              </BarChart>
            ) : (
              <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="group" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
                <Legend />
                {result.series.map((s, i) => (
                  <Line
                    key={s.metric_id}
                    type="monotone"
                    dataKey={s.metric_id}
                    stroke={COLORS[i % COLORS.length]}
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    name={s.metric_name}
                  />
                ))}
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Empty state ── */}
      {!result && !loading && (
        <div className="text-center py-20 text-gray-400">
          <p className="text-lg mb-1">{t('noMetricsSelected')}</p>
        </div>
      )}
    </div>
  );
}
