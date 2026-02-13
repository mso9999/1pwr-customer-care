import { useState, useEffect, useCallback } from 'react';
import {
  getTariffCurrent, updateGlobalRate, updateConcessionRate, updateCustomerRate,
  deleteConcessionOverride, deleteCustomerOverride, getTariffHistory,
  type TariffCurrentResponse, type TariffHistoryEntry,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string) {
  if (!iso) return '--';
  return iso.slice(0, 16).replace('T', ' ');
}

function SourceBadge({ source }: { source: string }) {
  const colors: Record<string, string> = {
    global: 'bg-blue-100 text-blue-700',
    concession: 'bg-amber-100 text-amber-700',
    customer: 'bg-purple-100 text-purple-700',
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${colors[source] || 'bg-gray-100 text-gray-600'}`}>
      {source}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Rate edit modal
// ---------------------------------------------------------------------------

interface RateModalProps {
  title: string;
  currentRate?: number;
  onSave: (rate: number, effectiveFrom: string, notes: string) => Promise<void>;
  onCancel: () => void;
}

function RateModal({ title, currentRate, onSave, onCancel }: RateModalProps) {
  const [rate, setRate] = useState(currentRate?.toString() || '');
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    const r = parseFloat(rate);
    if (!r || r <= 0) { setError('Rate must be a positive number'); return; }
    setSaving(true);
    setError('');
    try {
      await onSave(r, effectiveFrom || '', notes);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-5 sm:p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-gray-800">{title}</h3>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Rate (LSL / kWh)</label>
          <input type="number" step="0.01" min="0.01" value={rate} onChange={e => setRate(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            placeholder="e.g. 5.50" autoFocus />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Effective From (optional, default: now)</label>
          <input type="datetime-local" value={effectiveFrom} onChange={e => setEffectiveFrom(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Notes (optional)</label>
          <input type="text" value={notes} onChange={e => setNotes(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            placeholder="e.g. Q2 tariff adjustment" />
        </div>

        {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded-lg">{error}</p>}

        <div className="flex gap-3 pt-2">
          <button onClick={onCancel}
            className="flex-1 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={saving}
            className="flex-1 py-3 bg-blue-600 text-white rounded-xl font-semibold text-sm hover:bg-blue-700 disabled:opacity-50 transition">
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add concession / customer modal
// ---------------------------------------------------------------------------

interface AddOverrideModalProps {
  scope: 'concession' | 'customer';
  onSave: (key: string, rate: number, effectiveFrom: string, notes: string) => Promise<void>;
  onCancel: () => void;
}

function AddOverrideModal({ scope, onSave, onCancel }: AddOverrideModalProps) {
  const [key, setKey] = useState('');
  const [rate, setRate] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    const k = key.trim().toUpperCase();
    const r = parseFloat(rate);
    if (!k) { setError(`${scope === 'concession' ? 'Concession code' : 'Customer ID'} is required`); return; }
    if (!r || r <= 0) { setError('Rate must be a positive number'); return; }
    setSaving(true);
    setError('');
    try {
      await onSave(k, r, effectiveFrom || '', notes);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-5 sm:p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-gray-800">
          Add {scope === 'concession' ? 'Concession' : 'Customer'} Override
        </h3>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">
            {scope === 'concession' ? 'Concession Code (e.g. MAK)' : 'Customer ID'}
          </label>
          <input type="text" value={key} onChange={e => setKey(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            placeholder={scope === 'concession' ? 'MAK' : '5974'} autoFocus />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Rate (LSL / kWh)</label>
          <input type="number" step="0.01" min="0.01" value={rate} onChange={e => setRate(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
            placeholder="5.50" />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Effective From (optional)</label>
          <input type="datetime-local" value={effectiveFrom} onChange={e => setEffectiveFrom(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Notes (optional)</label>
          <input type="text" value={notes} onChange={e => setNotes(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>

        {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded-lg">{error}</p>}

        <div className="flex gap-3 pt-2">
          <button onClick={onCancel}
            className="flex-1 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={saving}
            className="flex-1 py-3 bg-blue-600 text-white rounded-xl font-semibold text-sm hover:bg-blue-700 disabled:opacity-50 transition">
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function TariffManagementPage() {
  const { canWrite } = useAuth();
  const [data, setData] = useState<TariffCurrentResponse | null>(null);
  const [history, setHistory] = useState<TariffHistoryEntry[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPages, setHistoryPages] = useState(1);
  const [historyScope, setHistoryScope] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Modal state
  const [editGlobal, setEditGlobal] = useState(false);
  const [editConcession, setEditConcession] = useState<string | null>(null);
  const [editCustomer, setEditCustomer] = useState<string | null>(null);
  const [addConcession, setAddConcession] = useState(false);
  const [addCustomer, setAddCustomer] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<{ scope: string; key: string; rate: number } | null>(null);

  const [tab, setTab] = useState<'overrides' | 'history'>('overrides');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getTariffCurrent();
      setData(d);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load tariff data');
    } finally {
      setLoading(false);
    }
  }, []);

  const reloadHistory = useCallback(async () => {
    try {
      const h = await getTariffHistory({
        page: historyPage,
        limit: 25,
        scope: historyScope || undefined,
      });
      setHistory(h.history);
      setHistoryTotal(h.total);
      setHistoryPages(h.pages);
    } catch {
      /* ignore */
    }
  }, [historyPage, historyScope]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { reloadHistory(); }, [reloadHistory]);

  const showSuccess = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(''), 4000);
  };

  // Handlers
  const handleGlobalSave = async (rate: number, eff: string, notes: string) => {
    await updateGlobalRate(rate, eff || undefined, notes || undefined);
    setEditGlobal(false);
    showSuccess(`Global rate updated to ${rate} LSL/kWh`);
    reload();
    reloadHistory();
  };

  const handleConcessionSave = async (rate: number, eff: string, notes: string) => {
    if (!editConcession) return;
    await updateConcessionRate(editConcession, rate, eff || undefined, notes || undefined);
    setEditConcession(null);
    showSuccess(`${editConcession} rate updated to ${rate} LSL/kWh`);
    reload();
    reloadHistory();
  };

  const handleCustomerSave = async (rate: number, eff: string, notes: string) => {
    if (!editCustomer) return;
    await updateCustomerRate(editCustomer, rate, eff || undefined, notes || undefined);
    setEditCustomer(null);
    showSuccess(`Customer ${editCustomer} rate updated to ${rate} LSL/kWh`);
    reload();
    reloadHistory();
  };

  const handleAddConcession = async (key: string, rate: number, eff: string, notes: string) => {
    await updateConcessionRate(key, rate, eff || undefined, notes || undefined);
    setAddConcession(false);
    showSuccess(`Concession ${key} override set to ${rate} LSL/kWh`);
    reload();
    reloadHistory();
  };

  const handleAddCustomer = async (key: string, rate: number, eff: string, notes: string) => {
    await updateCustomerRate(key, rate, eff || undefined, notes || undefined);
    setAddCustomer(false);
    showSuccess(`Customer ${key} override set to ${rate} LSL/kWh`);
    reload();
    reloadHistory();
  };

  const handleDelete = async () => {
    if (!deleteConfirm) return;
    try {
      if (deleteConfirm.scope === 'concession') {
        await deleteConcessionOverride(deleteConfirm.key);
      } else {
        await deleteCustomerOverride(deleteConfirm.key);
      }
      showSuccess(`${deleteConfirm.scope} override for ${deleteConfirm.key} removed`);
      setDeleteConfirm(null);
      reload();
      reloadHistory();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Delete failed');
      setDeleteConfirm(null);
    }
  };

  if (loading && !data) {
    return (
      <div className="text-center py-16">
        <span className="animate-spin inline-block w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full" />
        <p className="text-gray-400 mt-3">Loading tariff data...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Tariff Management</h1>
        <p className="text-sm text-gray-400 mt-0.5">
          Set global, concession-level, and customer-level electricity tariffs
        </p>
      </div>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>}
      {success && (
        <div className="p-3 bg-green-50 border border-green-200 rounded-xl text-green-700 text-sm flex items-center gap-2">
          <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          {success} (logged in mutation history)
        </div>
      )}

      {/* Global Rate Card */}
      {data && (
        <div className="bg-white rounded-xl border p-5">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Global Rate</h2>
            {canWrite && (
              <button onClick={() => setEditGlobal(true)}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 transition">
                Update
              </button>
            )}
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-bold text-blue-700">{data.global_rate}</span>
            <span className="text-lg text-gray-400">LSL / kWh</span>
          </div>
          {data.pending_global && (
            <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm">
              <span className="font-medium text-amber-700">Pending:</span>{' '}
              {data.pending_global.rate_lsl} LSL/kWh effective {fmtDate(data.pending_global.effective_from)}
              {data.pending_global.notes && <span className="text-gray-500"> -- {data.pending_global.notes}</span>}
            </div>
          )}
          <p className="text-xs text-gray-400 mt-2">
            Applies to all customers unless overridden at the concession or customer level.
            Synced to tblconfig.therate in the ACCDB.
          </p>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {([['overrides', 'Rate Overrides'], ['history', 'Change History']] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition ${
              tab === key ? 'bg-white shadow-sm text-blue-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'overrides' && data && (
        <div className="space-y-6">
          {/* Concession Overrides */}
          <div className="bg-white rounded-xl border overflow-hidden">
            <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between">
              <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">Concession Overrides</span>
              {canWrite && (
                <button onClick={() => setAddConcession(true)}
                  className="px-4 py-2 bg-amber-600 text-white rounded-lg text-xs font-medium hover:bg-amber-700 transition flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Add Override
                </button>
              )}
            </div>
            {data.concession_overrides.length === 0 ? (
              <div className="px-4 py-8 text-center text-gray-400 text-sm">
                No concession overrides. All concessions use the global rate.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left">
                      <th className="px-4 py-3 font-medium text-gray-500">Concession</th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right">Rate (LSL/kWh)</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Effective From</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Set By</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Notes</th>
                      {canWrite && <th className="px-4 py-3 font-medium text-gray-500 text-right w-24">Actions</th>}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {data.concession_overrides.map(o => (
                      <tr key={o.scope_key} className="hover:bg-gray-50 group">
                        <td className="px-4 py-2.5 font-mono font-semibold text-gray-800">{o.scope_key}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-amber-700 font-semibold">
                          {o.rate_lsl}
                          {o.pending && <span className="ml-1.5 text-xs text-amber-500">(pending)</span>}
                        </td>
                        <td className="px-4 py-2.5 text-gray-500">{fmtDate(o.effective_from)}</td>
                        <td className="px-4 py-2.5 text-gray-500">{o.set_by_name || o.set_by}</td>
                        <td className="px-4 py-2.5 text-gray-400 text-xs">{o.notes || '--'}</td>
                        {canWrite && (
                          <td className="px-4 py-2.5 text-right">
                            <div className="opacity-0 group-hover:opacity-100 transition flex items-center justify-end gap-1">
                              <button onClick={() => setEditConcession(o.scope_key)}
                                className="p-1.5 rounded-lg hover:bg-blue-50 text-blue-500 hover:text-blue-700 transition" title="Edit">
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                </svg>
                              </button>
                              <button onClick={() => setDeleteConfirm({ scope: 'concession', key: o.scope_key, rate: o.rate_lsl })}
                                className="p-1.5 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600 transition" title="Remove">
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                              </button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Customer Overrides */}
          <div className="bg-white rounded-xl border overflow-hidden">
            <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between">
              <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">
                Customer Overrides ({data.customer_override_count})
              </span>
              {canWrite && (
                <button onClick={() => setAddCustomer(true)}
                  className="px-4 py-2 bg-purple-600 text-white rounded-lg text-xs font-medium hover:bg-purple-700 transition flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Add Override
                </button>
              )}
            </div>
            {data.customer_overrides.length === 0 ? (
              <div className="px-4 py-8 text-center text-gray-400 text-sm">
                No individual customer overrides.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left">
                      <th className="px-4 py-3 font-medium text-gray-500">Customer ID</th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right">Rate (LSL/kWh)</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Effective From</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Set By</th>
                      <th className="px-4 py-3 font-medium text-gray-500">Notes</th>
                      {canWrite && <th className="px-4 py-3 font-medium text-gray-500 text-right w-24">Actions</th>}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {data.customer_overrides.map(o => (
                      <tr key={o.scope_key} className="hover:bg-gray-50 group">
                        <td className="px-4 py-2.5 font-mono font-semibold text-gray-800">{o.scope_key}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-purple-700 font-semibold">
                          {o.rate_lsl}
                          {o.pending && <span className="ml-1.5 text-xs text-purple-500">(pending)</span>}
                        </td>
                        <td className="px-4 py-2.5 text-gray-500">{fmtDate(o.effective_from)}</td>
                        <td className="px-4 py-2.5 text-gray-500">{o.set_by_name || o.set_by}</td>
                        <td className="px-4 py-2.5 text-gray-400 text-xs">{o.notes || '--'}</td>
                        {canWrite && (
                          <td className="px-4 py-2.5 text-right">
                            <div className="opacity-0 group-hover:opacity-100 transition flex items-center justify-end gap-1">
                              <button onClick={() => setEditCustomer(o.scope_key)}
                                className="p-1.5 rounded-lg hover:bg-blue-50 text-blue-500 hover:text-blue-700 transition" title="Edit">
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                </svg>
                              </button>
                              <button onClick={() => setDeleteConfirm({ scope: 'customer', key: o.scope_key, rate: o.rate_lsl })}
                                className="p-1.5 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600 transition" title="Remove">
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                              </button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'history' && (
        <div className="bg-white rounded-xl border overflow-hidden">
          <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between flex-wrap gap-2">
            <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">
              Tariff Change History ({historyTotal})
            </span>
            <select value={historyScope} onChange={e => { setHistoryScope(e.target.value); setHistoryPage(1); }}
              className="px-3 py-1.5 border rounded-lg text-xs bg-white">
              <option value="">All scopes</option>
              <option value="global">Global</option>
              <option value="concession">Concession</option>
              <option value="customer">Customer</option>
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left">
                  <th className="px-4 py-3 font-medium text-gray-500">Date</th>
                  <th className="px-4 py-3 font-medium text-gray-500">Scope</th>
                  <th className="px-4 py-3 font-medium text-gray-500">Key</th>
                  <th className="px-4 py-3 font-medium text-gray-500 text-right">Previous</th>
                  <th className="px-4 py-3 font-medium text-gray-500 text-right">New Rate</th>
                  <th className="px-4 py-3 font-medium text-gray-500">Effective</th>
                  <th className="px-4 py-3 font-medium text-gray-500">By</th>
                  <th className="px-4 py-3 font-medium text-gray-500">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {history.map(h => (
                  <tr key={h.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(h.timestamp)}</td>
                    <td className="px-4 py-2.5"><SourceBadge source={h.scope} /></td>
                    <td className="px-4 py-2.5 font-mono text-gray-700">{h.scope_key || '--'}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-gray-400">
                      {h.previous_rate != null ? h.previous_rate : '--'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono font-semibold text-gray-800">
                      {h.rate_lsl === 0 ? <span className="text-red-500">removed</span> : h.rate_lsl}
                    </td>
                    <td className="px-4 py-2.5 text-gray-500">{fmtDate(h.effective_from)}</td>
                    <td className="px-4 py-2.5 text-gray-500">{h.set_by_name || h.set_by}</td>
                    <td className="px-4 py-2.5 text-gray-400 text-xs truncate max-w-[200px]">{h.notes || '--'}</td>
                  </tr>
                ))}
                {history.length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">No tariff changes recorded</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {historyPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t bg-gray-50">
              <p className="text-xs text-gray-500">Page {historyPage} of {historyPages}</p>
              <div className="flex gap-1">
                <button onClick={() => setHistoryPage(p => Math.max(1, p - 1))} disabled={historyPage === 1}
                  className="px-3 py-1.5 bg-white border rounded-lg text-xs disabled:opacity-40 hover:bg-gray-100">Prev</button>
                <button onClick={() => setHistoryPage(p => Math.min(historyPages, p + 1))} disabled={historyPage === historyPages}
                  className="px-3 py-1.5 bg-white border rounded-lg text-xs disabled:opacity-40 hover:bg-gray-100">Next</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Modals */}
      {editGlobal && data && (
        <RateModal title="Update Global Rate" currentRate={data.global_rate}
          onSave={handleGlobalSave} onCancel={() => setEditGlobal(false)} />
      )}
      {editConcession && data && (
        <RateModal
          title={`Update ${editConcession} Rate`}
          currentRate={data.concession_overrides.find(o => o.scope_key === editConcession)?.rate_lsl}
          onSave={handleConcessionSave} onCancel={() => setEditConcession(null)} />
      )}
      {editCustomer && data && (
        <RateModal
          title={`Update Customer ${editCustomer} Rate`}
          currentRate={data.customer_overrides.find(o => o.scope_key === editCustomer)?.rate_lsl}
          onSave={handleCustomerSave} onCancel={() => setEditCustomer(null)} />
      )}
      {addConcession && (
        <AddOverrideModal scope="concession" onSave={handleAddConcession} onCancel={() => setAddConcession(false)} />
      )}
      {addCustomer && (
        <AddOverrideModal scope="customer" onSave={handleAddCustomer} onCancel={() => setAddCustomer(false)} />
      )}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => setDeleteConfirm(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5 space-y-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-gray-800">Remove Override?</h3>
            <p className="text-sm text-gray-500">
              Remove the {deleteConfirm.scope} override for <span className="font-mono font-medium">{deleteConfirm.key}</span>{' '}
              (currently {deleteConfirm.rate} LSL/kWh). This will revert to the {deleteConfirm.scope === 'customer' ? 'concession or global' : 'global'} rate.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setDeleteConfirm(null)}
                className="flex-1 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">Cancel</button>
              <button onClick={handleDelete}
                className="flex-1 py-3 bg-red-600 text-white rounded-xl font-semibold text-sm hover:bg-red-700 transition">Remove</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
