import { useState, useEffect } from 'react';
import { getPendingVerifications, verifyPayments, verificationExportUrl, type PaymentVerification } from '../lib/api';

export default function PaymentVerificationPage() {
  const [rows, setRows] = useState<PaymentVerification[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('pending');
  const [typeFilter, setTypeFilter] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [actionNote, setActionNote] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (statusFilter) params.status = statusFilter;
      if (typeFilter) params.payment_type = typeFilter;
      const res = await getPendingVerifications(params);
      setRows(res.verifications);
      setTotal(res.total);
      setSelected(new Set());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [statusFilter, typeFilter]);

  const toggleAll = () => {
    if (selected.size === rows.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(rows.map(r => r.id)));
    }
  };

  const toggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleAction = async (action: 'verify' | 'reject') => {
    if (selected.size === 0) return;
    setBusy(true);
    try {
      await verifyPayments(Array.from(selected), action, actionNote || undefined);
      setActionNote('');
      load();
    } finally {
      setBusy(false);
    }
  };

  const typeColors: Record<string, string> = {
    connection_fee: 'bg-purple-50 text-purple-700',
    readyboard_fee: 'bg-cyan-50 text-cyan-700',
    electricity: 'bg-blue-50 text-blue-700',
    uncategorized: 'bg-gray-100 text-gray-600',
  };

  const statusColors: Record<string, string> = {
    pending: 'bg-amber-50 text-amber-700',
    verified: 'bg-green-50 text-green-700',
    rejected: 'bg-red-50 text-red-700',
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-800 mb-6">Payment Verification</h1>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="text-sm border rounded-lg px-3 py-2">
          <option value="pending">Pending</option>
          <option value="verified">Verified</option>
          <option value="rejected">Rejected</option>
          <option value="">All</option>
        </select>
        <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} className="text-sm border rounded-lg px-3 py-2">
          <option value="">All Types</option>
          <option value="connection_fee">Connection Fee</option>
          <option value="readyboard_fee">Readyboard Fee</option>
          <option value="electricity">Electricity</option>
          <option value="uncategorized">Uncategorized</option>
        </select>
        <span className="text-sm text-gray-500">{total} record{total !== 1 ? 's' : ''}</span>
        <a
          href={verificationExportUrl(statusFilter, typeFilter || undefined)}
          className="ml-auto px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 no-underline"
          download
        >
          Export XLSX
        </a>
      </div>

      {/* Bulk actions */}
      {selected.size > 0 && statusFilter === 'pending' && (
        <div className="bg-blue-50 rounded-xl p-4 mb-4 flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-blue-800">{selected.size} selected</span>
          <input
            type="text"
            value={actionNote}
            onChange={e => setActionNote(e.target.value)}
            placeholder="Note (optional)"
            className="text-sm border rounded-lg px-3 py-1.5 flex-1 min-w-[200px]"
          />
          <button
            onClick={() => handleAction('verify')}
            disabled={busy}
            className="px-4 py-1.5 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 disabled:opacity-50"
          >
            Verify
          </button>
          <button
            onClick={() => handleAction('reject')}
            disabled={busy}
            className="px-4 py-1.5 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                {statusFilter === 'pending' && (
                  <th className="px-4 py-3 w-10">
                    <input type="checkbox" checked={selected.size === rows.length && rows.length > 0} onChange={toggleAll} />
                  </th>
                )}
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Account</th>
                <th className="px-4 py-3">Customer</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3 text-right">Amount</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-t border-gray-100 hover:bg-gray-50">
                  {statusFilter === 'pending' && (
                    <td className="px-4 py-3">
                      <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                    </td>
                  )}
                  <td className="px-4 py-3 text-gray-600">{new Date(r.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 font-mono text-xs">{r.account_number}</td>
                  <td className="px-4 py-3">{[r.first_name, r.last_name].filter(Boolean).join(' ') || '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${typeColors[r.payment_type] ?? ''}`}>
                      {r.payment_type.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right font-medium">M {Number(r.amount).toFixed(2)}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[r.status] ?? ''}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs max-w-[200px] truncate">{r.note ?? '—'}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={statusFilter === 'pending' ? 8 : 7} className="text-center py-8 text-gray-400">
                    No {statusFilter || ''} verifications found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
