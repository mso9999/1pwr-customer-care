import { useEffect, useState, useCallback } from 'react';
import {
  getAssetRegister,
  getKpis,
  getSiteCustomers,
  getSiteTransactions,
  type AssetRegisterRow,
  type KpiRow,
  type SiteCustomer,
  type SiteTransaction,
} from '../lib/api';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

const PAGE_SIZE = 100;

function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 text-gray-400 text-sm">{message}</div>
  );
}

function Pagination({ page, total, limit, onPageChange }: { page: number; total: number; limit: number; onPageChange: (p: number) => void }) {
  const totalPages = Math.ceil(total / limit);
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-between mt-3">
      <span className="text-xs text-gray-500">
        Page {page} of {totalPages} ({total} total)
      </span>
      <div className="flex gap-2">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          className="px-3 py-1.5 text-sm border rounded-md disabled:opacity-40 enabled:hover:bg-gray-50"
        >
          Prev
        </button>
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          className="px-3 py-1.5 text-sm border rounded-md disabled:opacity-40 enabled:hover:bg-gray-50"
        >
          Next
        </button>
      </div>
    </div>
  );
}

type Tab = 'asset-register' | 'kpis' | 'customers' | 'transactions';

const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#9333ea'];

export default function InvestorAnalyticsPage() {
  const [tab, setTab] = useState<Tab>('asset-register');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Asset register
  const [assets, setAssets] = useState<AssetRegisterRow[]>([]);

  // KPIs
  const [kpis, setKpis] = useState<KpiRow[]>([]);
  const [periodType, setPeriodType] = useState<'quarter' | 'month'>('quarter');
  const [kpiSite, setKpiSite] = useState<string>('');

  // Customers
  const [customers, setCustomers] = useState<SiteCustomer[]>([]);
  const [customerSite, setCustomerSite] = useState<string>('MAK');
  const [customerTypeFilter, setCustomerTypeFilter] = useState<string>('');
  const [customerTotal, setCustomerTotal] = useState(0);
  const [customerPage, setCustomerPage] = useState(1);

  // Transactions
  const [txns, setTxns] = useState<SiteTransaction[]>([]);
  const [txnSite, setTxnSite] = useState<string>('MAK');
  const [txnDateFrom, setTxnDateFrom] = useState<string>('');
  const [txnDateTo, setTxnDateTo] = useState<string>('');
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);

  const siteCodes = assets.map((a) => a.site_code).sort();

  const loadAssetRegister = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getAssetRegister();
      setAssets(data);
    } catch (e: any) {
      setError(e.message || 'Failed to load asset register');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadKpis = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getKpis({
        period: periodType,
        concession: kpiSite || undefined,
      });
      setKpis(data);
    } catch (e: any) {
      setError(e.message || 'Failed to load KPIs');
    } finally {
      setLoading(false);
    }
  }, [periodType, kpiSite]);

  const loadCustomers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getSiteCustomers(customerSite, {
        customer_type: customerTypeFilter || undefined,
        page: customerPage,
        limit: 100,
      });
      setCustomers(data.customers);
      setCustomerTotal(data.total);
    } catch (e: any) {
      setError(e.message || 'Failed to load customers');
    } finally {
      setLoading(false);
    }
  }, [customerSite, customerTypeFilter, customerPage]);

  const loadTxns = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getSiteTransactions(txnSite, {
        date_from: txnDateFrom || undefined,
        date_to: txnDateTo || undefined,
        page: txnPage,
        limit: 100,
      });
      setTxns(data.transactions);
      setTxnTotal(data.total);
    } catch (e: any) {
      setError(e.message || 'Failed to load transactions');
    } finally {
      setLoading(false);
    }
  }, [txnSite, txnDateFrom, txnDateTo, txnPage]);

  useEffect(() => {
    if (tab === 'asset-register') loadAssetRegister();
  }, [tab, loadAssetRegister]);

  useEffect(() => {
    if (tab === 'kpis') loadKpis();
  }, [tab, loadKpis]);

  useEffect(() => {
    if (tab === 'customers') loadCustomers();
  }, [tab, loadCustomers]);

  useEffect(() => {
    if (tab === 'transactions') loadTxns();
  }, [tab, loadTxns]);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'asset-register', label: 'Asset Register' },
    { key: 'kpis', label: 'KPI Time Series' },
    { key: 'customers', label: 'Site Customers' },
    { key: 'transactions', label: 'Transactions' },
  ];

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 mb-4">Investor Analytics</h1>

      {/* Tab bar */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {tabs.map((tb) => (
          <button
            key={tb.key}
            onClick={() => setTab(tb.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tb.key
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="text-red-400 hover:text-red-600 ml-2">✕</button>
        </div>
      )}
      {loading && (
        <div className="flex items-center gap-2 mb-4 text-sm text-gray-500">
          <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading…
        </div>
      )}

      {/* Asset Register Tab */}
      {tab === 'asset-register' && !loading && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <a
              href="/api/reports/quarterly_investor_report/export"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md px-3 py-1.5 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
              Export XLSX
            </a>
          </div>
          {assets.length === 0 ? (
            <EmptyState message="No site metadata found. Run the SparkMeter ETL to populate the asset register." />
          ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Site</th>
                <th className="px-3 py-2 text-left">Country</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-right">PV (kWp)</th>
                <th className="px-3 py-2 text-right">Battery (kWh)</th>
                <th className="px-3 py-2 text-right">Total Conns</th>
                <th className="px-3 py-2 text-right">Active</th>
                <th className="px-3 py-2 text-right">HH</th>
                <th className="px-3 py-2 text-right">SME</th>
                <th className="px-3 py-2 text-right">C&I</th>
                <th className="px-3 py-2 text-right">Tariff (USD/kWh)</th>
                <th className="px-3 py-2 text-right">Avail %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {assets.map((a) => (
                <tr key={a.site_code} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium">{a.site_code} — {a.full_name}</td>
                  <td className="px-3 py-2">{a.country}</td>
                  <td className="px-3 py-2">{a.status}</td>
                  <td className="px-3 py-2 text-right">{a.pv_kwp ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{a.battery_kwh ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{a.total_connections}</td>
                  <td className="px-3 py-2 text-right">{a.active_connections}</td>
                  <td className="px-3 py-2 text-right">{a.hh_count}</td>
                  <td className="px-3 py-2 text-right">{a.sme_count}</td>
                  <td className="px-3 py-2 text-right">{a.ci_count}</td>
                  <td className="px-3 py-2 text-right">{a.avg_tariff_usd_kwh ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{a.system_availability_pct?.toFixed(1) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
          )}
        </div>
      )}

      {/* KPI Time Series Tab */}
      {tab === 'kpis' && !loading && (
        <div className="space-y-6">
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Period</label>
              <select
                value={periodType}
                onChange={(e) => setPeriodType(e.target.value as 'quarter' | 'month')}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              >
                <option value="quarter">Quarterly</option>
                <option value="month">Monthly</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Site (empty = portfolio)</label>
              <select
                value={kpiSite}
                onChange={(e) => setKpiSite(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              >
                <option value="">All Sites</option>
                {siteCodes.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">Connections</h3>
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={kpis}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="total_connections" name="Total" fill={COLORS[0]} />
                  <Bar dataKey="active_connections" name="Active" fill={COLORS[2]} />
                  <Bar dataKey="new_connections" name="New" fill={COLORS[3]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">Revenue (USD)</h3>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={kpis}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="revenue_usd" name="Revenue USD" stroke={COLORS[1]} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">Energy (kWh)</h3>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={kpis}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="energy_kwh" name="Energy kWh" stroke={COLORS[2]} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">ARPU (USD/conn/month)</h3>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={kpis}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="arpu_usd_month" name="ARPU USD" stroke={COLORS[4]} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* KPI table */}
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-3 py-2 text-left">Period</th>
                  <th className="px-3 py-2 text-right">Total</th>
                  <th className="px-3 py-2 text-right">Active</th>
                  <th className="px-3 py-2 text-right">New</th>
                  <th className="px-3 py-2 text-right">Energy (kWh)</th>
                  <th className="px-3 py-2 text-right">Revenue (USD)</th>
                  <th className="px-3 py-2 text-right">ARPU</th>
                  <th className="px-3 py-2 text-right">Tariff</th>
                  <th className="px-3 py-2 text-right">Prod Use %</th>
                  <th className="px-3 py-2 text-right">Avail %</th>
                  <th className="px-3 py-2 text-right">OPEX (USD)</th>
                  <th className="px-3 py-2 text-right">EBITDA (USD)</th>
                  <th className="px-3 py-2 text-right">CAPEX (USD)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {kpis.map((k) => (
                  <tr key={k.period} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-medium">{k.period}</td>
                    <td className="px-3 py-2 text-right">{k.total_connections}</td>
                    <td className="px-3 py-2 text-right">{k.active_connections}</td>
                    <td className="px-3 py-2 text-right">{k.new_connections}</td>
                    <td className="px-3 py-2 text-right">{k.energy_kwh.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">{k.revenue_usd.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">{k.arpu_usd_month}</td>
                    <td className="px-3 py-2 text-right">{k.avg_tariff_usd_kwh}</td>
                    <td className="px-3 py-2 text-right">{(k.productive_use_share * 100).toFixed(1)}%</td>
                    <td className="px-3 py-2 text-right">{k.system_availability_pct?.toFixed(1) ?? '—'}</td>
                    <td className="px-3 py-2 text-right">{k.opex_usd?.toLocaleString() ?? '—'}</td>
                    <td className="px-3 py-2 text-right">{k.ebitda_usd?.toLocaleString() ?? '—'}</td>
                    <td className="px-3 py-2 text-right">{k.capex_cumulative_usd?.toLocaleString() ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Site Customers Tab */}
      {tab === 'customers' && !loading && (
        <div className="space-y-4">
          {siteCodes.length === 0 ? (
            <EmptyState message="Load the Asset Register tab first to populate site codes." />
          ) : (
          <>
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Site</label>
              <select
                value={customerSite}
                onChange={(e) => { setCustomerSite(e.target.value); setCustomerPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              >
                {siteCodes.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Type</label>
              <select
                value={customerTypeFilter}
                onChange={(e) => { setCustomerTypeFilter(e.target.value); setCustomerPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              >
                <option value="">All</option>
                <option value="HH">HH</option>
                <option value="SME">SME</option>
                <option value="C_I">C&I</option>
                <option value="UNK">Unknown</option>
              </select>
            </div>
            <div className="text-sm text-gray-500">{customerTotal} customers</div>
          </div>

          {customers.length === 0 ? (
            <EmptyState message="No customers found for this site and filter." />
          ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-left">Name</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-left">Tariff Plan</th>
                  <th className="px-3 py-2 text-left">Connected</th>
                  <th className="px-3 py-2 text-left">Last Txn</th>
                  <th className="px-3 py-2 text-left">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {customers.map((c) => (
                  <tr key={c.account_number} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-xs">{c.account_number}</td>
                    <td className="px-3 py-2">{c.customer_name}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                        c.customer_type === 'HH' ? 'bg-blue-100 text-blue-700' :
                        c.customer_type === 'SME' ? 'bg-amber-100 text-amber-700' :
                        c.customer_type === 'C_I' ? 'bg-purple-100 text-purple-700' :
                        'bg-gray-100 text-gray-500'
                      }`}>{c.customer_type}</span>
                    </td>
                    <td className="px-3 py-2">{c.tariff_plan ?? '—'}</td>
                    <td className="px-3 py-2">{c.connection_date ?? '—'}</td>
                    <td className="px-3 py-2">{c.last_transaction_date ?? '—'}</td>
                    <td className="px-3 py-2">{c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
          <Pagination page={customerPage} total={customerTotal} limit={PAGE_SIZE} onPageChange={setCustomerPage} />
          </>
          )}
        </div>
      )}

      {/* Transactions Tab */}
      {tab === 'transactions' && !loading && (
        <div className="space-y-4">
          {siteCodes.length === 0 ? (
            <EmptyState message="Load the Asset Register tab first to populate site codes." />
          ) : (
          <>
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Site</label>
              <select
                value={txnSite}
                onChange={(e) => { setTxnSite(e.target.value); setTxnPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              >
                {siteCodes.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">From</label>
              <input
                type="date"
                value={txnDateFrom}
                onChange={(e) => { setTxnDateFrom(e.target.value); setTxnPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">To</label>
              <input
                type="date"
                value={txnDateTo}
                onChange={(e) => { setTxnDateTo(e.target.value); setTxnPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              />
            </div>
            <div className="text-sm text-gray-500">{txnTotal} transactions</div>
          </div>

          {txns.length === 0 ? (
            <EmptyState message="No transactions found for the selected site and date range." />
          ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-3 py-2 text-left">Account</th>
                  <th className="px-3 py-2 text-left">Customer</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-left">Date</th>
                  <th className="px-3 py-2 text-right">kWh</th>
                  <th className="px-3 py-2 text-right">Amount (local)</th>
                  <th className="px-3 py-2 text-right">Amount (USD)</th>
                  <th className="px-3 py-2 text-left">Tariff</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {txns.map((t, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-xs">{t.account_number}</td>
                    <td className="px-3 py-2">{t.customer_name}</td>
                    <td className="px-3 py-2">{t.customer_type}</td>
                    <td className="px-3 py-2">{t.timestamp?.slice(0, 10) ?? '—'}</td>
                    <td className="px-3 py-2 text-right">{t.kwh.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right">{t.amount_local.toFixed(2)} {t.currency}</td>
                    <td className="px-3 py-2 text-right">{t.amount_usd.toFixed(2)}</td>
                    <td className="px-3 py-2">{t.tariff_plan ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
          <Pagination page={txnPage} total={txnTotal} limit={PAGE_SIZE} onPageChange={setTxnPage} />
          </>
          )}
        </div>
      )
    </div>
  );
}
