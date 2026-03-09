import { useState, useEffect } from 'react';
import {
  getFinancingProducts, createFinancingProduct, updateFinancingProduct,
  getFinancingAgreements, getFinancingAgreement,
  type FinancingProduct, type FinancingAgreement, type FinancingLedgerEntry,
} from '../lib/api';

// ---------------------------------------------------------------------------
// Product form modal
// ---------------------------------------------------------------------------

function ProductModal({ product, onClose, onSaved }: {
  product: FinancingProduct | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: product?.name ?? '',
    default_principal: product?.default_principal ?? 0,
    default_interest_rate: product?.default_interest_rate ?? 0,
    default_setup_fee: product?.default_setup_fee ?? 0,
    default_repayment_fraction: product?.default_repayment_fraction ?? 0.2,
    default_penalty_rate: product?.default_penalty_rate ?? 0,
    default_penalty_grace_days: product?.default_penalty_grace_days ?? 30,
    default_penalty_interval_days: product?.default_penalty_interval_days ?? 30,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      if (product) {
        await updateFinancingProduct(product.id, form);
      } else {
        await createFinancingProduct(form);
      }
      onSaved();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const field = (label: string, key: keyof typeof form, type = 'number', step = 'any') => (
    <label className="block">
      <span className="text-sm text-gray-600">{label}</span>
      <input
        type={type}
        step={step}
        className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
        value={form[key]}
        onChange={e => setForm({ ...form, [key]: type === 'number' ? Number(e.target.value) : e.target.value })}
      />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <h3 className="text-lg font-bold">{product ? 'Edit Product' : 'New Product Template'}</h3>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {field('Product Name', 'name', 'text')}
        <div className="grid grid-cols-2 gap-3">
          {field('Default Principal (M)', 'default_principal')}
          {field('Interest Rate (decimal)', 'default_interest_rate', 'number', '0.01')}
          {field('Setup Fee (M)', 'default_setup_fee')}
          {field('Repayment Fraction', 'default_repayment_fraction', 'number', '0.01')}
          {field('Penalty Rate (decimal)', 'default_penalty_rate', 'number', '0.01')}
          {field('Grace Days', 'default_penalty_grace_days', 'number', '1')}
          {field('Penalty Interval (days)', 'default_penalty_interval_days', 'number', '1')}
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
          <button type="submit" disabled={saving} className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {saving ? 'Saving...' : product ? 'Update' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ledger detail modal
// ---------------------------------------------------------------------------

function LedgerModal({ agreement, onClose }: { agreement: FinancingAgreement; onClose: () => void }) {
  const [detail, setDetail] = useState<FinancingAgreement | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getFinancingAgreement(agreement.id).then(d => { setDetail(d); setLoading(false); });
  }, [agreement.id]);

  const typeColors: Record<string, string> = {
    payment: 'text-green-700 bg-green-50',
    penalty: 'text-red-700 bg-red-50',
    fee: 'text-amber-700 bg-amber-50',
    adjustment: 'text-blue-700 bg-blue-50',
    writeoff: 'text-gray-700 bg-gray-100',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl p-6 max-h-[85vh] overflow-y-auto">
        <div className="flex justify-between items-start mb-4">
          <div>
            <h3 className="text-lg font-bold">Agreement #{agreement.id}</h3>
            <p className="text-sm text-gray-500">{agreement.account_number} — {agreement.description}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>

        <div className="grid grid-cols-3 gap-3 mb-4 text-sm">
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-gray-500 text-xs">Total Owed</div>
            <div className="font-bold">M {agreement.total_owed?.toFixed(2)}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-gray-500 text-xs">Outstanding</div>
            <div className="font-bold text-red-600">M {agreement.outstanding_balance?.toFixed(2)}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-gray-500 text-xs">Paid</div>
            <div className="font-bold text-green-600">M {(agreement.total_owed - agreement.outstanding_balance)?.toFixed(2)}</div>
          </div>
        </div>

        {/* Progress bar */}
        <div className="w-full bg-gray-200 rounded-full h-3 mb-4">
          <div
            className="bg-green-500 h-3 rounded-full transition-all"
            style={{ width: `${Math.min(((agreement.total_owed - agreement.outstanding_balance) / agreement.total_owed) * 100, 100)}%` }}
          />
        </div>

        <h4 className="font-semibold text-sm mb-2">Ledger</h4>
        {loading ? (
          <p className="text-sm text-gray-400">Loading...</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 text-xs border-b">
                <th className="pb-2">Date</th>
                <th className="pb-2">Type</th>
                <th className="pb-2 text-right">Amount</th>
                <th className="pb-2 text-right">Balance After</th>
                <th className="pb-2">Note</th>
              </tr>
            </thead>
            <tbody>
              {(detail?.ledger ?? []).map((e: FinancingLedgerEntry) => (
                <tr key={e.id} className="border-b border-gray-100">
                  <td className="py-1.5 text-gray-600">{new Date(e.created_at).toLocaleDateString()}</td>
                  <td className="py-1.5">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${typeColors[e.entry_type] ?? ''}`}>
                      {e.entry_type}
                    </span>
                  </td>
                  <td className={`py-1.5 text-right font-medium ${e.amount >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {e.amount >= 0 ? '+' : ''}{Number(e.amount).toFixed(2)}
                  </td>
                  <td className="py-1.5 text-right text-gray-700">M {Number(e.balance_after).toFixed(2)}</td>
                  <td className="py-1.5 text-gray-500 text-xs max-w-[200px] truncate">{e.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function FinancingPage() {
  const [tab, setTab] = useState<'products' | 'agreements'>('agreements');

  const [products, setProducts] = useState<FinancingProduct[]>([]);
  const [agreements, setAgreements] = useState<FinancingAgreement[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('active');

  const [editProduct, setEditProduct] = useState<FinancingProduct | null | undefined>(undefined);
  const [viewAgreement, setViewAgreement] = useState<FinancingAgreement | null>(null);

  const loadProducts = async () => { setProducts(await getFinancingProducts()); };
  const loadAgreements = async () => {
    const params: Record<string, string> = {};
    if (statusFilter) params.status = statusFilter;
    setAgreements(await getFinancingAgreements(params));
  };

  useEffect(() => {
    Promise.all([loadProducts(), loadAgreements()]).finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadAgreements(); }, [statusFilter]);

  const statusColors: Record<string, string> = {
    active: 'bg-blue-50 text-blue-700',
    paid_off: 'bg-green-50 text-green-700',
    defaulted: 'bg-red-50 text-red-700',
    cancelled: 'bg-gray-100 text-gray-600',
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-800">Financing Management</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b">
        {(['agreements', 'products'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'agreements' ? 'Agreements' : 'Product Templates'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
        </div>
      ) : tab === 'products' ? (
        <div>
          <div className="flex justify-end mb-3">
            <button onClick={() => setEditProduct(null)} className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700">
              + New Product
            </button>
          </div>
          <div className="bg-white rounded-xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Principal</th>
                  <th className="px-4 py-3">Interest</th>
                  <th className="px-4 py-3">Fee</th>
                  <th className="px-4 py-3">Repayment %</th>
                  <th className="px-4 py-3">Penalty</th>
                  <th className="px-4 py-3">Active</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {products.map(p => (
                  <tr key={p.id} className="border-t border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3">M {p.default_principal?.toFixed(2)}</td>
                    <td className="px-4 py-3">{(p.default_interest_rate * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3">M {p.default_setup_fee?.toFixed(2)}</td>
                    <td className="px-4 py-3">{(p.default_repayment_fraction * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3">{(p.default_penalty_rate * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${p.is_active ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                        {p.is_active ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <button onClick={() => setEditProduct(p)} className="text-blue-600 hover:underline text-xs">Edit</button>
                    </td>
                  </tr>
                ))}
                {products.length === 0 && (
                  <tr><td colSpan={8} className="text-center py-8 text-gray-400">No product templates yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div>
          <div className="flex items-center gap-3 mb-3">
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="text-sm border rounded-lg px-3 py-2"
            >
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="paid_off">Paid Off</option>
              <option value="defaulted">Defaulted</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
          <div className="bg-white rounded-xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">Account</th>
                  <th className="px-4 py-3">Product</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3 text-right">Total Owed</th>
                  <th className="px-4 py-3 text-right">Outstanding</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {agreements.map(a => (
                  <tr
                    key={a.id}
                    className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                    onClick={() => setViewAgreement(a)}
                  >
                    <td className="px-4 py-3 font-medium">#{a.id}</td>
                    <td className="px-4 py-3 font-mono text-xs">{a.account_number}</td>
                    <td className="px-4 py-3">{a.product_name ?? '—'}</td>
                    <td className="px-4 py-3 max-w-[200px] truncate">{a.description}</td>
                    <td className="px-4 py-3 text-right">M {Number(a.total_owed).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-medium text-red-600">M {Number(a.outstanding_balance).toFixed(2)}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[a.status] ?? ''}`}>
                        {a.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-500">{new Date(a.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
                {agreements.length === 0 && (
                  <tr><td colSpan={8} className="text-center py-8 text-gray-400">No agreements found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {editProduct !== undefined && (
        <ProductModal
          product={editProduct}
          onClose={() => setEditProduct(undefined)}
          onSaved={() => { setEditProduct(undefined); loadProducts(); }}
        />
      )}

      {viewAgreement && (
        <LedgerModal agreement={viewAgreement} onClose={() => setViewAgreement(null)} />
      )}
    </div>
  );
}
