import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { listRows, deleteRecord, listColdStorage, restoreRecord, type PaginatedResponse } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

type Tab = 'active' | 'cold';

function daysLeft(deletedAt: string): number {
  const del = new Date(deletedAt).getTime();
  const purge = del + 30 * 86_400_000;
  return Math.max(0, Math.ceil((purge - Date.now()) / 86_400_000));
}

export default function CustomersPage() {
  const [tab, setTab] = useState<Tab>('active');
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterSite, setFilterSite] = useState('');
  const [sites, setSites] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { canWrite } = useAuth();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [confirmAction, setConfirmAction] = useState<'delete' | 'restore' | null>(null);

  // Load sites once
  useEffect(() => {
    fetch('/api/sites').then(r => r.json()).then(d => {
      setSites((d.sites || []).map((s: any) => s.concession));
    }).catch(() => {});
  }, []);

  // Fetch data whenever tab, page, search, or filter changes
  const fetchData = useCallback(() => {
    setLoading(true);
    const promise = tab === 'active'
      ? listRows('customers', {
          page,
          limit: 50,
          search: search || undefined,
          filter_col: filterSite ? 'community' : undefined,
          filter_val: filterSite || undefined,
        })
      : listColdStorage('customers', { page, limit: 50 });

    promise
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [tab, page, search, filterSite]);

  useEffect(fetchData, [fetchData]);

  // Clear selection on navigation changes
  useEffect(() => { setSelected(new Set()); }, [tab, page, search, filterSite]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    setPage(1);
    setSearch('');
    setSearchInput('');
    setFilterSite('');
    setSelected(new Set());
  };

  // --- Selection helpers ---
  const rowIds = (data?.rows || []).map(r => String(r['id'] ?? r['customer_id_legacy'] ?? ''));
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

  // --- Batch actions ---
  const handleDelete = async () => {
    setConfirmAction(null);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try { await deleteRecord('customers', id); } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    fetchData();
    if (failed) alert(`${failed} of ${ids.length} records failed to move to cold storage.`);
  };

  const handleRestore = async () => {
    setConfirmAction(null);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try { await restoreRecord('customers', id); } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    fetchData();
    if (failed) alert(`${failed} of ${ids.length} records failed to restore.`);
  };

  // --- Confirmation modal ---
  const confirmModal = confirmAction && (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setConfirmAction(null)}>
      <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full mx-4 p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
            confirmAction === 'delete' ? 'bg-red-100' : 'bg-green-100'
          }`}>
            {confirmAction === 'delete' ? (
              <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            ) : (
              <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
              </svg>
            )}
          </div>
          <h3 className="text-lg font-semibold text-gray-800">
            {confirmAction === 'delete' ? 'Delete Customers' : 'Restore Customers'}
          </h3>
        </div>
        <p className="text-sm text-gray-600 mb-6">
          {confirmAction === 'delete' ? (
            <>
              Move <strong>{selected.size}</strong> customer record{selected.size !== 1 ? 's' : ''} to cold storage?
              They can be restored within 30 days before permanent deletion.
            </>
          ) : (
            <>
              Restore <strong>{selected.size}</strong> customer record{selected.size !== 1 ? 's' : ''} from cold storage?
              They will reappear in the active customer list.
            </>
          )}
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => setConfirmAction(null)}
            className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-200 transition"
          >
            Cancel
          </button>
          <button
            onClick={confirmAction === 'delete' ? handleDelete : handleRestore}
            className={`flex-1 py-2.5 text-white rounded-xl text-sm font-semibold transition ${
              confirmAction === 'delete'
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-green-600 hover:bg-green-700'
            }`}
          >
            {confirmAction === 'delete' ? `Delete ${selected.size}` : `Restore ${selected.size}`}
          </button>
        </div>
      </div>
    </div>
  );

  // --- Shared row rendering ---
  const deriveAccountNumber = (row: Record<string, any>): string => {
    const plot = String(row['plot_number'] || '').trim();
    const site = String(row['community'] || '').trim().toUpperCase();
    const m = plot.match(/^[A-Za-z]{2,4}\s+(\d{3,4})/);
    if (m && site) return `${m[1].padStart(4, '0')}${site}`;
    return '';
  };

  const renderDesktopRow = (row: Record<string, any>, i: number) => {
    const rowId = String(row['id'] ?? row['customer_id_legacy'] ?? '');
    const acct = deriveAccountNumber(row);
    const displayId = acct || String(row['customer_id_legacy'] || '');
    const name = [row['first_name'], row['last_name']].filter(Boolean).join(' ');
    const phone = String(row['phone'] || row['cell_phone_1'] || '');
    const site = String(row['community'] || '');
    const district = String(row['district'] || '');
    const terminated = row['date_service_terminated'];
    const isSelected = selected.has(rowId);
    const linkTarget = acct || String(row['customer_id_legacy'] || rowId);

    return (
      <tr key={i} className={`hover:bg-gray-50 ${isSelected ? 'bg-blue-50' : ''}`}>
        {canWrite && (
          <td className="px-3 py-2">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={() => toggleOne(rowId)}
              className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
          </td>
        )}
        <td className="px-4 py-2">
          {tab === 'active' ? (
            <Link to={`/customers/${linkTarget}`} className="text-blue-600 hover:underline font-medium font-mono">{displayId}</Link>
          ) : (
            <span className="font-medium text-gray-500 font-mono">{displayId}</span>
          )}
        </td>
        <td className="px-4 py-2">{name}</td>
        <td className="px-4 py-2 text-gray-500">{phone}</td>
        <td className="px-4 py-2">{site}</td>
        <td className="px-4 py-2 text-gray-500">{district}</td>
        <td className="px-4 py-2">
          {tab === 'cold' ? (
            <span className="px-2 py-0.5 bg-amber-100 text-amber-800 text-xs rounded-full font-medium">
              {daysLeft(row['deleted_at'])}d left
            </span>
          ) : terminated ? (
            <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs rounded-full">Terminated</span>
          ) : (
            <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full">Active</span>
          )}
        </td>
      </tr>
    );
  };

  const renderMobileCard = (row: Record<string, any>, i: number) => {
    const rowId = String(row['id'] ?? row['customer_id_legacy'] ?? '');
    const acct = deriveAccountNumber(row);
    const displayId = acct || String(row['customer_id_legacy'] || '');
    const name = [row['first_name'], row['last_name']].filter(Boolean).join(' ');
    const phone = String(row['phone'] || row['cell_phone_1'] || '');
    const site = String(row['community'] || '');
    const terminated = row['date_service_terminated'];
    const isSelected = selected.has(rowId);
    const linkTarget = acct || String(row['customer_id_legacy'] || rowId);

    const cardContent = (
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className={`font-medium text-sm font-mono ${tab === 'active' ? 'text-blue-700' : 'text-gray-500'}`}>{displayId}</p>
          <p className="text-gray-800 font-medium truncate">{name || '--'}</p>
          <p className="text-gray-500 text-sm">{phone}</p>
        </div>
        <div className="flex flex-col items-end gap-1 ml-3 shrink-0">
          {tab === 'cold' ? (
            <span className="px-2 py-0.5 bg-amber-100 text-amber-800 text-xs rounded-full font-medium">
              {daysLeft(row['deleted_at'])}d left
            </span>
          ) : terminated ? (
            <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs rounded-full">Terminated</span>
          ) : (
            <span className="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full">Active</span>
          )}
          <span className="text-xs text-gray-400">{site}</span>
        </div>
      </div>
    );

    return (
      <div key={i} className={`bg-white rounded-lg shadow p-4 ${isSelected ? 'ring-2 ring-blue-400' : ''}`}>
        <div className="flex items-start gap-3">
          {canWrite && (
            <input
              type="checkbox"
              checked={isSelected}
              onChange={() => toggleOne(rowId)}
              className="w-4 h-4 mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500 shrink-0"
            />
          )}
          {tab === 'active' ? (
            <Link to={`/customers/${linkTarget}`} className="flex-1 min-w-0 active:opacity-70">
              {cardContent}
            </Link>
          ) : (
            <div className="flex-1 min-w-0">{cardContent}</div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Customers</h1>
        {canWrite && tab === 'active' && (
          <div className="flex gap-2">
            <Link to="/assign-meter" className="px-4 py-2.5 bg-emerald-600 text-white rounded-xl text-sm font-medium hover:bg-emerald-700 active:bg-emerald-800 transition flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              Assign Meter
            </Link>
            <Link to="/commission" className="px-4 py-2.5 bg-amber-600 text-white rounded-xl text-sm font-medium hover:bg-amber-700 active:bg-amber-800 transition flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              Commission
            </Link>
            <Link to="/customers/new" className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 active:bg-blue-800 transition flex items-center gap-1.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
              Add Customer
            </Link>
          </div>
        )}
      </div>

      {/* Tab bar */}
      {canWrite && (
        <div className="flex border-b">
          <button
            onClick={() => switchTab('active')}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition ${
              tab === 'active'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Active Customers
          </button>
          <button
            onClick={() => switchTab('cold')}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition flex items-center gap-1.5 ${
              tab === 'cold'
                ? 'border-amber-600 text-amber-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
            </svg>
            Cold Storage
          </button>
        </div>
      )}

      {/* Filters (active tab only) */}
      {tab === 'active' && (
        <div className="space-y-2 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap">
          <form onSubmit={handleSearch} className="flex gap-2">
            <input
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              placeholder="Search name, ID, plot..."
              className="flex-1 sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
            />
            <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200 whitespace-nowrap">Search</button>
          </form>
          <select
            value={filterSite}
            onChange={e => { setFilterSite(e.target.value); setPage(1); }}
            className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
          >
            <option value="">All Sites</option>
            {sites.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      )}

      {/* Cold storage info banner */}
      {tab === 'cold' && !loading && data && data.total > 0 && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          <svg className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-sm text-amber-800">
            These records were deleted and will be <strong>permanently purged after 30 days</strong>.
            Select records and click <strong>Restore</strong> to move them back to the active list.
          </p>
        </div>
      )}

      {/* Selection action bar */}
      {canWrite && selected.size > 0 && (
        <div className="flex items-center justify-between bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-blue-800">
            {selected.size} record{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setSelected(new Set())}
              className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition"
            >
              Clear
            </button>
            {tab === 'active' ? (
              <button
                onClick={() => setConfirmAction('delete')}
                disabled={busy}
                className="px-3 py-1.5 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 active:bg-red-800 disabled:opacity-50 transition flex items-center gap-1.5"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
                Delete
              </button>
            ) : (
              <button
                onClick={() => setConfirmAction('restore')}
                disabled={busy}
                className="px-3 py-1.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 active:bg-green-800 disabled:opacity-50 transition flex items-center gap-1.5"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                </svg>
                Restore
              </button>
            )}
          </div>
        </div>
      )}

      {confirmModal}

      {/* Content */}
      {loading || busy ? (
        <div className="text-center py-8 text-gray-400">{busy ? (tab === 'cold' ? 'Restoring...' : 'Deleting...') : 'Loading...'}</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          {tab === 'cold' ? (
            <div className="space-y-2">
              <svg className="w-12 h-12 mx-auto text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
              </svg>
              <p>Cold storage is empty</p>
            </div>
          ) : 'No customers found'}
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {canWrite && (
                    <th className="px-3 py-3 w-10">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={toggleAll}
                        className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                    </th>
                  )}
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Account</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Name</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Phone</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Site</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">District</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">
                    {tab === 'cold' ? 'Purge In' : 'Status'}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map(renderDesktopRow)}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden space-y-2">
            {canWrite && data.rows.length > 0 && (
              <button
                onClick={toggleAll}
                className="text-sm text-blue-600 font-medium px-1 py-1"
              >
                {allSelected ? 'Deselect All' : 'Select All'}
              </button>
            )}
            {data.rows.map(renderMobileCard)}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span className="text-xs sm:text-sm">Page {data.page}/{data.pages} ({data.total.toLocaleString()})</span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm"
              >Prev</button>
              <button
                disabled={page >= data.pages}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1.5 border rounded disabled:opacity-30 hover:bg-gray-100 text-sm"
              >Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
