import { useState, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, Legend,
} from 'recharts';
import { getCustomerData, createRecord, updateRecord, deleteRecord, type CustomerDataResponse, type Transaction, type HourlyPoint } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function Stat({ label, value, sub, color = 'blue' }: { label: string; value: string; sub?: string; color?: string }) {
  const ring = { blue: 'ring-blue-100', green: 'ring-green-100', amber: 'ring-amber-100', red: 'ring-red-100' }[color] || 'ring-blue-100';
  const text = { blue: 'text-blue-700', green: 'text-green-700', amber: 'text-amber-700', red: 'text-red-700' }[color] || 'text-blue-700';
  return (
    <div className={`bg-white rounded-xl border p-4 ring-1 ${ring}`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${text}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Time formatting
// ---------------------------------------------------------------------------

function fmtRecharge(seconds: number): string {
  if (seconds <= 0) return '--';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${d}d ${h}h ${m}m`;
}

// ---------------------------------------------------------------------------
// Transaction table name in ACCDB
// ---------------------------------------------------------------------------

const TXN_TABLE = 'tblaccounthistory1';

// ---------------------------------------------------------------------------
// Transaction form modal
// ---------------------------------------------------------------------------

interface TxnFormProps {
  initial?: Transaction | null;
  accountNumber: string;
  meterId: string;
  defaultRate?: string;
  onSave: () => void;
  onCancel: () => void;
}

function TransactionFormModal({ initial, accountNumber, meterId, defaultRate, onSave, onCancel }: TxnFormProps) {
  const isEdit = !!initial;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const [txnDate, setTxnDate] = useState(
    initial?.date ? initial.date.slice(0, 16) : new Date().toISOString().slice(0, 16)
  );
  const [amount, setAmount] = useState(initial?.amount_lsl?.toString() || '');
  const [kwh, setKwh] = useState(initial?.kwh?.toString() || '');
  const [rate, setRate] = useState(initial?.rate?.toString() || defaultRate || '5.00');
  const [isPayment, setIsPayment] = useState(initial?.is_payment ?? true);
  const [balance, setBalance] = useState(initial?.balance?.toString() || '');

  const handleSubmit = async () => {
    if (!amount.trim() && !kwh.trim()) { setError('Amount or kWh is required'); return; }
    setSaving(true);
    setError('');

    const data: Record<string, unknown> = {
      'accountnumber': accountNumber,
      'meterid': meterId,
      'transaction date': txnDate.replace('T', ' '),
      'transaction amount': parseFloat(amount || '0'),
      'kwh value': parseFloat(kwh || '0'),
      'rate used': parseFloat(rate || '0'),
      'payment': isPayment ? 1 : 0,
    };
    if (balance.trim()) data['current balance'] = parseFloat(balance);

    try {
      if (isEdit && initial) {
        await updateRecord(TXN_TABLE, String(initial.id), data);
      } else {
        await createRecord(TXN_TABLE, data);
      }
      onSave();
    } catch (e: any) {
      setError(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-5 sm:p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-gray-800">{isEdit ? 'Edit Transaction' : 'Add Transaction'}</h3>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Date / Time</label>
          <input type="datetime-local" value={txnDate} onChange={e => setTxnDate(e.target.value)}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
        </div>

        <div className="flex gap-3">
          <label className={`flex-1 flex items-center gap-2 px-4 py-3 border-2 rounded-xl cursor-pointer transition ${
            isPayment ? 'border-green-400 bg-green-50' : 'border-gray-200 hover:border-gray-300'
          }`}>
            <input type="radio" checked={isPayment} onChange={() => setIsPayment(true)} className="sr-only" />
            <span className={`text-sm font-medium ${isPayment ? 'text-green-700' : 'text-gray-500'}`}>Payment</span>
          </label>
          <label className={`flex-1 flex items-center gap-2 px-4 py-3 border-2 rounded-xl cursor-pointer transition ${
            !isPayment ? 'border-amber-400 bg-amber-50' : 'border-gray-200 hover:border-gray-300'
          }`}>
            <input type="radio" checked={!isPayment} onChange={() => setIsPayment(false)} className="sr-only" />
            <span className={`text-sm font-medium ${!isPayment ? 'text-amber-700' : 'text-gray-500'}`}>Consumption</span>
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Amount (LSL)</label>
            <input type="number" step="0.01" value={amount} onChange={e => setAmount(e.target.value)}
              className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">kWh</label>
            <input type="number" step="0.01" value={kwh} onChange={e => setKwh(e.target.value)}
              className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Rate (LSL/kWh)</label>
            <input type="number" step="0.01" value={rate} onChange={e => setRate(e.target.value)}
              className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Balance (optional)</label>
            <input type="number" step="0.1" value={balance} onChange={e => setBalance(e.target.value)} placeholder="Auto"
              className="w-full px-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none" />
          </div>
        </div>

        {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded-lg">{error}</p>}

        <div className="flex gap-3 pt-2">
          <button onClick={onCancel}
            className="flex-1 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={saving}
            className="flex-1 py-3 bg-blue-600 text-white rounded-xl font-semibold text-sm hover:bg-blue-700 disabled:opacity-50 transition">
            {saving ? 'Saving...' : isEdit ? 'Update' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function CustomerDataPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { canWrite } = useAuth();
  const paramAcct = searchParams.get('account') || '';

  const [account, setAccount] = useState(paramAcct);
  const [inputVal, setInputVal] = useState(paramAcct);
  const [data, setData] = useState<CustomerDataResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'transactions' | '24h' | '7d' | '30d' | '12m'>('transactions');

  // Transaction CRUD state
  const [showForm, setShowForm] = useState(false);
  const [editingTxn, setEditingTxn] = useState<Transaction | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<Transaction | null>(null);
  const [crudError, setCrudError] = useState('');
  const [crudSuccess, setCrudSuccess] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  // Fetch data when account changes (or after CRUD refresh)
  useEffect(() => {
    if (!account) return;
    setLoading(true);
    setError('');
    getCustomerData(account)
      .then(setData)
      .catch(e => setError(e.message || 'Failed to load customer data'))
      .finally(() => setLoading(false));
  }, [account, refreshKey]);

  // CRUD helpers
  const canEditTxns = canWrite; // write_customers OR write_transactions
  const meterId = data?.meter?.meterid || '';

  const handleTxnSaved = useCallback(() => {
    setShowForm(false);
    setEditingTxn(null);
    setCrudSuccess(editingTxn ? 'Transaction updated' : 'Transaction created');
    setRefreshKey(k => k + 1);
    setTimeout(() => setCrudSuccess(''), 4000);
  }, [editingTxn]);

  const handleDelete = useCallback(async (txn: Transaction) => {
    setCrudError('');
    try {
      await deleteRecord(TXN_TABLE, String(txn.id));
      setDeleteConfirm(null);
      setCrudSuccess('Transaction deleted');
      setRefreshKey(k => k + 1);
      setTimeout(() => setCrudSuccess(''), 4000);
    } catch (e: any) {
      setCrudError(e.message || 'Delete failed');
    }
  }, []);

  // Trigger lookup
  const doLookup = () => {
    const val = inputVal.trim().toUpperCase();
    if (!val) return;
    setAccount(val);
    setSearchParams({ account: val });
  };

  // Transaction table sorting
  const [sortCol, setSortCol] = useState<'date' | 'amount_lsl' | 'kwh' | 'balance'>('date');
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    if (!data) return [];
    const txns = [...data.transactions];
    txns.sort((a, b) => {
      let va: number, vb: number;
      if (sortCol === 'date') {
        va = a.date ? new Date(a.date).getTime() : 0;
        vb = b.date ? new Date(b.date).getTime() : 0;
      } else {
        va = (a as any)[sortCol] ?? 0;
        vb = (b as any)[sortCol] ?? 0;
      }
      return sortAsc ? va - vb : vb - va;
    });
    return txns;
  }, [data, sortCol, sortAsc]);

  const toggleSort = (col: typeof sortCol) => {
    if (sortCol === col) setSortAsc(!sortAsc);
    else { setSortCol(col); setSortAsc(false); }
  };

  const sortIcon = (col: typeof sortCol) => {
    if (sortCol !== col) return <span className="text-gray-300 ml-1">&#x25B4;&#x25BE;</span>;
    return <span className="text-blue-600 ml-1">{sortAsc ? '\u25B4' : '\u25BE'}</span>;
  };

  // Pagination
  const [page, setPage] = useState(1);
  const perPage = 25;
  const totalPages = Math.ceil(sorted.length / perPage);
  const paged = sorted.slice((page - 1) * perPage, page * perPage);

  // Reset page when account changes
  useEffect(() => { setPage(1); }, [account]);

  const d = data?.dashboard;
  const p = data?.profile;

  return (
    <div className="space-y-6">
      {/* Header + search */}
      <div className="flex flex-col sm:flex-row sm:items-end gap-4">
        <div className="flex-1">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Customer Data Lookup</h1>
          <p className="text-sm text-gray-400 mt-0.5">View transaction history, consumption, and balance for any customer account</p>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doLookup()}
            placeholder="Account # or Customer ID"
            className="px-4 py-2.5 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none w-56"
          />
          <button
            onClick={doLookup}
            disabled={loading}
            className="px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 transition"
          >
            {loading ? 'Loading...' : 'Lookup'}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>
      )}

      {!data && !loading && !error && (
        <div className="text-center py-16 text-gray-400">
          <svg className="w-16 h-16 mx-auto mb-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <p className="text-lg font-medium">Enter an account number or customer ID to get started</p>
          <p className="text-sm mt-1">e.g. 0045MAK, 0003MAS, 0005KET &mdash; or a numeric customer ID like 5974</p>
        </div>
      )}

      {loading && (
        <div className="text-center py-16">
          <span className="animate-spin inline-block w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full" />
          <p className="text-gray-400 mt-3">Loading customer data...</p>
        </div>
      )}

      {data && d && (
        <>
          {/* Customer info banner */}
          <div className="bg-white rounded-xl border p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center gap-4">
            <div className="flex-1 min-w-0">
              <div className="min-w-0">
                {p?.first_name || p?.last_name ? (
                  <>
                    <h2 className="text-lg font-semibold text-gray-800 truncate">
                      {`${p?.first_name || ''} ${p?.last_name || ''}`.trim()}
                    </h2>
                    <p className="text-sm text-gray-400 truncate mt-0.5">
                      <button
                        onClick={() => p?.customer_id && navigate(`/customers/${String(p.customer_id)}`)}
                        className="font-mono font-medium text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {account}
                      </button>
                      {p?.customer_id ? <> &middot; ID: <span className="font-mono">{String(p.customer_id)}</span></> : null}
                      {data.meter?.community ? <> &middot; Site: {String(data.meter.community)}</> : null}
                    </p>
                  </>
                ) : (
                  <>
                    <h2 className="text-lg font-semibold text-gray-800 truncate">
                      <button
                        onClick={() => p?.customer_id && navigate(`/customers/${String(p.customer_id)}`)}
                        className="text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {account}
                      </button>
                    </h2>
                    <p className="text-sm text-gray-400 truncate mt-0.5">
                      {p?.customer_id ? <>ID: <span className="font-mono">{String(p.customer_id)}</span></> : null}
                      {data.meter?.community ? <>{p?.customer_id ? ' \u00b7 ' : ''}Site: {String(data.meter.community)}</> : null}
                    </p>
                  </>
                )}
              </div>
            </div>
            {data.meter && (
              <div className="text-sm text-gray-500 shrink-0 flex flex-col items-end gap-0.5">
                <span>Meter: <span className="font-mono text-gray-700">{data.meter.meterid}</span></span>
                {data.meter.customer_type && <span>Type: <span className="font-semibold">{data.meter.customer_type}</span></span>}
                {data.meter.village && <span>{data.meter.village}</span>}
              </div>
            )}
          </div>

          {/* Tariff info banner */}
          {data.tariff && (
            <div className="flex items-center gap-2 text-xs bg-gray-50 border rounded-lg px-3 py-2">
              <span className="text-gray-500">Tariff:</span>
              <span className="font-bold text-gray-800">{data.tariff.rate_lsl} LSL/kWh</span>
              <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                data.tariff.source === 'customer' ? 'bg-purple-100 text-purple-700' :
                data.tariff.source === 'concession' ? 'bg-amber-100 text-amber-700' :
                'bg-blue-100 text-blue-700'
              }`}>
                {data.tariff.source}{data.tariff.source_key ? `: ${data.tariff.source_key}` : ''}
              </span>
            </div>
          )}

          {/* Stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Balance" value={`${d.balance_kwh.toFixed(1)} kWh`} color="green" />
            <Stat label="Avg Consumption" value={`${d.avg_kwh_per_day.toFixed(1)} kWh/day`} color="blue" />
            <Stat
              label="Est. Recharge"
              value={fmtRecharge(d.estimated_recharge_seconds)}
              color={d.estimated_recharge_seconds < 86400 * 3 ? 'red' : 'amber'}
            />
            <Stat
              label="Last Payment"
              value={d.last_payment ? `LSL ${d.last_payment.amount.toFixed(0)}` : '--'}
              sub={d.last_payment?.date || undefined}
              color="blue"
            />
          </div>

          {/* Totals */}
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Total Consumption (all time)" value={`${d.total_kwh_all_time.toFixed(0)} kWh`} color="blue" />
            <Stat label="Total Purchases (all time)" value={`LSL ${d.total_lsl_all_time.toFixed(0)}`} color="green" />
          </div>

          {/* Tabs */}
          <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
            {([
              ['transactions', `Transactions (${data.transaction_count})`],
              ['24h', 'Last 24 Hours'],
              ['7d', 'Last 7 Days'],
              ['30d', 'Last 30 Days'],
              ['12m', 'Last 12 Months'],
            ] as const).map(([key, label]) => (
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

          {/* CRUD feedback */}
          {crudSuccess && (
            <div className="p-3 bg-green-50 border border-green-200 rounded-xl text-green-700 text-sm flex items-center gap-2">
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              {crudSuccess} (logged in mutation history)
            </div>
          )}
          {crudError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{crudError}</div>
          )}

          {/* Tab content */}
          {tab === 'transactions' && (
            <div className="bg-white rounded-xl border overflow-hidden">
              {/* Add transaction button */}
              {canEditTxns && (
                <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between">
                  <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">Transaction History</span>
                  <button
                    onClick={() => { setEditingTxn(null); setShowForm(true); setCrudError(''); }}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 active:bg-blue-800 transition flex items-center gap-1.5"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Add Transaction
                  </button>
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left">
                      <th className="px-4 py-3 font-medium text-gray-500 cursor-pointer select-none" onClick={() => toggleSort('date')}>
                        Date {sortIcon('date')}
                      </th>
                      <th className="px-4 py-3 font-medium text-gray-500">Type</th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right cursor-pointer select-none" onClick={() => toggleSort('amount_lsl')}>
                        Amount (LSL) {sortIcon('amount_lsl')}
                      </th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right cursor-pointer select-none" onClick={() => toggleSort('kwh')}>
                        kWh {sortIcon('kwh')}
                      </th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right">Rate</th>
                      <th className="px-4 py-3 font-medium text-gray-500 text-right cursor-pointer select-none" onClick={() => toggleSort('balance')}>
                        Balance {sortIcon('balance')}
                      </th>
                      <th className="px-4 py-3 font-medium text-gray-500">Meter</th>
                      {canEditTxns && <th className="px-4 py-3 font-medium text-gray-500 text-right w-24">Actions</th>}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {paged.map((t: Transaction) => (
                      <tr key={t.id} className="hover:bg-gray-50 group">
                        <td className="px-4 py-2.5 text-gray-700 whitespace-nowrap">{t.date?.slice(0, 16) || '--'}</td>
                        <td className="px-4 py-2.5">
                          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                            t.is_payment ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
                          }`}>
                            {t.is_payment ? 'Payment' : 'Consumption'}
                          </span>
                        </td>
                        <td className={`px-4 py-2.5 text-right font-mono ${t.is_payment ? 'text-green-600' : 'text-gray-600'}`}>
                          {t.is_payment ? '+' : ''}{t.amount_lsl.toFixed(2)}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-gray-700">{t.kwh.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-gray-400">{t.rate.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-gray-700">{t.balance != null ? t.balance.toFixed(1) : '--'}</td>
                        <td className="px-4 py-2.5 text-gray-400 text-xs font-mono truncate max-w-[120px]">{t.meter || '--'}</td>
                        {canEditTxns && (
                          <td className="px-4 py-2.5 text-right">
                            <div className="opacity-0 group-hover:opacity-100 transition flex items-center justify-end gap-1">
                              <button
                                onClick={() => { setEditingTxn(t); setShowForm(true); setCrudError(''); }}
                                className="p-1.5 rounded-lg hover:bg-blue-50 text-blue-500 hover:text-blue-700 transition"
                                title="Edit"
                              >
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                </svg>
                              </button>
                              <button
                                onClick={() => { setDeleteConfirm(t); setCrudError(''); }}
                                className="p-1.5 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600 transition"
                                title="Delete"
                              >
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                              </button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                    {paged.length === 0 && (
                      <tr><td colSpan={canEditTxns ? 8 : 7} className="px-4 py-8 text-center text-gray-400">No transactions found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between px-4 py-3 border-t bg-gray-50">
                  <p className="text-xs text-gray-500">
                    Showing {(page - 1) * perPage + 1}-{Math.min(page * perPage, sorted.length)} of {sorted.length}
                  </p>
                  <div className="flex gap-1">
                    <button
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                      disabled={page === 1}
                      className="px-3 py-1.5 bg-white border rounded-lg text-xs disabled:opacity-40 hover:bg-gray-100"
                    >Prev</button>
                    <span className="px-3 py-1.5 text-xs text-gray-600">{page} / {totalPages}</span>
                    <button
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
                      className="px-3 py-1.5 bg-white border rounded-lg text-xs disabled:opacity-40 hover:bg-gray-100"
                    >Next</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === '24h' && (() => {
            const pts: HourlyPoint[] = d.hourly_24h ?? [];
            const sources = pts.length > 0
              ? Object.keys(pts[0]).filter(k => k !== 'hour' && k !== 'kwh')
              : [];
            const isMulti = sources.length > 1;
            const colors: Record<string, string> = { 'SparkMeter': '#3b82f6', '1Meter Prototype': '#f59e0b' };
            return (
              <div className="bg-white rounded-xl border p-5">
                <h3 className="text-sm font-semibold text-gray-700 mb-1">
                  Hourly Consumption — Last 24 Hours (kWh)
                </h3>
                {isMulti && (
                  <p className="text-xs text-gray-400 mb-3">Both meters measure the same load. Close agreement confirms accuracy.</p>
                )}
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={pts}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="hour" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(11, 16)} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip
                      labelFormatter={v => String(v).slice(5)}
                      formatter={(v, name) => [`${Number(v ?? 0).toFixed(3)} kWh`, name]}
                    />
                    {isMulti ? (
                      <>
                        <Legend />
                        {sources.map(src => (
                          <Bar key={src} dataKey={src} fill={colors[src] ?? '#6b7280'} radius={[3, 3, 0, 0]} />
                        ))}
                      </>
                    ) : (
                      <Bar dataKey={sources[0] ?? 'kwh'} radius={[4, 4, 0, 0]}>
                        {pts.map((_, i) => (
                          <Cell key={i} fill={i === pts.length - 1 ? '#3b82f6' : '#93c5fd'} />
                        ))}
                      </Bar>
                    )}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            );
          })()}

          {tab === '7d' && (
            <div className="bg-white rounded-xl border p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Daily Consumption - Last 7 Days (kWh)</h3>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={d.daily_7d}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={v => v.slice(5)} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => [`${Number(v ?? 0).toFixed(2)} kWh`, 'Consumption']} />
                  <Bar dataKey="kwh" radius={[4, 4, 0, 0]}>
                    {d.daily_7d.map((_, i) => (
                      <Cell key={i} fill={i === d.daily_7d.length - 1 ? '#3b82f6' : '#93c5fd'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {tab === '30d' && (
            <div className="bg-white rounded-xl border p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Daily Consumption - Last 30 Days (kWh)</h3>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={d.daily_30d}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} interval={4} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => [`${Number(v ?? 0).toFixed(2)} kWh`, 'Consumption']} />
                  <Line type="monotone" dataKey="kwh" stroke="#3b82f6" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {tab === '12m' && (
            <div className="bg-white rounded-xl border p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Monthly Consumption - Last 12 Months (kWh)</h3>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={d.monthly_12m}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => [`${Number(v ?? 0).toFixed(1)} kWh`, 'Consumption']} />
                  <Bar dataKey="kwh" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      {/* Transaction form modal */}
      {showForm && data && (
        <TransactionFormModal
          initial={editingTxn}
          accountNumber={account}
          meterId={meterId}
          defaultRate={data.tariff?.rate_lsl?.toString()}
          onSave={handleTxnSaved}
          onCancel={() => { setShowForm(false); setEditingTxn(null); }}
        />
      )}

      {/* Delete confirmation modal */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => setDeleteConfirm(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5 space-y-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-gray-800">Delete Transaction?</h3>
            <p className="text-sm text-gray-500">
              This will delete the {deleteConfirm.is_payment ? 'payment' : 'consumption'} of{' '}
              <span className="font-mono font-medium">LSL {deleteConfirm.amount_lsl.toFixed(2)}</span>{' '}
              from {deleteConfirm.date?.slice(0, 10) || 'unknown date'}.
              The change will be logged and can be reverted from the Mutations page.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setDeleteConfirm(null)}
                className="flex-1 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium text-sm hover:bg-gray-200 transition">
                Cancel
              </button>
              <button onClick={() => handleDelete(deleteConfirm)}
                className="flex-1 py-3 bg-red-600 text-white rounded-xl font-semibold text-sm hover:bg-red-700 transition">
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
