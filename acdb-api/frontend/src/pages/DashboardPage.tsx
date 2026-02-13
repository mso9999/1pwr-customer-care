import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LabelList } from 'recharts';
import { listTables, getSiteSummary, type TableInfo, type SiteStat } from '../lib/api';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#ec4899', '#14b8a6', '#6366f1', '#84cc16', '#e11d48', '#0ea5e9', '#a855f7'];

interface SiteRow {
  concession: string;
  customer_count: number;
  mwh: number;
  lsl_thousands: number;
}

export default function DashboardPage() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [siteData, setSiteData] = useState<SiteRow[]>([]);
  const [totals, setTotals] = useState({ mwh: 0, lsl_thousands: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      listTables().catch(() => []),
      fetch('/api/sites').then(r => r.ok ? r.json() : { sites: [] }).catch(() => ({ sites: [] })),
      getSiteSummary().catch(() => ({ sites: [], totals: { mwh: 0, lsl_thousands: 0 } })),
    ]).then(([t, sitesResp, stats]) => {
      setTables(t);

      // Merge customer counts with MWh/LSL stats by site code
      const statsMap = new Map<string, SiteStat>();
      for (const s of (stats.sites || [])) {
        statsMap.set(s.site, s);
      }

      const merged: SiteRow[] = ((sitesResp.sites || []) as { concession: string; customer_count: number }[]).map(s => {
        const stat = statsMap.get(s.concession);
        return {
          concession: s.concession,
          customer_count: s.customer_count,
          mwh: stat?.mwh ?? 0,
          lsl_thousands: stat?.lsl_thousands ?? 0,
        };
      });

      setSiteData(merged);
      setTotals(stats.totals || { mwh: 0, lsl_thousands: 0 });
    }).finally(() => setLoading(false));
  }, []);

  const totalCustomers = siteData.reduce((sum, s) => sum + s.customer_count, 0);
  const totalTables = tables.length;

  if (loading) return <div className="text-center py-16 text-gray-400">Loading dashboard...</div>;

  // Chart data with callout labels
  const barData = siteData.map(s => ({
    ...s,
    label: `${s.mwh.toFixed(1)} MWh / ${s.lsl_thousands.toFixed(1)}k LSL`,
  }));

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">Dashboard</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <p className="text-xs sm:text-sm text-gray-500">Customers</p>
          <p className="text-2xl sm:text-3xl font-bold text-blue-700">{totalCustomers.toLocaleString()}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <p className="text-xs sm:text-sm text-gray-500">Total MWh</p>
          <p className="text-2xl sm:text-3xl font-bold text-green-700">{totals.mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <p className="text-xs sm:text-sm text-gray-500">'000 LSL Sold</p>
          <p className="text-2xl sm:text-3xl font-bold text-amber-700">{totals.lsl_thousands.toLocaleString(undefined, { maximumFractionDigits: 1 })}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <p className="text-xs sm:text-sm text-gray-500">Sites</p>
          <p className="text-2xl sm:text-3xl font-bold text-purple-700">{siteData.length}</p>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
        {/* Bar chart: customers per site with MWh/LSL callouts */}
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <h2 className="text-base sm:text-lg font-semibold text-gray-700 mb-3 sm:mb-4">Customers per Site</h2>
          {barData.length > 0 ? (
            <ResponsiveContainer width="100%" height={Math.max(250, barData.length * 45)}>
              <BarChart data={barData} layout="vertical" margin={{ left: 10, right: 140 }}>
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis dataKey="concession" type="category" width={40} tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(value: any, name: any) => {
                    if (name === 'customer_count') return [value, 'Customers'];
                    return [value, name];
                  }}
                />
                <Bar dataKey="customer_count" fill="#3b82f6" radius={[0, 4, 4, 0]}>
                  <LabelList
                    dataKey="label"
                    position="right"
                    style={{ fontSize: 10, fill: '#6b7280' }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-gray-400 text-center py-8">No site data available</p>
          )}
        </div>

        {/* Pie chart: distribution */}
        <div className="bg-white rounded-lg shadow p-4 sm:p-5">
          <h2 className="text-base sm:text-lg font-semibold text-gray-700 mb-3 sm:mb-4">Customer Distribution</h2>
          {siteData.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={siteData}
                    dataKey="customer_count"
                    nameKey="concession"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    label={false}
                  >
                    {siteData.map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 justify-center">
                {siteData.map((s, i) => (
                  <div key={s.concession} className="flex items-center gap-1.5 text-xs text-gray-600">
                    <span className="w-2.5 h-2.5 rounded-full inline-block shrink-0" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
                    {s.concession} ({s.customer_count})
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-gray-400 text-center py-8">No data</p>
          )}
        </div>
      </div>

      {/* Per-site stats table */}
      <div className="bg-white rounded-lg shadow p-4 sm:p-5">
        <h2 className="text-base sm:text-lg font-semibold text-gray-700 mb-3">Site Performance</h2>
        {siteData.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">Site</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">Customers</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">MWh</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">'000 LSL</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">MWh/Customer</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {siteData.map(s => (
                  <tr key={s.concession} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-medium">{s.concession}</td>
                    <td className="px-3 py-2 text-right">{s.customer_count.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">{s.mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className="px-3 py-2 text-right">{s.lsl_thousands.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className="px-3 py-2 text-right text-gray-500">
                      {s.customer_count > 0 ? (s.mwh / s.customer_count).toFixed(2) : '--'}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-gray-50 border-t font-medium">
                <tr>
                  <td className="px-3 py-2">Total</td>
                  <td className="px-3 py-2 text-right">{totalCustomers.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{totals.mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                  <td className="px-3 py-2 text-right">{totals.lsl_thousands.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                  <td className="px-3 py-2 text-right text-gray-500">
                    {totalCustomers > 0 ? (totals.mwh / totalCustomers).toFixed(2) : '--'}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        ) : (
          <p className="text-gray-400 text-center py-4">No data</p>
        )}
      </div>

      {/* Quick access: tables */}
      <div className="bg-white rounded-lg shadow p-4 sm:p-5">
        <h2 className="text-base sm:text-lg font-semibold text-gray-700 mb-3">Database Tables ({totalTables})</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
          {tables.map((t) => (
            <Link key={t.name} to={`/tables/${t.name}`} className="p-3 bg-gray-50 hover:bg-blue-50 active:bg-blue-100 rounded-lg border text-sm transition">
              <div className="font-medium text-gray-800 truncate">{t.name}</div>
              <div className="text-xs text-gray-400">{t.row_count.toLocaleString()} rows &middot; {t.column_count} cols</div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
