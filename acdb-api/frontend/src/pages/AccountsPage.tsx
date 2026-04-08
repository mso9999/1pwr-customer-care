import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { listRows, deleteRecord, type PaginatedResponse } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

type OrphanInfo = { account_number: string; reason: string };

export default function AccountsPage() {
  const { t } = useTranslation(['accounts', 'common']);
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterSite, setFilterSite] = useState('');
  const [sites, setSites] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { canWrite, canWriteCustomers, token } = useAuth();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const [orphanMap, setOrphanMap] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    fetch('/api/sites').then(r => r.json()).then(d => {
      setSites((d.sites || []).map((s: any) => s.concession));
    }).catch(() => {});
    if (token) {
      fetch('/api/tables/accounts/orphaned', { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.json())
        .then(d => {
          const m = new Map<string, string>();
          (d.orphaned || []).forEach((o: OrphanInfo) => m.set(o.account_number, o.reason));
          setOrphanMap(m);
        })
        .catch(() => {});
    }
  }, [token]);

  const fetchData = useCallback(() => {
    setLoading(true);
    listRows('accounts', {
      page,
      limit: 50,
      search: search || undefined,
      filter_col: filterSite ? 'community' : undefined,
      filter_val: filterSite || undefined,
    })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [page, search, filterSite]);

  useEffect(fetchData, [fetchData]);
  useEffect(() => { setSelected(new Set()); }, [page, search, filterSite]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  const rowIds = (data?.rows || []).map(r => String(r['account_number'] ?? ''));
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

  const refreshOrphans = useCallback(() => {
    if (!token) return;
    fetch('/api/tables/accounts/orphaned', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(d => {
        const m = new Map<string, string>();
        (d.orphaned || []).forEach((o: OrphanInfo) => m.set(o.account_number, o.reason));
        setOrphanMap(m);
      })
      .catch(() => {});
  }, [token]);

  const handleDelete = async () => {
    setShowConfirm(false);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try { await deleteRecord('accounts', id); } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    fetchData();
    refreshOrphans();
    if (failed) alert(`${failed} of ${ids.length} records failed to delete.`);
  };

  const orphanBadge = (acct: string) => {
    const reason = orphanMap.get(acct);
    if (!reason) return null;
    const label = reason === 'customer_in_cold_storage' ? t('accounts:customerDeleted') : t('accounts:noCustomerLinked');
    return (
      <span
        title={label}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-amber-100 text-amber-800 text-[10px] font-semibold rounded-full uppercase tracking-wide"
      >
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        {label}
      </span>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('accounts:title')}</h1>
        {canWriteCustomers && (
          <Link to="/assign-meter" className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 active:bg-blue-800 transition flex items-center gap-1.5">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
            {t('accounts:newAccount')}
          </Link>
        )}
      </div>

      {orphanMap.size > 0 && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <svg className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
          <div className="text-sm">
            <span className="font-semibold text-amber-800">{t('accounts:orphanAlert', { count: orphanMap.size })}</span>
            <span className="text-amber-700"> — {t('accounts:orphanExplanation')}</span>
          </div>
        </div>
      )}

      <div className="space-y-2 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder={t('accounts:searchPlaceholder')}
            className="flex-1 sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200 whitespace-nowrap">{t('accounts:search')}</button>
        </form>
        <select
          value={filterSite}
          onChange={e => { setFilterSite(e.target.value); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">{t('accounts:allSites')}</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {canWrite && selected.size > 0 && (
        <div className="flex items-center justify-between bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-blue-800">
            {t('accounts:accountsSelected', { count: selected.size })}
          </span>
          <div className="flex gap-2">
            <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition">{t('accounts:clear')}</button>
            <button
              onClick={() => setShowConfirm(true)}
              disabled={busy}
              className="px-3 py-1.5 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              {t('accounts:delete')}
            </button>
          </div>
        </div>
      )}

      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowConfirm(false)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full mx-4 p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-gray-800">{t('accounts:deleteAccounts')}</h3>
            </div>
            <p className="text-sm text-gray-600 mb-6">
              {t('accounts:deleteConfirm', { count: selected.size })}
            </p>
            <div className="flex gap-3">
              <button onClick={() => setShowConfirm(false)} className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-200 transition">{t('accounts:cancel')}</button>
              <button onClick={handleDelete} className="flex-1 py-2.5 bg-red-600 text-white rounded-xl text-sm font-semibold hover:bg-red-700 transition">{t('accounts:deleteCount', { count: selected.size })}</button>
            </div>
          </div>
        </div>
      )}

      {loading || busy ? (
        <div className="text-center py-8 text-gray-400">{busy ? t('accounts:deleting') : t('accounts:loading')}</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-8 text-gray-400">{t('accounts:noAccounts')}</div>
      ) : (
        <>
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {canWrite && (
                    <th className="px-3 py-3 w-10">
                      <input type="checkbox" checked={allSelected} onChange={toggleAll} className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                    </th>
                  )}
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('accounts:colAccountNumber')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('accounts:colCustomer')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('accounts:colMeter')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('accounts:colSite')}</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">{t('accounts:colCreatedBy')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((row, i) => {
                  const acct = String(row['account_number'] || '');
                  const cid = String(row['customer_id'] || '');
                  const mid = String(row['meter_id'] || '');
                  const site = String(row['community'] || '');
                  const createdBy = String(row['created_by'] || '');
                  const isSelected = selected.has(acct);
                  return (
                    <tr key={i} className={`hover:bg-gray-50 ${isSelected ? 'bg-blue-50' : ''}`}>
                      {canWrite && (
                        <td className="px-3 py-2">
                          <input type="checkbox" checked={isSelected} onChange={() => toggleOne(acct)} className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                        </td>
                      )}
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <Link to={`/customer-data?account=${acct}`} className="text-blue-600 hover:underline font-mono font-medium">{acct}</Link>
                          {orphanBadge(acct)}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        {cid ? <Link to={`/customers/${cid}`} className="text-blue-600 hover:underline">#{cid}</Link> : <span className="text-gray-400">--</span>}
                      </td>
                      <td className="px-4 py-2 font-mono text-gray-700">{mid || <span className="text-gray-400">--</span>}</td>
                      <td className="px-4 py-2">{site}</td>
                      <td className="px-4 py-2 text-gray-500 text-xs">{createdBy || '--'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="md:hidden space-y-2">
            {canWrite && data.rows.length > 0 && (
              <button onClick={toggleAll} className="text-sm text-blue-600 font-medium px-1 py-1">
                {allSelected ? t('accounts:deselectAll') : t('accounts:selectAll')}
              </button>
            )}
            {data.rows.map((row, i) => {
              const acct = String(row['account_number'] || '');
              const cid = String(row['customer_id'] || '');
              const mid = String(row['meter_id'] || '');
              const site = String(row['community'] || '');
              const isSelected = selected.has(acct);
              return (
                <div key={i} className={`bg-white rounded-lg shadow p-4 ${isSelected ? 'ring-2 ring-blue-400' : ''}`}>
                  <div className="flex items-start gap-3">
                    {canWrite && (
                      <input type="checkbox" checked={isSelected} onChange={() => toggleOne(acct)} className="w-4 h-4 mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Link to={`/customer-data?account=${acct}`} className="font-mono text-sm font-medium text-blue-700">{acct}</Link>
                        {orphanBadge(acct)}
                      </div>
                      <div className="mt-1.5 text-xs text-gray-500 space-y-0.5">
                        {cid && <p>{t('accounts:colCustomer')}: <Link to={`/customers/${cid}`} className="text-blue-600 hover:underline">#{cid}</Link></p>}
                        {mid && <p>{t('accounts:colMeter')}: <span className="font-mono">{mid}</span></p>}
                        <p>{t('accounts:colSite')}: {site || '--'}</p>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

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
