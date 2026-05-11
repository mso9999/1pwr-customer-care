import { useEffect, useState, useCallback } from 'react';
import { listSmsLog, type SmsLogEntry, type SmsLogResponse } from '../lib/api';

const SMS_TYPES = ['balance', 'welcome', 'contract'];
const PAGE_SIZES = [25, 50, 100];

export default function SMSLogPage() {
  const [data, setData] = useState<SmsLogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  // Filters
  const [filterType, setFilterType] = useState('');
  const [filterSuccess, setFilterSuccess] = useState('');
  const [filterPhone, setFilterPhone] = useState('');
  const [filterAccount, setFilterAccount] = useState('');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');

  // Expanded row
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const fetch = useCallback(() => {
    setLoading(true);
    setError('');
    listSmsLog({
      page,
      per_page: perPage,
      sms_type: filterType || undefined,
      success: filterSuccess === '' ? undefined : filterSuccess === 'true',
      phone: filterPhone || undefined,
      account_number: filterAccount || undefined,
      date_from: filterDateFrom || undefined,
      date_to: filterDateTo || undefined,
    })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [page, perPage, filterType, filterSuccess, filterPhone, filterAccount, filterDateFrom, filterDateTo]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetch();
  };

  const handleClear = () => {
    setFilterType('');
    setFilterSuccess('');
    setFilterPhone('');
    setFilterAccount('');
    setFilterDateFrom('');
    setFilterDateTo('');
    setPage(1);
  };

  const totalPages = data?.pages || 1;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <h1 className="text-xl font-bold text-gray-800 mb-1">SMS Outbound Log</h1>
      <p className="text-sm text-gray-500 mb-6">
        Every outbound SMS sent through the gateway. Logged automatically — no opt-in needed.
      </p>

      {/* Filters */}
      <form onSubmit={handleApplyFilters} className="mb-6 p-4 bg-white rounded-lg border border-gray-200 shadow-sm">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3 mb-3">
          <label className="block">
            <span className="text-xs text-gray-500">Type</span>
            <select
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
            >
              <option value="">All</option>
              {SMS_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-gray-500">Status</span>
            <select
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              value={filterSuccess}
              onChange={(e) => setFilterSuccess(e.target.value)}
            >
              <option value="">All</option>
              <option value="true">Sent</option>
              <option value="false">Failed</option>
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-gray-500">Phone</span>
            <input
              type="text"
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              placeholder="Partial match..."
              value={filterPhone}
              onChange={(e) => setFilterPhone(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-500">Account</span>
            <input
              type="text"
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              placeholder="e.g. 0045MAK"
              value={filterAccount}
              onChange={(e) => setFilterAccount(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-500">From</span>
            <input
              type="date"
              className="mt-1 block w-full rounded border-gray-300 text-sm"
              value={filterDateFrom}
              onChange={(e) => setFilterDateFrom(e.target.value)}
            />
          </label>
        </div>
        <div className="flex items-center gap-2">
          <button type="submit" className="px-4 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Apply
          </button>
          <button type="button" onClick={handleClear} className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-800">
            Clear
          </button>
        </div>
      </form>

      {/* Error */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 text-sm rounded">{error}</div>
      )}

      {/* Results */}
      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : data && data.rows.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No SMS records match these filters.</div>
      ) : data ? (
        <>
          <div className="mb-2 text-sm text-gray-500">
            {data.total.toLocaleString()} records
            {data.pages > 1 && ` · page ${data.page} of ${data.pages}`}
          </div>
          <div className="overflow-x-auto bg-white rounded-lg border border-gray-200 shadow-sm">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 font-medium">Phone</th>
                  <th className="px-3 py-2 font-medium">Account</th>
                  <th className="px-3 py-2 font-medium">Trigger</th>
                  <th className="px-3 py-2 font-medium">Gateway</th>
                  <th className="px-3 py-2 font-medium">CM.com</th>
                  <th className="px-3 py-2 font-medium w-8"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row) => (
                  <SmsRow
                    key={row.id}
                    entry={row}
                    expanded={expandedId === row.id}
                    onToggle={() => setExpandedId(expandedId === row.id ? null : row.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <span>Rows:</span>
                {PAGE_SIZES.map((s) => (
                  <button
                    key={s}
                    onClick={() => { setPerPage(s); setPage(1); }}
                    className={`px-2 py-0.5 rounded ${perPage === s ? 'bg-blue-100 text-blue-700 font-medium' : 'hover:bg-gray-100'}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                  className="px-3 py-1 text-sm rounded border border-gray-300 disabled:opacity-30 hover:bg-gray-50"
                >
                  Prev
                </button>
                {pageButtons(page, totalPages).map((p, i) =>
                  p === '...' ? (
                    <span key={`dots-${i}`} className="px-2 text-gray-400">...</span>
                  ) : (
                    <button
                      key={p}
                      onClick={() => setPage(p as number)}
                      className={`w-8 h-8 text-sm rounded ${p === page ? 'bg-blue-600 text-white' : 'border border-gray-300 hover:bg-gray-50'}`}
                    >
                      {p}
                    </button>
                  ),
                )}
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(page + 1)}
                  className="px-3 py-1 text-sm rounded border border-gray-300 disabled:opacity-30 hover:bg-gray-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

function SmsRow({ entry, expanded, onToggle }: { entry: SmsLogEntry; expanded: boolean; onToggle: () => void }) {
  const dt = entry.sent_at ? new Date(entry.sent_at) : null;
  const timeStr = dt
    ? dt.toLocaleDateString('en-GB') + ' ' + dt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
    : '—';

  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer hover:bg-gray-50 ${expanded ? 'bg-blue-50/50' : ''}`}
      >
        <td className="px-3 py-2 whitespace-nowrap text-gray-600 font-mono text-xs">{timeStr}</td>
        <td className="px-3 py-2">
          <span className="inline-block px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-700">{entry.sms_type}</span>
        </td>
        <td className="px-3 py-2 font-mono text-xs">{entry.phone_normalized || entry.phone_raw || '—'}</td>
        <td className="px-3 py-2 font-mono text-xs">{entry.account_number || '—'}</td>
        <td className="px-3 py-2 text-xs text-gray-500">{entry.trigger_ctx || '—'}</td>
        <td className="px-3 py-2">
          {entry.success ? (
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
              Queued
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs text-red-600">
              <span className="inline-block w-2 h-2 rounded-full bg-red-400" />
              Failed
            </span>
          )}
        </td>
        <td className="px-3 py-2">
          {entry.cm_status === 'Accepted' ? (
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
              Accepted
            </span>
          ) : entry.cm_status ? (
            <span className="inline-flex items-center gap-1 text-xs text-red-600">
              <span className="inline-block w-2 h-2 rounded-full bg-red-400" />
              {entry.cm_status}
            </span>
          ) : entry.success ? (
            <span className="text-xs text-gray-400">pending</span>
          ) : (
            <span className="text-xs text-gray-300">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-gray-400 text-xs">{expanded ? '▲' : '▼'}</td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50">
          <td colSpan={8} className="px-4 py-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-gray-400">Message: </span>
                <span className="text-gray-700">{entry.message}</span>
              </div>
              <div>
                <span className="text-gray-400">Gateway: </span>
                <span className="text-gray-700 font-mono">{entry.gateway_url || '—'}</span>
              </div>
              {entry.cm_status && (
                <div>
                  <span className="text-gray-400">CM.com: </span>
                  <span className={entry.cm_status === 'Accepted' ? 'text-green-600' : 'text-red-600'}>
                    {entry.cm_status}
                    {entry.cm_error_code && entry.cm_error_code !== 0 ? ` (code ${entry.cm_error_code})` : ''}
                    {entry.cm_status_at ? ` at ${new Date(entry.cm_status_at).toLocaleString()}` : ''}
                  </span>
                </div>
              )}
              {entry.error && (
                <div className="md:col-span-2">
                  <span className="text-gray-400">Error: </span>
                  <span className="text-red-600">{entry.error}</span>
                </div>
              )}
              <div className="text-gray-400 md:col-span-2">
                ID {entry.id} · Raw phone: {entry.phone_raw || '—'}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function pageButtons(current: number, total: number): (number | string)[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages: (number | string)[] = [];
  if (current > 3) pages.push(1, '...');
  for (let i = Math.max(1, current - 1); i <= Math.min(total, current + 1); i++) {
    pages.push(i);
  }
  if (current < total - 2) pages.push('...', total);
  if (pages[0] !== 1) pages.unshift(1);
  return pages;
}
