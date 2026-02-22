import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  listRows,
  deleteRecord,
  decommissionMeter,
  getMeterHistory,
  type PaginatedResponse,
  type MeterAssignment,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

type ModalKind = 'delete' | 'decommission' | 'history' | null;

export default function MetersPage() {
  const [data, setData] = useState<PaginatedResponse | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [filterSite, setFilterSite] = useState('');
  const [filterPlatform, setFilterPlatform] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [sites, setSites] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { canWrite } = useAuth();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<ModalKind>(null);

  // Decommission form state
  const [decommReason, setDecommReason] = useState('faulty');
  const [decommReplacement, setDecommReplacement] = useState('');
  const [decommNotes, setDecommNotes] = useState('');

  // History modal state
  const [historyMeterId, setHistoryMeterId] = useState('');
  const [historyData, setHistoryData] = useState<MeterAssignment[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  useEffect(() => {
    fetch('/api/sites').then(r => r.json()).then(d => {
      setSites((d.sites || []).map((s: any) => s.concession));
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(() => {
    setLoading(true);
    const filterCol = filterSite ? 'community' : filterPlatform ? 'platform' : filterStatus ? 'status' : undefined;
    const filterVal = filterSite || filterPlatform || filterStatus || undefined;
    listRows('meters', {
      page,
      limit: 50,
      search: search || undefined,
      filter_col: filterCol,
      filter_val: filterVal,
    })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [page, search, filterSite, filterPlatform, filterStatus]);

  useEffect(fetchData, [fetchData]);
  useEffect(() => { setSelected(new Set()); }, [page, search, filterSite, filterPlatform, filterStatus]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput);
  };

  const rowIds = (data?.rows || []).map(r => String(r['meter_id'] ?? ''));
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
    setModal(null);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try { await deleteRecord('meters', id); } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    fetchData();
    if (failed) alert(`${failed} of ${ids.length} records failed to delete.`);
  };

  const handleDecommission = async () => {
    setModal(null);
    setBusy(true);
    const ids = [...selected];
    let failed = 0;
    for (const id of ids) {
      try {
        await decommissionMeter(id, {
          reason: decommReason,
          replacement_meter_id: decommReplacement || undefined,
          notes: decommNotes || undefined,
        });
      } catch { failed++; }
    }
    setSelected(new Set());
    setBusy(false);
    setDecommReason('faulty');
    setDecommReplacement('');
    setDecommNotes('');
    fetchData();
    if (failed) alert(`${failed} of ${ids.length} records failed to update.`);
  };

  const openHistory = async (meterId: string) => {
    setHistoryMeterId(meterId);
    setHistoryData([]);
    setModal('history');
    setHistoryLoading(true);
    try {
      const res = await getMeterHistory(meterId);
      setHistoryData(res.assignments || []);
    } catch {
      setHistoryData([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const platformBadge = (p: string) => {
    const colors: Record<string, string> = {
      sparkmeter: 'bg-purple-100 text-purple-700',
      koios: 'bg-sky-100 text-sky-700',
      prototype: 'bg-amber-100 text-amber-700',
    };
    return (
      <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${colors[p?.toLowerCase()] || 'bg-gray-100 text-gray-600'}`}>
        {p || '--'}
      </span>
    );
  };

  const roleBadge = (r: string) => {
    const colors: Record<string, string> = {
      primary: 'bg-green-100 text-green-700',
      check: 'bg-blue-100 text-blue-700',
      backup: 'bg-gray-100 text-gray-600',
    };
    return r ? (
      <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${colors[r?.toLowerCase()] || 'bg-gray-100 text-gray-600'}`}>
        {r}
      </span>
    ) : null;
  };

  const statusBadge = (s: string) => {
    const colors: Record<string, string> = {
      active: 'bg-green-100 text-green-700',
      maintenance: 'bg-yellow-100 text-yellow-700',
      faulty: 'bg-red-100 text-red-700',
      test: 'bg-orange-100 text-orange-700',
      decommissioned: 'bg-gray-200 text-gray-600',
      retired: 'bg-gray-200 text-gray-600',
    };
    return (
      <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${colors[s?.toLowerCase()] || 'bg-gray-100 text-gray-600'}`}>
        {s || '--'}
      </span>
    );
  };

  const fmtDate = (d: string | null) => {
    if (!d) return '--';
    try { return new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); }
    catch { return d; }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Meters</h1>
        {canWrite && (
          <Link to="/assign-meter" className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 active:bg-blue-800 transition flex items-center gap-1.5">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
            Assign Meter
          </Link>
        )}
      </div>

      {/* Filters */}
      <div className="space-y-2 sm:space-y-0 sm:flex sm:gap-3 sm:flex-wrap">
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="Search meter ID, account..."
            className="flex-1 sm:w-64 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <button type="submit" className="px-3 py-2 bg-gray-100 border rounded-lg text-sm hover:bg-gray-200 whitespace-nowrap">Search</button>
        </form>
        <select
          value={filterSite}
          onChange={e => { setFilterSite(e.target.value); setFilterPlatform(''); setFilterStatus(''); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">All Sites</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={filterPlatform}
          onChange={e => { setFilterPlatform(e.target.value); setFilterSite(''); setFilterStatus(''); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">All Platforms</option>
          {['sparkmeter', 'koios', 'prototype'].map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          value={filterStatus}
          onChange={e => { setFilterStatus(e.target.value); setFilterSite(''); setFilterPlatform(''); setPage(1); }}
          className="w-full sm:w-auto px-3 py-2 border rounded-lg text-sm bg-white"
        >
          <option value="">All Statuses</option>
          {['active', 'maintenance', 'faulty', 'test', 'decommissioned'].map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {/* Selection bar */}
      {canWrite && selected.size > 0 && (
        <div className="flex items-center justify-between bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-blue-800">
            {selected.size} meter{selected.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex gap-2">
            <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition">Clear</button>
            <button
              onClick={() => { setDecommReason('faulty'); setDecommReplacement(''); setDecommNotes(''); setModal('decommission'); }}
              disabled={busy}
              className="px-3 py-1.5 bg-amber-600 text-white text-sm font-medium rounded-lg hover:bg-amber-700 disabled:opacity-50 transition flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              Decommission
            </button>
            <button
              onClick={() => setModal('delete')}
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

      {/* Delete confirm modal */}
      {modal === 'delete' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setModal(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full mx-4 p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-gray-800">Delete Meters</h3>
            </div>
            <p className="text-sm text-gray-600 mb-6">
              Permanently delete <strong>{selected.size}</strong> meter record{selected.size !== 1 ? 's' : ''}? This cannot be undone.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setModal(null)} className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-200 transition">Cancel</button>
              <button onClick={handleDelete} className="flex-1 py-2.5 bg-red-600 text-white rounded-xl text-sm font-semibold hover:bg-red-700 transition">Delete {selected.size}</button>
            </div>
          </div>
        </div>
      )}

      {/* Decommission modal */}
      {modal === 'decommission' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setModal(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full mx-4 p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-800">Decommission Meter{selected.size > 1 ? 's' : ''}</h3>
                <p className="text-xs text-gray-500">{selected.size} meter{selected.size !== 1 ? 's' : ''} selected</p>
              </div>
            </div>

            <div className="space-y-4 mb-6">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reason</label>
                <select
                  value={decommReason}
                  onChange={e => setDecommReason(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm bg-white"
                >
                  <option value="faulty">Faulty</option>
                  <option value="test">Test Meter</option>
                  <option value="decommissioned">Decommissioned</option>
                  <option value="retired">Retired</option>
                </select>
              </div>
              {selected.size === 1 && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Replacement Meter ID (optional)</label>
                  <input
                    value={decommReplacement}
                    onChange={e => setDecommReplacement(e.target.value)}
                    placeholder="e.g. SMRSD-04-00035EDB"
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                  />
                  <p className="text-xs text-gray-400 mt-1">The replacement will inherit this meter's account assignment</p>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Notes (optional)</label>
                <textarea
                  value={decommNotes}
                  onChange={e => setDecommNotes(e.target.value)}
                  rows={2}
                  placeholder="Additional context..."
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none resize-none"
                />
              </div>
            </div>

            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6">
              <p className="text-xs text-blue-700">
                <strong>Consumption data is preserved.</strong> Historical readings from
                {selected.size === 1 ? ' this meter' : ' these meters'} remain associated
                with the customer account. The meter status will be updated and the assignment
                history recorded.
              </p>
            </div>

            <div className="flex gap-3">
              <button onClick={() => setModal(null)} className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-200 transition">Cancel</button>
              <button onClick={handleDecommission} className="flex-1 py-2.5 bg-amber-600 text-white rounded-xl text-sm font-semibold hover:bg-amber-700 transition">
                Decommission {selected.size}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* History modal */}
      {modal === 'history' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setModal(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full mx-4 p-6 max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-800">Assignment History</h3>
                <p className="text-sm text-gray-500 font-mono">{historyMeterId}</p>
              </div>
              <button onClick={() => setModal(null)} className="p-1 text-gray-400 hover:text-gray-600">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {historyLoading ? (
              <div className="py-8 text-center text-gray-400">Loading...</div>
            ) : historyData.length === 0 ? (
              <div className="py-8 text-center text-gray-400">No assignment history found</div>
            ) : (
              <div className="space-y-3">
                {historyData.map((a, i) => (
                  <div key={a.id || i} className={`border rounded-lg p-3 ${a.removed_at ? 'bg-gray-50 border-gray-200' : 'bg-green-50 border-green-200'}`}>
                    <div className="flex items-center justify-between mb-1">
                      <Link to={`/customer-data?account=${a.account_number}`} className="text-sm font-medium text-blue-600 hover:underline">
                        {a.account_number}
                      </Link>
                      {a.removed_at ? (
                        <span className="px-2 py-0.5 text-xs rounded-full bg-gray-200 text-gray-600">
                          {a.removal_reason || 'removed'}
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 text-xs rounded-full bg-green-200 text-green-700">active</span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500 space-y-0.5">
                      <p>Assigned: {fmtDate(a.assigned_at)}</p>
                      {a.removed_at && <p>Removed: {fmtDate(a.removed_at)}</p>}
                      {a.replaced_by && <p>Replaced by: <span className="font-mono">{a.replaced_by}</span></p>}
                      {a.notes && <p className="text-gray-400 italic">{a.notes}</p>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Content */}
      {loading || busy ? (
        <div className="text-center py-8 text-gray-400">{busy ? 'Processing...' : 'Loading...'}</div>
      ) : !data || data.rows.length === 0 ? (
        <div className="text-center py-8 text-gray-400">No meters found</div>
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
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Meter ID</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Account</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Customer</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Site</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Platform</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Role</th>
                  <th className="px-4 py-3 text-left font-medium text-gray-600">Status</th>
                  <th className="px-4 py-3 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((row, i) => {
                  const mid = String(row['meter_id'] || '');
                  const acct = String(row['account_number'] || '');
                  const cid = String(row['customer_id_legacy'] || '');
                  const site = String(row['community'] || '');
                  const platform = String(row['platform'] || '');
                  const role = String(row['role'] || '');
                  const status = String(row['status'] || '');
                  const isSelected = selected.has(mid);
                  return (
                    <tr key={i} className={`hover:bg-gray-50 ${isSelected ? 'bg-blue-50' : ''}`}>
                      {canWrite && (
                        <td className="px-3 py-2">
                          <input type="checkbox" checked={isSelected} onChange={() => toggleOne(mid)} className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                        </td>
                      )}
                      <td className="px-4 py-2 font-mono text-sm font-medium text-gray-800">{mid}</td>
                      <td className="px-4 py-2">
                        {acct ? <Link to={`/customer-data?account=${acct}`} className="text-blue-600 hover:underline">{acct}</Link> : <span className="text-gray-400">--</span>}
                      </td>
                      <td className="px-4 py-2">
                        {cid ? <Link to={`/customers/${cid}`} className="text-blue-600 hover:underline">{cid}</Link> : <span className="text-gray-400">--</span>}
                      </td>
                      <td className="px-4 py-2">{site}</td>
                      <td className="px-4 py-2">{platformBadge(platform)}</td>
                      <td className="px-4 py-2">{roleBadge(role)}</td>
                      <td className="px-4 py-2">{statusBadge(status)}</td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => openHistory(mid)}
                          className="p-1 text-gray-400 hover:text-blue-600 rounded transition"
                          title="View assignment history"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        </button>
                      </td>
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
              const mid = String(row['meter_id'] || '');
              const acct = String(row['account_number'] || '');
              const cid = String(row['customer_id_legacy'] || '');
              const site = String(row['community'] || '');
              const platform = String(row['platform'] || '');
              const role = String(row['role'] || '');
              const status = String(row['status'] || '');
              const isSelected = selected.has(mid);
              return (
                <div key={i} className={`bg-white rounded-lg shadow p-4 ${isSelected ? 'ring-2 ring-blue-400' : ''}`}>
                  <div className="flex items-start gap-3">
                    {canWrite && (
                      <input type="checkbox" checked={isSelected} onChange={() => toggleOne(mid)} className="w-4 h-4 mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between">
                        <div className="min-w-0">
                          <p className="font-mono text-sm font-medium text-gray-800 truncate">{mid}</p>
                          <div className="flex gap-2 mt-1 flex-wrap">
                            {platformBadge(platform)}
                            {roleBadge(role)}
                            {statusBadge(status)}
                          </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          <span className="text-xs text-gray-400">{site}</span>
                          <button
                            onClick={() => openHistory(mid)}
                            className="p-1 text-gray-400 hover:text-blue-600 rounded transition"
                            title="History"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                          </button>
                        </div>
                      </div>
                      <div className="mt-2 text-xs text-gray-500 space-y-0.5">
                        {acct && <p>Account: <Link to={`/customer-data?account=${acct}`} className="text-blue-600 hover:underline">{acct}</Link></p>}
                        {cid && <p>Customer: <Link to={`/customers/${cid}`} className="text-blue-600 hover:underline">#{cid}</Link></p>}
                      </div>
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
