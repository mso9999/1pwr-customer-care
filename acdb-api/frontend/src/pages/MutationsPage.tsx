import { useEffect, useState, useCallback } from 'react';
import { listMutations, getMutation, revertMutation } from '../lib/api';
import type { Mutation } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

// Badge color per action type
function actionBadge(action: string) {
  const colors: Record<string, string> = {
    create: 'bg-green-100 text-green-800',
    update: 'bg-yellow-100 text-yellow-800',
    delete: 'bg-red-100 text-red-800',
    revert_create: 'bg-purple-100 text-purple-800',
    revert_update: 'bg-purple-100 text-purple-800',
    revert_delete: 'bg-purple-100 text-purple-800',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[action] || 'bg-gray-100 text-gray-700'}`}>
      {action}
    </span>
  );
}

// Diff viewer for old vs new values
function DiffView({ oldVals, newVals }: { oldVals: Record<string, unknown> | null; newVals: Record<string, unknown> | null }) {
  const allKeys = Array.from(new Set([...Object.keys(oldVals || {}), ...Object.keys(newVals || {})]));
  if (allKeys.length === 0) return <p className="text-sm text-gray-400 italic">No data</p>;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b bg-gray-50">
            <th className="px-3 py-2 text-left font-medium text-gray-600">Field</th>
            <th className="px-3 py-2 text-left font-medium text-gray-600">Old Value</th>
            <th className="px-3 py-2 text-left font-medium text-gray-600">New Value</th>
          </tr>
        </thead>
        <tbody>
          {allKeys.map((key) => {
            const ov = oldVals?.[key];
            const nv = newVals?.[key];
            const changed = JSON.stringify(ov) !== JSON.stringify(nv);
            return (
              <tr key={key} className={changed ? 'bg-amber-50' : ''}>
                <td className="px-3 py-1.5 font-mono text-xs text-gray-700 whitespace-nowrap">{key}</td>
                <td className={`px-3 py-1.5 text-xs break-all ${changed && ov !== undefined ? 'text-red-700 line-through' : 'text-gray-500'}`}>
                  {ov !== undefined && ov !== null ? String(ov) : <span className="italic text-gray-300">null</span>}
                </td>
                <td className={`px-3 py-1.5 text-xs break-all ${changed && nv !== undefined ? 'text-green-700 font-medium' : 'text-gray-500'}`}>
                  {nv !== undefined && nv !== null ? String(nv) : <span className="italic text-gray-300">null</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function MutationsPage() {
  const { user } = useAuth();
  const canRevert = user?.role === 'superadmin' || user?.role === 'onm_team';

  const [mutations, setMutations] = useState<Mutation[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Filters
  const [tableFilter, setTableFilter] = useState('');
  const [userFilter, setUserFilter] = useState('');
  const [actionFilter, setActionFilter] = useState('');

  // Detail panel
  const [selectedMutation, setSelectedMutation] = useState<Mutation | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reverting, setReverting] = useState(false);

  const fetchMutations = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await listMutations({
        page,
        limit: 50,
        table: tableFilter || undefined,
        user: userFilter || undefined,
        action: actionFilter || undefined,
      });
      setMutations(data.mutations);
      setTotal(data.total);
      setPages(data.pages);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page, tableFilter, userFilter, actionFilter]);

  useEffect(() => {
    fetchMutations();
  }, [fetchMutations]);

  const handleRowClick = async (m: Mutation) => {
    if (selectedMutation?.id === m.id) {
      setSelectedMutation(null);
      return;
    }
    setDetailLoading(true);
    try {
      const detail = await getMutation(m.id);
      setSelectedMutation(detail);
    } catch {
      setSelectedMutation(m);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleRevert = async (id: number) => {
    if (!confirm('Are you sure you want to revert this mutation? This will modify the database.')) return;
    setReverting(true);
    try {
      await revertMutation(id);
      setSelectedMutation(null);
      fetchMutations();
    } catch (e: any) {
      alert(`Revert failed: ${e.message}`);
    } finally {
      setReverting(false);
    }
  };

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Mutation Log</h1>
        <span className="text-sm text-gray-500">{total} total mutations</span>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <input
          type="text"
          placeholder="Filter by table..."
          value={tableFilter}
          onChange={(e) => { setTableFilter(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm flex-1 focus:ring-2 focus:ring-blue-300 focus:outline-none"
        />
        <input
          type="text"
          placeholder="Filter by user..."
          value={userFilter}
          onChange={(e) => { setUserFilter(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm flex-1 focus:ring-2 focus:ring-blue-300 focus:outline-none"
        />
        <select
          value={actionFilter}
          onChange={(e) => { setActionFilter(e.target.value); setPage(1); }}
          className="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
        >
          <option value="">All actions</option>
          <option value="create">Create</option>
          <option value="update">Update</option>
          <option value="delete">Delete</option>
        </select>
      </div>

      {error && <div className="bg-red-50 text-red-700 px-4 py-2 rounded-lg text-sm mb-4">{error}</div>}

      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : mutations.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No mutations recorded yet</div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-xl shadow overflow-hidden">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">#</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Timestamp</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Table</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Record</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {mutations.map((m) => (
                  <tr
                    key={m.id}
                    onClick={() => handleRowClick(m)}
                    className={`cursor-pointer transition hover:bg-blue-50 ${
                      selectedMutation?.id === m.id ? 'bg-blue-50 ring-1 ring-blue-200' : ''
                    } ${m.reverted ? 'opacity-60' : ''}`}
                  >
                    <td className="px-4 py-3 font-mono text-gray-400">{m.id}</td>
                    <td className="px-4 py-3 text-gray-700 whitespace-nowrap">{new Date(m.timestamp + 'Z').toLocaleString()}</td>
                    <td className="px-4 py-3 text-gray-700">
                      <span className="font-medium">{m.user_name || m.user_id}</span>
                      <span className="text-xs text-gray-400 ml-1">({m.user_type})</span>
                    </td>
                    <td className="px-4 py-3">{actionBadge(m.action)}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{m.table_name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{m.record_id}</td>
                    <td className="px-4 py-3">
                      {m.reverted ? (
                        <span className="text-xs text-purple-600 font-medium">Reverted</span>
                      ) : (
                        <span className="text-xs text-gray-400">Active</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {mutations.map((m) => (
              <div
                key={m.id}
                onClick={() => handleRowClick(m)}
                className={`bg-white rounded-xl shadow p-4 cursor-pointer transition ${
                  selectedMutation?.id === m.id ? 'ring-2 ring-blue-300' : ''
                } ${m.reverted ? 'opacity-60' : ''}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-400 font-mono">#{m.id}</span>
                  <div className="flex items-center gap-2">
                    {actionBadge(m.action)}
                    {m.reverted && <span className="text-xs text-purple-600 font-medium">Reverted</span>}
                  </div>
                </div>
                <div className="text-sm text-gray-700 font-medium">{m.table_name} &middot; {m.record_id}</div>
                <div className="text-xs text-gray-500 mt-1">
                  {m.user_name || m.user_id} &middot; {new Date(m.timestamp + 'Z').toLocaleString()}
                </div>
              </div>
            ))}
          </div>

          {/* Detail panel */}
          {selectedMutation && (
            <div className="mt-4 bg-white rounded-xl shadow-lg border border-blue-200 p-4 sm:p-6">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
                <h2 className="text-lg font-bold text-gray-800">
                  Mutation #{selectedMutation.id}
                  <span className="ml-2">{actionBadge(selectedMutation.action)}</span>
                </h2>
                <div className="flex items-center gap-2">
                  {canRevert && !selectedMutation.reverted && (
                    <button
                      onClick={() => handleRevert(selectedMutation.id)}
                      disabled={reverting}
                      className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 disabled:opacity-50"
                    >
                      {reverting ? 'Reverting...' : 'Revert'}
                    </button>
                  )}
                  <button
                    onClick={() => setSelectedMutation(null)}
                    className="px-3 py-2 text-sm text-gray-500 hover:text-gray-800 border rounded-lg"
                  >
                    Close
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-4">
                <div>
                  <span className="text-gray-400 text-xs">Table</span>
                  <p className="font-mono text-gray-700">{selectedMutation.table_name}</p>
                </div>
                <div>
                  <span className="text-gray-400 text-xs">Record ID</span>
                  <p className="font-mono text-gray-700">{selectedMutation.record_id}</p>
                </div>
                <div>
                  <span className="text-gray-400 text-xs">User</span>
                  <p className="text-gray-700">{selectedMutation.user_name || selectedMutation.user_id}</p>
                </div>
                <div>
                  <span className="text-gray-400 text-xs">Time</span>
                  <p className="text-gray-700">{new Date(selectedMutation.timestamp + 'Z').toLocaleString()}</p>
                </div>
              </div>

              {selectedMutation.reverted ? (
                <div className="bg-purple-50 border border-purple-200 rounded-lg px-4 py-2 text-sm text-purple-700 mb-4">
                  Reverted by {selectedMutation.reverted_by} at {selectedMutation.reverted_at ? new Date(selectedMutation.reverted_at + 'Z').toLocaleString() : 'unknown'}
                </div>
              ) : null}

              {detailLoading ? (
                <div className="text-center py-8 text-gray-400">Loading details...</div>
              ) : (
                <DiffView
                  oldVals={selectedMutation.old_values || null}
                  newVals={selectedMutation.new_values || null}
                />
              )}
            </div>
          )}

          {/* Pagination */}
          {pages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-6">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 border rounded-lg text-sm disabled:opacity-40"
              >
                Prev
              </button>
              <span className="text-sm text-gray-600">
                Page {page} of {pages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(pages, p + 1))}
                disabled={page === pages}
                className="px-3 py-1.5 border rounded-lg text-sm disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
