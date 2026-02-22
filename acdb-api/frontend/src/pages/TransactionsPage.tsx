import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { listRows, deleteRecord, type PaginatedResponse } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

function fmtDate(d: string | null): string {
  if (!d) return '--';
  try {
    const dt = new Date(d);
    return dt.toLocaleDateString('en-ZA', { year: 'numeric', month: 'short', day: 'numeric' })
      + ' ' + dt.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' });
  } catch { return d; }
}

function fmtNum(v: unknown, decimals = 2): string {
  const n = Number(v);
  return isNaN(n) ? '--' : n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export default function TransactionsPage() {
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterPayment, setFilterPayment] = useState('');
  const [loading, setLoading] = useState(true);
  const { canWrite } = useAuth();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const fetchData = useCallback(() => {
    setLoading(true);
    listRows('transactions', {
      page,
      limit: 50,
      search: search || undefined,
      sort: 'transaction_date',
      order: 'desc',
      filter_col: filterPayment ? 'is_payment' : undefined,
      filter_val: filterPayment || undefined,
    })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [page, search, filterPayment]);

  useEffect(fetchData, [fetchData]);
  useEffect(() => { setSelected(new Set()); }, [page, search, filterPayment]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  const rowIds = (data?.rows || []).map(r => String(r['id'] ?? ''));
  const allSelected = rowIds.length > 0 && rowIds.every(id => selected.has(id));

  const toggleOne = useCallback((id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelected(prev => {
      const ids = rowIds;
      const allIn = ids.length > 0 && ids.every(id => prev.has(id));
      return allIn ? new Set() : new Set(ids);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const handleDelete = async () => {
    setShowConfirm(false);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try { await deleteRecord('transactions', id); } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    fetchData();
    if (failed) alert(`${failed} of ${ids.length} records failed to delete.`);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Transactions</h1>
      </div>

      {/* Filters */}
      <div className="space-y-2 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="Search account, meter..."
            className="flex-1 sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200 whitespace-nowrap">Search</button>
        </form>
        <select
          value={filterPayment}
          onChange={e => { setFilterPayment(e.target.value); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">All Types</option>
          <option value="true">Payments Only</option>
          <option value="false">Consumption Only</option>
        </select>
      </div>

      {/* Selection bar */}
      {canWrite && selected.size > 0 && (
        <div className="flex items-center justify-between bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-blue-800">
            {selected.size} transaction{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex gap-2">
            <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition">Clear</button>
            <button
              onClick={() => setShowConfirm(true)}
              disabled={busy}
              className="px-3 py-1.5 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete
            </button>
          </div>
        </div>
      )}

      {/* Confirm dialog */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowConfirm(false)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full mx-4 p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-gray-800">Delete Transactions</h3>
            </div>
            <p className="text-sm text-gray-600 mb-6">
              Permanently delete <strong>{selected.size}</strong> transaction{selected.size !== 1 ? 's' : ''}? This cannot be undone.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setShowConfirm(false)} className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-200 transition">Cancel</button>
              <button onClick={handleDelete} className="flex-1 py-2.5 bg-red-600 text-white rounded-xl text-sm font-semibold hover:bg-red-700 transition">Delete {selected.size}</button>
            </div>
          </div>
        </div>
      )}

      {/* Content */}
      {loading || busy ? (
        <div className="text-center py-8 text-gray-400">{busy ? 'Deleting...' : 'Loading...'}</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-8 text-gray-400">No transactions found</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {canWrite && (
                    <th className="px-3 py-3 w-10">
                      <input type="checkbox" checked={allSelected} onChange={toggleAll} className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                    </th>
                  )}
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Date</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Account</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Meter</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-600">Amount (LSL)</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-600">kWh</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-600">Rate</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Type</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-600">Balance</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((row, i) => {
                  const rid = String(row['id'] ?? '');
                  const acct = String(row['account_number'] || '');
                  const mid = String(row['meter_id'] || '');
                  const date = String(row['transaction_date'] || '');
                  const amount = row['transaction_amount'];
                  const kwh = row['kwh_value'];
                  const rate = row['rate_used'];
                  const balance = row['current_balance'];
                  const isPay = row['is_payment'] === true || row['is_payment'] === 'true' || row['is_payment'] === 1;
                  const isSelected = selected.has(rid);
                  return (
                    <tr key={i} className={`hover:bg-gray-50 ${isSelected ? 'bg-blue-50' : ''}`}>
                      {canWrite && (
                        <td className="px-3 py-2">
                          <input type="checkbox" checked={isSelected} onChange={() => toggleOne(rid)} className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                        </td>
                      )}
                      <td className="px-4 py-2 text-gray-600 whitespace-nowrap">{fmtDate(date)}</td>
                      <td className="px-4 py-2">
                        <Link to={`/customer-data?account=${acct}`} className="text-blue-600 hover:underline font-mono text-xs">{acct}</Link>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-gray-600">{mid || '--'}</td>
                      <td className={`px-4 py-2 text-right font-medium ${isPay ? 'text-green-700' : 'text-gray-800'}`}>{fmtNum(amount)}</td>
                      <td className="px-4 py-2 text-right text-gray-600">{fmtNum(kwh, 1)}</td>
                      <td className="px-4 py-2 text-right text-gray-500">{fmtNum(rate)}</td>
                      <td className="px-4 py-2">
                        <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${
                          isPay ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                        }`}>
                          {isPay ? 'Payment' : 'Vend'}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right text-gray-600">{balance != null ? fmtNum(balance) : '--'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-2">
            {canWrite && data.rows.length > 0 && (
              <button onClick={toggleAll} className="text-sm text-blue-600 font-medium px-1 py-1">
                {allSelected ? 'Deselect All' : 'Select All'}
              </button>
            )}
            {data.rows.map((row, i) => {
              const rid = String(row['id'] ?? '');
              const acct = String(row['account_number'] || '');
              const date = String(row['transaction_date'] || '');
              const amount = row['transaction_amount'];
              const kwh = row['kwh_value'];
              const isPay = row['is_payment'] === true || row['is_payment'] === 'true' || row['is_payment'] === 1;
              const isSelected = selected.has(rid);
              return (
                <div key={i} className={`bg-white rounded-lg shadow p-4 ${isSelected ? 'ring-2 ring-blue-400' : ''}`}>
                  <div className="flex items-start gap-3">
                    {canWrite && (
                      <input type="checkbox" checked={isSelected} onChange={() => toggleOne(rid)} className="w-4 h-4 mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-xs text-gray-500">{fmtDate(date)}</p>
                          <Link to={`/customer-data?account=${acct}`} className="text-sm font-mono text-blue-600 hover:underline">{acct}</Link>
                        </div>
                        <div className="text-right">
                          <p className={`text-sm font-semibold ${isPay ? 'text-green-700' : 'text-gray-800'}`}>
                            {isPay ? '+' : ''}{fmtNum(amount)} LSL
                          </p>
                          <p className="text-xs text-gray-500">{fmtNum(kwh, 1)} kWh</p>
                        </div>
                      </div>
                      <span className={`mt-1 inline-block px-2 py-0.5 text-xs rounded-full font-medium ${
                        isPay ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {isPay ? 'Payment' : 'Vend'}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span className="text-xs sm:text-sm">Page {data.page}/{data.pages} ({data.total.toLocaleString()})</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm">Prev</button>
              <button disabled={page >= data.pages} onClick={() => setPage(p => p + 1)} className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm">Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
