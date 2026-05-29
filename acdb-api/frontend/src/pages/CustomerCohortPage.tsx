import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCountry } from '../contexts/CountryContext';
import {
  exportCustomerCohort,
  getCustomerCohortStatuses,
  queryCustomerCohort,
  type CohortExportColumn,
  type CohortQueryResponse,
  type CohortConnectionStatus,
  type CohortContractStatus,
  type CohortStatus,
} from '../lib/api';

const ALL_STATUSES: CohortStatus[] = [
  'not_paid',
  'partially_paid_not_connected',
  'partially_paid_connected',
  'partially_paid_not_metered',
  'fully_paid_not_connected',
  'fully_paid_connected',
  'fully_paid_not_metered',
  'terminated',
];

const ALL_CONNECTION: CohortConnectionStatus[] = [
  'not_connected',
  'connected',
  'terminated',
];

const ALL_CONTRACT: CohortContractStatus[] = ['signed', 'not_signed'];

const CUSTOMER_TYPES = [
  'HH1', 'HH2', 'HH3', 'SME', 'CHU', 'SCH', 'HC',
  'GOV', 'COM', 'IND', 'SCP', 'REL', 'AGR', 'CLI', 'PUE',
  'HCF', 'OTH',
];

type SortBy =
  | 'site'
  | 'account_number'
  | 'name'
  | 'phone'
  | 'customer_type'
  | 'total_paid'
  | 'first_fee_payment_date'
  | 'date_connected'
  | 'cohort_status';

const PAGE_SIZE = 50;

// ── helpers ─────────────────────────────────────────────────────────

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '—';
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  return s.slice(0, 10);
}

function fullName(row: { first_name: string | null; last_name: string | null }): string {
  return [row.first_name, row.last_name].filter(Boolean).join(' ').trim() || '—';
}

function statusColor(status: CohortStatus): string {
  switch (status) {
    case 'fully_paid_connected':         return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'fully_paid_not_metered':       return 'bg-teal-50 text-teal-800 border-teal-200';
    case 'partially_paid_connected':     return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'partially_paid_not_metered':   return 'bg-yellow-50 text-yellow-800 border-yellow-200';
    case 'partially_paid_not_connected': return 'bg-orange-50 text-orange-700 border-orange-200';
    case 'fully_paid_not_connected':     return 'bg-blue-50 text-blue-700 border-blue-200';
    case 'not_paid':                     return 'bg-rose-50 text-rose-700 border-rose-200';
    case 'terminated':                   return 'bg-gray-100 text-gray-600 border-gray-200';
    default:                             return 'bg-gray-50 text-gray-600 border-gray-200';
  }
}

// ── MultiSelect ─────────────────────────────────────────────────────

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
    if (selected.includes(code)) onChange(selected.filter((s) => s !== code));
    else onChange([...selected, code]);
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
        <div className="absolute z-20 mt-1 w-full bg-white border border-gray-200 rounded shadow-lg max-h-60 overflow-y-auto">
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
              <button
                type="button"
                onClick={() => toggle(s)}
                className="text-blue-400 hover:text-blue-600"
              >
                &times;
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Export column picker ────────────────────────────────────────────

function exportColLabel(
  t: (key: string, opts?: Record<string, unknown>) => string,
  col: CohortExportColumn,
): string {
  const key = `exportCol.${col.id}`;
  const translated = t(key);
  return translated === key ? col.label : translated;
}

function CohortExportModal({
  open,
  catalog,
  defaultOptional,
  selected,
  onChangeSelected,
  onClose,
  onConfirm,
  exporting,
  error,
}: {
  open: boolean;
  catalog: CohortExportColumn[];
  defaultOptional: string[];
  selected: Set<string>;
  onChangeSelected: (next: Set<string>) => void;
  onClose: () => void;
  onConfirm: () => void;
  exporting: boolean;
  error: string;
}) {
  const { t } = useTranslation(['customerCohort']);

  if (!open) return null;

  const mandatory = catalog.filter((c) => c.mandatory);
  const defaultSet = new Set(defaultOptional);
  const tableCols = catalog.filter((c) => !c.mandatory && defaultSet.has(c.id));
  const extraCols = catalog.filter((c) => !c.mandatory && !defaultSet.has(c.id));

  const toggle = (id: string, mandatoryCol: boolean) => {
    if (mandatoryCol) return;
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChangeSelected(next);
  };

  const setOptional = (ids: string[]) => {
    const next = new Set<string>();
    for (const c of mandatory) next.add(c.id);
    for (const id of ids) next.add(id);
    onChangeSelected(next);
  };

  const renderGroup = (title: string, cols: CohortExportColumn[]) => (
    <div key={title}>
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{title}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        {cols.map((col) => (
          <label
            key={col.id}
            className={`flex items-center gap-2 text-sm rounded px-2 py-1.5 ${
              col.mandatory ? 'bg-gray-50 text-gray-600' : 'hover:bg-gray-50 cursor-pointer'
            }`}
          >
            <input
              type="checkbox"
              checked={col.mandatory || selected.has(col.id)}
              disabled={col.mandatory || exporting}
              onChange={() => toggle(col.id, col.mandatory)}
              className="rounded border-gray-300 text-blue-600"
            />
            {exportColLabel(t, col)}
          </label>
        ))}
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div
        className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] flex flex-col"
        role="dialog"
        aria-labelledby="cohort-export-title"
      >
        <div className="px-5 py-4 border-b border-gray-200">
          <h2 id="cohort-export-title" className="text-lg font-semibold text-gray-800">
            {t('exportTitle')}
          </h2>
          <p className="text-sm text-gray-500 mt-1">{t('exportHint')}</p>
        </div>
        <div className="px-5 py-4 overflow-y-auto space-y-4 flex-1">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={exporting}
              onClick={() => setOptional(defaultOptional)}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
            >
              {t('exportSelectTableDefaults')}
            </button>
            <button
              type="button"
              disabled={exporting}
              onClick={() => setOptional(catalog.filter((c) => !c.mandatory).map((c) => c.id))}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
            >
              {t('exportSelectAllOptional')}
            </button>
            <button
              type="button"
              disabled={exporting}
              onClick={() => setOptional([])}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
            >
              {t('exportClearOptional')}
            </button>
          </div>
          {renderGroup(t('exportMandatory'), mandatory)}
          {tableCols.length > 0 && renderGroup(t('exportTableColumns'), tableCols)}
          {extraCols.length > 0 && renderGroup(t('exportExtraAttributes'), extraCols)}
        </div>
        {error && (
          <div className="mx-5 mb-2 p-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded">
            {error}
          </div>
        )}
        <div className="px-5 py-4 border-t border-gray-200 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={exporting}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 disabled:opacity-40"
          >
            {t('exportCancel')}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={exporting}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40"
          >
            {exporting ? t('exporting') : t('exportConfirm')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────

export default function CustomerCohortPage() {
  const { t } = useTranslation(['customerCohort', 'common']);
  const { country, setCountry, config } = useCountry();

  // Filter state
  const [filterSites, setFilterSites] = useState<string[]>([]);
  const [filterTypes, setFilterTypes] = useState<string[]>([]);
  const [filterStatuses, setFilterStatuses] = useState<string[]>([]);
  const [filterConnection, setFilterConnection] = useState<string[]>([]);
  const [filterContract, setFilterContract] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // Sort + page
  const [sortBy, setSortBy] = useState<SortBy>('site');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);

  // Data
  const [result, setResult] = useState<CohortQueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [exportCatalog, setExportCatalog] = useState<CohortExportColumn[]>([]);
  const [defaultExportOptional, setDefaultExportOptional] = useState<string[]>([]);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportSelected, setExportSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');

  useEffect(() => {
    setFilterSites([]);
  }, [country]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [country, filterSites, filterTypes, filterStatuses, filterConnection, filterContract, search]);

  const runQuery = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await queryCustomerCohort({
        filters: {
          country: country || undefined,
          sites: filterSites.length ? filterSites : undefined,
          customer_types: filterTypes.length ? filterTypes : undefined,
          statuses: filterStatuses.length ? (filterStatuses as CohortStatus[]) : undefined,
          connection_statuses: filterConnection.length
            ? (filterConnection as CohortConnectionStatus[])
            : undefined,
          contract_statuses: filterContract.length
            ? (filterContract as CohortContractStatus[])
            : undefined,
          search: search.trim() || undefined,
        },
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        page_size: PAGE_SIZE,
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message || t('queryFailed'));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [
    country,
    filterSites,
    filterTypes,
    filterStatuses,
    filterConnection,
    filterContract,
    search,
    sortBy,
    sortDir,
    page,
    t,
  ]);

  useEffect(() => { void runQuery(); }, [runQuery]);

  useEffect(() => {
    getCustomerCohortStatuses()
      .then((cat) => {
        setExportCatalog(cat.export_columns || []);
        const defaults = cat.default_export_columns || [];
        setDefaultExportOptional(defaults);
        const initial = new Set<string>();
        for (const c of cat.export_columns || []) {
          if (c.mandatory || defaults.includes(c.id)) initial.add(c.id);
        }
        setExportSelected(initial);
      })
      .catch(() => {});
  }, []);

  const totalPages = useMemo(
    () => (result ? Math.max(1, Math.ceil(result.total / PAGE_SIZE)) : 1),
    [result],
  );

  // Site / type options
  const siteOptions = useMemo(() => {
    if (!config?.sites) return [];
    return Object.entries(config.sites).map(([code, name]) => ({ code, label: `${code} — ${name}` }));
  }, [config]);

  const typeOptions = useMemo(
    () => CUSTOMER_TYPES.map((c) => ({ code: c, label: c })),
    [],
  );

  const statusOptions = useMemo(
    () => ALL_STATUSES.map((s) => ({ code: s, label: t(`status.${s}`) })),
    [t],
  );

  const connectionOptions = useMemo(
    () => ALL_CONNECTION.map((s) => ({ code: s, label: t(`connection.${s}`) })),
    [t],
  );

  const contractOptions = useMemo(
    () => ALL_CONTRACT.map((s) => ({ code: s, label: t(`contract.${s}`) })),
    [t],
  );

  const handleSort = (col: SortBy) => {
    if (sortBy === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(col);
      setSortDir('asc');
    }
  };

  const sortIcon = (col: SortBy) =>
    sortBy === col ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  const buildCsv = (
    columns: string[],
    labels: Record<string, string>,
    rows: Record<string, unknown>[],
  ) => {
    const escape = (v: unknown) => {
      if (v == null) return '';
      const s = String(v).replace(/"/g, '""');
      return /[",\n]/.test(s) ? `"${s}"` : s;
    };
    const header = columns.map((c) => labels[c] || c);
    const lines = [header.join(',')];
    for (const r of rows) {
      lines.push(columns.map((c) => escape(r[c])).join(','));
    }
    return lines.join('\n');
  };

  const runExport = async () => {
    setExporting(true);
    setExportError('');
    try {
      const res = await exportCustomerCohort({
        filters: {
          country: country || undefined,
          sites: filterSites.length ? filterSites : undefined,
          customer_types: filterTypes.length ? filterTypes : undefined,
          statuses: filterStatuses.length ? (filterStatuses as CohortStatus[]) : undefined,
          connection_statuses: filterConnection.length
            ? (filterConnection as CohortConnectionStatus[])
            : undefined,
          contract_statuses: filterContract.length
            ? (filterContract as CohortContractStatus[])
            : undefined,
          search: search.trim() || undefined,
        },
        sort_by: sortBy,
        sort_dir: sortDir,
        columns: Array.from(exportSelected),
      });
      if (res.exported === 0) {
        setExportError(t('noResults'));
        return;
      }
      const labels: Record<string, string> = {};
      for (const col of exportCatalog) {
        labels[col.id] = exportColLabel(t, col);
      }
      Object.assign(labels, res.column_labels);
      const csv = buildCsv(res.columns, labels, res.rows);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `customer-cohort-${country}-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setExportModalOpen(false);
      if (res.truncated) {
        window.alert(
          t('exportTruncated', { max: 50000, total: res.total }),
        );
      }
    } catch (e: unknown) {
      setExportError(e instanceof Error ? e.message : t('exportFailed'));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-800">{t('title')}</h1>
        <p className="text-sm text-gray-500 mt-1">{t('subtitle')}</p>
      </div>

      {/* ── Filter bar ── */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <label className="block">
            <span className="text-xs text-gray-500">{t('country')}</span>
            <select
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              value={country}
              onChange={(e) => { setCountry(e.target.value); setFilterSites([]); }}
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
            options={typeOptions}
            selected={filterTypes}
            onChange={setFilterTypes}
            allLabel={t('allTypes')}
          />
          <label className="block">
            <span className="text-xs text-gray-500">{t('search')}</span>
            <form
              onSubmit={(e) => { e.preventDefault(); setSearch(searchInput); }}
              className="mt-1 flex gap-1"
            >
              <input
                type="text"
                placeholder={t('searchPlaceholder')}
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="flex-1 min-w-0 rounded border-gray-300 text-sm"
              />
              <button
                type="submit"
                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                {t('go')}
              </button>
            </form>
          </label>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-3 pt-3 border-t border-gray-100">
          <MultiSelect
            label={t('paymentStatus')}
            options={statusOptions}
            selected={filterStatuses}
            onChange={setFilterStatuses}
            allLabel={t('anyPaymentStatus')}
          />
          <MultiSelect
            label={t('connectionStatus')}
            options={connectionOptions}
            selected={filterConnection}
            onChange={setFilterConnection}
            allLabel={t('anyConnectionStatus')}
          />
          <MultiSelect
            label={t('contractStatus')}
            options={contractOptions}
            selected={filterContract}
            onChange={setFilterContract}
            allLabel={t('anyContractStatus')}
          />
        </div>
      </div>

      {/* ── Results header ── */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-600">
          {loading ? (
            <span>{t('loading')}</span>
          ) : result ? (
            <span>
              {t('foundCount', { count: result.total })}
              {result.filters_applied.fee_threshold ? (
                <span className="ml-2 text-xs text-gray-400">
                  {t('threshold')}: {fmtNum(result.filters_applied.fee_threshold)}
                </span>
              ) : null}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => {
            setExportError('');
            setExportModalOpen(true);
          }}
          disabled={!result || result.total === 0 || exportCatalog.length === 0}
          className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
        >
          {t('exportCsv')}
        </button>
      </div>

      <CohortExportModal
        open={exportModalOpen}
        catalog={exportCatalog}
        defaultOptional={defaultExportOptional}
        selected={exportSelected}
        onChangeSelected={setExportSelected}
        onClose={() => !exporting && setExportModalOpen(false)}
        onConfirm={() => void runExport()}
        exporting={exporting}
        error={exportError}
      />

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 text-sm rounded">{error}</div>
      )}

      {/* ── Results table ── */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('site')}>
                  {t('col.site')}{sortIcon('site')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('account_number')}>
                  {t('col.account')}{sortIcon('account_number')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('name')}>
                  {t('col.name')}{sortIcon('name')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('phone')}>
                  {t('col.phone')}{sortIcon('phone')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('customer_type')}>
                  {t('col.type')}{sortIcon('customer_type')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('cohort_status')}>
                  {t('col.status')}{sortIcon('cohort_status')}
                </th>
                <th className="px-3 py-2 font-medium text-right" title={t('col.connectionFeeHint')}>
                  {t('col.connectionFee')}
                </th>
                <th className="px-3 py-2 font-medium text-right" title={t('col.readyboardFeeHint')}>
                  {t('col.readyboardFee')}
                </th>
                <th className="px-3 py-2 font-medium text-right" title={t('col.feeRepaymentViaElectricityHint')}>
                  {t('col.feeRepaymentViaElectricity')}
                </th>
                <th className="px-3 py-2 font-medium text-right" title={t('col.electricityKwhHint')}>
                  {t('col.electricityKwh')}
                </th>
                <th className="px-3 py-2 font-medium text-right cursor-pointer select-none" onClick={() => handleSort('total_paid')} title={t('col.totalPaidHint')}>
                  {t('col.totalPaid')}{sortIcon('total_paid')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('first_fee_payment_date')}>
                  {t('col.firstFeePaymentDate')}{sortIcon('first_fee_payment_date')}
                </th>
                <th className="px-3 py-2 font-medium cursor-pointer select-none" onClick={() => handleSort('date_connected')}>
                  {t('col.connected')}{sortIcon('date_connected')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {result?.rows.map((r) => (
                <tr key={r.customer_id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium text-gray-700 whitespace-nowrap">{r.site}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {r.account_number ? (
                      <Link
                        to={`/customer-data?account=${encodeURIComponent(r.account_number)}`}
                        className="text-blue-600 hover:underline font-mono text-xs"
                      >
                        {r.account_number}
                      </Link>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <Link
                      to={`/customers/${r.customer_id}`}
                      className="text-gray-800 hover:text-blue-600 hover:underline"
                    >
                      {fullName(r)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-gray-600 whitespace-nowrap">
                    {r.phone || '—'}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="px-1.5 py-0.5 text-xs bg-gray-100 text-gray-700 rounded">{r.customer_type}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={`px-2 py-0.5 text-xs border rounded-full ${statusColor(r.cohort_status)}`}>
                      {t(`status.${r.cohort_status}`)}
                    </span>
                    {(r.cohort_status_override || r.payment_status_override) && (
                      <span
                        className="ml-1 text-[10px] text-purple-600"
                        title={t('overrideHint')}
                      >
                        ✱
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-700 whitespace-nowrap">
                    {fmtNum(r.payments_connection_fee)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-700 whitespace-nowrap">
                    {fmtNum(r.payments_readyboard_fee)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-700 whitespace-nowrap">
                    {fmtNum(r.payments_fee_repayment_via_electricity)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-700 whitespace-nowrap">
                    {fmtNum(r.payments_electricity)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-700 whitespace-nowrap">
                    {fmtNum(r.total_paid)}
                  </td>
                  <td className="px-3 py-2 text-gray-600 whitespace-nowrap">
                    {fmtDate(r.first_fee_payment_date)}
                  </td>
                  <td className="px-3 py-2 text-gray-600 whitespace-nowrap">
                    {fmtDate(r.date_service_connected)}
                  </td>
                </tr>
              ))}
              {result && result.rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={13} className="text-center py-12 text-gray-400">
                    {t('noResults')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── Pagination ── */}
        {result && result.total > 0 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50 text-sm">
            <span className="text-gray-600">
              {t('common:pagination.page', {
                page: result.page,
                pages: totalPages,
                total: result.total,
              })}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-white disabled:opacity-40"
              >
                {t('prev')}
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-white disabled:opacity-40"
              >
                {t('next')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
