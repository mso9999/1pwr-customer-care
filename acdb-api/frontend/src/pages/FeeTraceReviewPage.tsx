import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  FEE_TRACE_CATEGORIES,
  getFeeTraceQueue,
  getOnboardingPipeline,
  type FeeTraceQueueRow,
} from '../lib/api';

const PAGE_SIZE = 100;

export default function FeeTraceReviewPage() {
  const { t } = useTranslation(['common']);
  const [category, setCategory] = useState<string>('listed_paid_missing_record');
  const [site, setSite] = useState('');
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<FeeTraceQueueRow[]>([]);
  const [total, setTotal] = useState(0);
  const [sites, setSites] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    getOnboardingPipeline()
      .then(res => setSites(res.sites || []))
      .catch(() => setSites([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await getFeeTraceQueue({
        category,
        site: site.trim() || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setRows(res.rows);
      setTotal(res.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load queue');
    } finally {
      setLoading(false);
    }
  }, [category, site, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const pages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);
  const page = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="max-w-7xl mx-auto p-4 sm:p-6">
      <h1 className="text-2xl font-bold text-gray-800 mb-1">{t('common:nav.feeTraceReview')}</h1>
      <p className="text-sm text-gray-600 mb-6">
        Workbook-listed fees with trace categories stored in 1PDB. Clear categories or add notes on the customer
        onboarding panel after verification.
      </p>

      <div className="flex flex-wrap gap-3 items-end mb-4">
        <label className="text-sm">
          <span className="block text-gray-600 mb-1">Category</span>
          <select
            className="border rounded px-2 py-1.5 min-w-[14rem]"
            value={category}
            onChange={e => {
              setOffset(0);
              setCategory(e.target.value);
            }}
          >
            {FEE_TRACE_CATEGORIES.map(c => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="block text-gray-600 mb-1">Site (community)</span>
          <input
            list="fee-trace-sites"
            className="border rounded px-2 py-1.5 w-40 sm:w-48"
            value={site}
            onChange={e => {
              setOffset(0);
              setSite(e.target.value);
            }}
            placeholder="All"
          />
          <datalist id="fee-trace-sites">
            {sites.map(s => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </label>
        <button
          type="button"
          className="text-sm px-3 py-1.5 rounded bg-gray-100 hover:bg-gray-200 border border-gray-300"
          onClick={() => load()}
        >
          Refresh
        </button>
      </div>

      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
      {loading && <p className="text-sm text-gray-500">{t('common:loading')}…</p>}

      {!loading && (
        <>
          <p className="text-sm text-gray-600 mb-2">
            {total} customer{total === 1 ? '' : 's'} (showing {rows.length})
          </p>
          <div className="overflow-x-auto rounded border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Account</th>
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Site</th>
                  <th className="px-3 py-2 font-medium">Conn. fee trace</th>
                  <th className="px-3 py-2 font-medium">Readyboard trace</th>
                  <th className="px-3 py-2 font-medium">Notes</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={`${r.customer_id}-${r.account_number}`} className="border-t border-gray-100 hover:bg-gray-50/80">
                    <td className="px-3 py-2 whitespace-nowrap">
                      <Link className="text-blue-700 hover:underline font-mono" to={`/customers/${encodeURIComponent(r.account_number)}`}>
                        {r.account_number}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      {[r.first_name, r.last_name].filter(Boolean).join(' ') || '—'}
                    </td>
                    <td className="px-3 py-2">{r.community || '—'}</td>
                    <td className="px-3 py-2 font-mono text-xs">{r.connection_fee_trace_category || '—'}</td>
                    <td className="px-3 py-2 font-mono text-xs">{r.readyboard_fee_trace_category || '—'}</td>
                    <td className="px-3 py-2 max-w-xs truncate text-gray-600" title={[r.connection_fee_trace_note, r.readyboard_fee_trace_note].filter(Boolean).join(' | ')}>
                      {[r.connection_fee_trace_note, r.readyboard_fee_trace_note].filter(Boolean).join(' | ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {total > PAGE_SIZE && (
            <div className="flex flex-wrap items-center gap-3 mt-4 text-sm">
              <button
                type="button"
                className="px-3 py-1 rounded border disabled:opacity-40"
                disabled={offset <= 0}
                onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))}
              >
                {t('common:pagination.prev')}
              </button>
              <span className="text-gray-600">
                {t('common:pagination.page', { page, pages, total })}
              </span>
              <button
                type="button"
                className="px-3 py-1 rounded border disabled:opacity-40"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(o => o + PAGE_SIZE)}
              >
                {t('common:pagination.next')}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
