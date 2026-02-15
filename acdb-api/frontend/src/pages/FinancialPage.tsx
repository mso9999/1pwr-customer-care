import { useEffect, useState, useRef, useCallback } from 'react';
import {
  Line, BarChart, Bar, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  Cell,
} from 'recharts';
import { getARPU, getMonthlyARPU } from '../lib/api';
import type { ARPUResponse, MonthlyARPUResponse } from '../lib/api';
import html2canvas from 'html2canvas';
import { jsPDF } from 'jspdf';

// Colors palette (same as OMReportPage)
const COLORS = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed',
  '#0891b2', '#be185d', '#65a30d', '#ea580c', '#4f46e5',
  '#0d9488', '#b91c1c',
];

// ---------------------------------------------------------------------------
// PDF Export Utilities (reused pattern from OMReportPage)
// ---------------------------------------------------------------------------

async function captureElement(el: HTMLElement): Promise<HTMLCanvasElement> {
  return html2canvas(el, {
    scale: 2,
    useCORS: true,
    backgroundColor: '#ffffff',
    logging: false,
  });
}

async function exportSingleFigure(el: HTMLElement, title: string) {
  const canvas = await captureElement(el);
  const imgData = canvas.toDataURL('image/png');
  const pdf = new jsPDF({
    orientation: canvas.width > canvas.height ? 'landscape' : 'portrait',
    unit: 'px',
    format: [canvas.width, canvas.height],
  });
  pdf.addImage(imgData, 'PNG', 0, 0, canvas.width, canvas.height);
  pdf.save(`${title.replace(/\s+/g, '_')}.pdf`);
}

async function exportAllFigures(figures: { el: HTMLElement; title: string }[]) {
  const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const margin = 10;

  // Title page
  pdf.setFontSize(24);
  pdf.setTextColor(30, 58, 138);
  pdf.text('Financial Analytics Report', pageW / 2, 60, { align: 'center' });
  pdf.setFontSize(14);
  pdf.setTextColor(100, 100, 100);
  pdf.text('Sotho Minigrid Portfolio (SMP)', pageW / 2, 80, { align: 'center' });
  pdf.text(`Generated: ${new Date().toLocaleDateString()}`, pageW / 2, 95, { align: 'center' });
  pdf.setFontSize(12);
  pdf.text('OnePower Lesotho', pageW / 2, 115, { align: 'center' });

  for (let i = 0; i < figures.length; i++) {
    pdf.addPage();
    const canvas = await captureElement(figures[i].el);
    const imgData = canvas.toDataURL('image/png');
    const ratio = canvas.width / canvas.height;
    const imgW = pageW - 2 * margin;
    let imgH = imgW / ratio;
    if (imgH > pageH - 2 * margin) {
      imgH = pageH - 2 * margin;
    }
    pdf.addImage(imgData, 'PNG', margin, margin, imgW, imgH);
  }

  pdf.save('Financial_Analytics_Report.pdf');
}

// ---------------------------------------------------------------------------
// Reusable components
// ---------------------------------------------------------------------------

function ExportBtn({ onClick, label = 'Export PDF' }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 hover:text-blue-700 transition shadow-sm"
    >
      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      {label}
    </button>
  );
}

function Figure({
  id,
  title,
  subtitle,
  figureRef,
  children,
  onExport,
}: {
  id: string;
  title: string;
  subtitle?: string;
  figureRef: (el: HTMLDivElement | null) => void;
  children: React.ReactNode;
  onExport: () => void;
}) {
  return (
    <div
      ref={figureRef}
      id={id}
      className="bg-white rounded-xl shadow-md border border-gray-100 p-4 sm:p-6 mb-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
        <div>
          <h3 className="text-base sm:text-lg font-bold text-gray-800">{title}</h3>
          {subtitle && <p className="text-xs sm:text-sm text-gray-500 mt-0.5">{subtitle}</p>}
        </div>
        <ExportBtn onClick={onExport} />
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatLSL(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString();
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function FinancialPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);

  const [data, setData] = useState<ARPUResponse | null>(null);
  const [monthlyData, setMonthlyData] = useState<MonthlyARPUResponse | null>(null);

  // Figure refs for PDF export
  const figRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const setFigRef = useCallback(
    (key: string) => (el: HTMLDivElement | null) => {
      figRefs.current[key] = el;
    },
    [],
  );

  useEffect(() => {
    async function loadAll() {
      setLoading(true);
      setError('');
      try {
        const [arpu, monthly] = await Promise.all([
          getARPU(),
          getMonthlyARPU().catch(() => null),
        ]);
        setData(arpu);
        setMonthlyData(monthly);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    loadAll();
  }, []);

  const handleExportFigure = (key: string, title: string) => () => {
    const el = figRefs.current[key];
    if (el) exportSingleFigure(el, title);
  };

  const handleExportAll = async () => {
    setExporting(true);
    try {
      const figures = Object.entries(figRefs.current)
        .filter(([, el]) => el !== null)
        .map(([key, el]) => ({ el: el!, title: key }));
      await exportAllFigures(figures);
    } finally {
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <div className="animate-spin w-10 h-10 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-gray-500">Loading financial data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return <div className="bg-red-50 text-red-700 px-6 py-4 rounded-xl">{error}</div>;
  }

  if (!data || !data.arpu || data.arpu.length === 0) {
    return (
      <div className="bg-amber-50 text-amber-800 px-6 py-4 rounded-xl">
        No ARPU data available. Ensure account history data exists in the ACCDB.
      </div>
    );
  }

  // Derived data
  const arpu = data.arpu;
  const siteCodes = data.site_codes;
  const siteNames = data.site_names;
  const latest = arpu[arpu.length - 1];
  const prev = arpu.length >= 2 ? arpu[arpu.length - 2] : null;

  const totalRevenue = arpu.reduce((sum, p) => sum + p.total_revenue, 0);
  const revenueGrowth =
    prev && prev.total_revenue > 0
      ? ((latest.total_revenue - prev.total_revenue) / prev.total_revenue) * 100
      : 0;

  // Build stacked revenue chart data: each quarter has per-site revenue
  const revenueStackData = arpu.map((p) => {
    const row: Record<string, any> = { quarter: p.quarter, total: p.total_revenue };
    for (const code of siteCodes) {
      row[code] = p.per_site[code]?.revenue ?? 0;
    }
    return row;
  });

  // Latest quarter ARPU by site for bar chart
  const latestSiteArpu = siteCodes
    .map((code) => ({
      site: code,
      name: siteNames[code] || code,
      arpu: latest.per_site[code]?.arpu ?? 0,
      revenue: latest.per_site[code]?.revenue ?? 0,
      customers: latest.per_site[code]?.customers ?? 0,
    }))
    .filter((s) => s.revenue > 0)
    .sort((a, b) => b.arpu - a.arpu);

  return (
    <div>
      {/* Report Header */}
      <div className="bg-gradient-to-r from-emerald-700 to-emerald-900 rounded-xl shadow-lg p-6 sm:p-8 mb-6 text-white">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">Financial Analytics</h1>
            <p className="text-emerald-200 mt-1">Sotho Minigrid Portfolio (SMP)</p>
            <p className="text-emerald-300 text-sm mt-1">
              ARPU, revenue trends & per-site financial breakdown
            </p>
          </div>
          <button
            onClick={handleExportAll}
            disabled={exporting}
            className="flex items-center gap-2 px-5 py-3 bg-white text-emerald-800 font-semibold rounded-lg hover:bg-emerald-50 transition shadow disabled:opacity-50"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            </svg>
            {exporting ? 'Generating PDF...' : 'Export All as PDF'}
          </button>
        </div>
      </div>

      {/* Headline Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 mb-6">
        <div className="bg-white rounded-xl shadow p-4 border-l-4 border-emerald-500">
          <p className="text-xs text-gray-500 uppercase font-medium">Latest ARPU</p>
          <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">
            LSL {latest.arpu.toLocaleString()}
          </p>
          <p className="text-xs text-gray-400 mt-1">{latest.quarter}</p>
        </div>
        <div className="bg-white rounded-xl shadow p-4 border-l-4 border-blue-500">
          <p className="text-xs text-gray-500 uppercase font-medium">Total Revenue</p>
          <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">
            LSL {formatLSL(totalRevenue)}
          </p>
          <p className="text-xs text-gray-400 mt-1">all quarters</p>
        </div>
        <div className="bg-white rounded-xl shadow p-4 border-l-4 border-purple-500">
          <p className="text-xs text-gray-500 uppercase font-medium">Active Customers</p>
          <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">
            {latest.active_customers.toLocaleString()}
          </p>
          <p className="text-xs text-gray-400 mt-1">{latest.quarter}</p>
        </div>
        <div className="bg-white rounded-xl shadow p-4 border-l-4 border-amber-500">
          <p className="text-xs text-gray-500 uppercase font-medium">Revenue Growth</p>
          <p
            className={`text-2xl sm:text-3xl font-bold mt-1 ${
              revenueGrowth >= 0 ? 'text-green-600' : 'text-red-600'
            }`}
          >
            {revenueGrowth >= 0 ? '+' : ''}
            {revenueGrowth.toFixed(1)}%
          </p>
          <p className="text-xs text-gray-400 mt-1">QoQ</p>
        </div>
      </div>

      {/* ── Figure 1: ARPU Trend ── */}
      <Figure
        id="fig-arpu-trend"
        title="Figure 1: ARPU Trend"
        subtitle="Average Revenue Per User (LSL) per quarter, with total revenue on secondary axis"
        figureRef={setFigRef('arpu-trend')}
        onExport={handleExportFigure('arpu-trend', 'ARPU_Trend')}
      >
        <ResponsiveContainer width="100%" height={380}>
          <ComposedChart data={arpu} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="quarter"
              angle={-35}
              textAnchor="end"
              tick={{ fontSize: 10 }}
              interval={0}
            />
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 11 }}
              label={{
                value: 'ARPU (LSL)',
                angle: -90,
                position: 'insideLeft',
                style: { fontSize: 10 },
              }}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => formatLSL(v)}
              label={{
                value: 'Revenue (LSL)',
                angle: 90,
                position: 'insideRight',
                style: { fontSize: 10 },
              }}
            />
            <Tooltip
              contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
              formatter={(value: any, name: any) => {
                if (name === 'ARPU') return [`LSL ${Number(value).toFixed(2)}`, name];
                return [`LSL ${Number(value).toLocaleString()}`, name];
              }}
            />
            <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
            <Bar
              yAxisId="right"
              dataKey="total_revenue"
              name="Total Revenue"
              fill="#93c5fd"
              fillOpacity={0.6}
              radius={[4, 4, 0, 0]}
            />
            <Line
              yAxisId="left"
              dataKey="arpu"
              name="ARPU"
              stroke="#10b981"
              strokeWidth={3}
              dot={{ r: 5, fill: '#10b981' }}
              activeDot={{ r: 7 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </Figure>

      {/* ── Figure 2: Monthly ARPU ── */}
      {monthlyData && monthlyData.monthly_arpu && monthlyData.monthly_arpu.length > 0 && (() => {
        const monthly = monthlyData.monthly_arpu;
        // Color each bar by quarter for visual grouping
        const quarterColors: Record<string, string> = {};
        const uniqueQuarters = [...new Set(monthly.map((m) => m.quarter))];
        uniqueQuarters.forEach((q, i) => {
          quarterColors[q] = COLORS[i % COLORS.length];
        });

        return (
          <Figure
            id="fig-monthly-arpu"
            title="Figure 2: Monthly ARPU"
            subtitle="Average Revenue Per User (LSL) per month, colored by quarter"
            figureRef={setFigRef('monthly-arpu')}
            onExport={handleExportFigure('monthly-arpu', 'Monthly_ARPU')}
          >
            <ResponsiveContainer width="100%" height={380}>
              <ComposedChart data={monthly} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="month"
                  angle={-45}
                  textAnchor="end"
                  tick={{ fontSize: 9 }}
                  interval={0}
                />
                <YAxis
                  yAxisId="left"
                  tick={{ fontSize: 11 }}
                  label={{
                    value: 'ARPU (LSL)',
                    angle: -90,
                    position: 'insideLeft',
                    style: { fontSize: 10 },
                  }}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: number) => formatLSL(v)}
                  label={{
                    value: 'Revenue (LSL)',
                    angle: 90,
                    position: 'insideRight',
                    style: { fontSize: 10 },
                  }}
                />
                <Tooltip
                  contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                  formatter={(value: any, name: any) => {
                    if (name === 'ARPU') return [`LSL ${Number(value).toFixed(2)}`, name];
                    return [`LSL ${Number(value).toLocaleString()}`, name];
                  }}
                  labelFormatter={(label) => {
                    const point = monthly.find((m) => m.month === label);
                    return `${label} (${point?.quarter || ''})`;
                  }}
                />
                <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                <Bar yAxisId="right" dataKey="total_revenue" name="Revenue" fillOpacity={0.6} radius={[3, 3, 0, 0]}>
                  {monthly.map((entry, i) => (
                    <Cell key={i} fill={quarterColors[entry.quarter] || '#93c5fd'} fillOpacity={0.5} />
                  ))}
                </Bar>
                <Line
                  yAxisId="left"
                  dataKey="arpu"
                  name="ARPU"
                  stroke="#10b981"
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: '#10b981' }}
                  activeDot={{ r: 6 }}
                />
              </ComposedChart>
            </ResponsiveContainer>

            {/* Quarter legend */}
            <div className="flex flex-wrap gap-3 mt-3 justify-center text-xs">
              {uniqueQuarters.map((q, i) => (
                <span key={q} className="flex items-center gap-1.5">
                  <span
                    className="inline-block w-3 h-3 rounded-sm"
                    style={{ backgroundColor: COLORS[i % COLORS.length], opacity: 0.6 }}
                  />
                  {q}
                </span>
              ))}
            </div>

            {/* Monthly data table */}
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium text-gray-600">Month</th>
                    <th className="px-3 py-2 text-left font-medium text-gray-600">Quarter</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Revenue (LSL)</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Customers</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">ARPU (LSL)</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {monthly.map((row) => (
                    <tr key={row.month} className="hover:bg-gray-50">
                      <td className="px-3 py-1.5 font-medium text-gray-800">{row.month}</td>
                      <td className="px-3 py-1.5 text-gray-500">{row.quarter}</td>
                      <td className="px-3 py-1.5 text-right text-gray-700 tabular-nums">
                        {Math.round(row.total_revenue).toLocaleString()}
                      </td>
                      <td className="px-3 py-1.5 text-right text-gray-700 tabular-nums">
                        {row.active_customers.toLocaleString()}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono font-semibold text-emerald-700 tabular-nums">
                        {row.arpu.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Figure>
        );
      })()}

      {/* ── Figure 3: Revenue by Site (stacked) ── */}
      <Figure
        id="fig-revenue-by-site"
        title="Figure 3: Quarterly Revenue by Site"
        subtitle="Stacked breakdown of LSL revenue per quarter by concession"
        figureRef={setFigRef('revenue-by-site')}
        onExport={handleExportFigure('revenue-by-site', 'Revenue_By_Site')}
      >
        <ResponsiveContainer width="100%" height={380}>
          <BarChart
            data={revenueStackData}
            margin={{ top: 5, right: 20, left: 0, bottom: 60 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="quarter"
              angle={-35}
              textAnchor="end"
              tick={{ fontSize: 10 }}
              interval={0}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => formatLSL(v)}
            />
            <Tooltip
              contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
              formatter={(value: any, name: any) => [
                `LSL ${Number(value).toLocaleString()}`,
                siteNames[name] || name,
              ]}
            />
            <Legend
              wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }}
              formatter={(value: string) => siteNames[value] || value}
            />
            {siteCodes.map((code, i) => (
              <Bar
                key={code}
                dataKey={code}
                stackId="revenue"
                fill={COLORS[i % COLORS.length]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </Figure>

      {/* ── Figure 4: ARPU by Site (latest quarter) ── */}
      {latestSiteArpu.length > 0 && (
        <Figure
          id="fig-arpu-by-site"
          title={`Figure 4: ARPU by Site (${latest.quarter})`}
          subtitle="Average Revenue Per User by concession for the latest quarter"
          figureRef={setFigRef('arpu-by-site')}
          onExport={handleExportFigure('arpu-by-site', 'ARPU_By_Site')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <BarChart
              data={latestSiteArpu}
              margin={{ top: 5, right: 80, left: 0, bottom: 40 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-25} textAnchor="end" />
              <YAxis
                tick={{ fontSize: 11 }}
                tickFormatter={(v: number) => `LSL ${v}`}
              />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any, name: any) => {
                  if (name === 'ARPU') return [`LSL ${Number(value).toFixed(2)}`, name];
                  if (name === 'Revenue') return [`LSL ${Number(value).toLocaleString()}`, name];
                  return [value, name];
                }}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Bar dataKey="arpu" name="ARPU" radius={[4, 4, 0, 0]}>
                {latestSiteArpu.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>

          {/* Summary table */}
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">Site</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">Customers</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">Revenue (LSL)</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">ARPU (LSL)</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {latestSiteArpu.map((s, i) => (
                  <tr key={s.site} className="hover:bg-gray-50">
                    <td className="px-3 py-1.5">
                      <span
                        className="inline-block w-3 h-3 rounded-sm mr-2"
                        style={{ backgroundColor: COLORS[i % COLORS.length] }}
                      />
                      <span className="font-semibold">{s.name}</span>
                      <span className="text-gray-400 ml-1 text-xs">({s.site})</span>
                    </td>
                    <td className="px-3 py-1.5 text-right text-gray-700">
                      {s.customers.toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right text-gray-700">
                      {s.revenue.toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold text-emerald-700">
                      {s.arpu.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-gray-50 border-t font-semibold">
                <tr>
                  <td className="px-3 py-2">Total / Average</td>
                  <td className="px-3 py-2 text-right">
                    {latest.active_customers.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {latest.total_revenue.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-emerald-700">
                    {latest.arpu.toFixed(2)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Figure>
      )}

      {/* ── Figure 5: Full Revenue Breakdown Table ── */}
      <Figure
        id="fig-revenue-table"
        title="Figure 5: Revenue Breakdown by Site and Quarter"
        subtitle="Detailed per-site, per-quarter revenue (LSL)"
        figureRef={setFigRef('revenue-table')}
        onExport={handleExportFigure('revenue-table', 'Revenue_Breakdown')}
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-600 sticky left-0 bg-gray-50">
                  Quarter
                </th>
                {siteCodes.map((code) => (
                  <th
                    key={code}
                    className="px-3 py-2 text-right font-medium text-gray-600 whitespace-nowrap"
                  >
                    {siteNames[code] || code}
                  </th>
                ))}
                <th className="px-3 py-2 text-right font-medium text-gray-800">Total</th>
                <th className="px-3 py-2 text-right font-medium text-emerald-700">ARPU</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {arpu.map((row) => (
                <tr key={row.quarter} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 font-medium text-gray-800 sticky left-0 bg-white whitespace-nowrap">
                    {row.quarter}
                  </td>
                  {siteCodes.map((code) => (
                    <td key={code} className="px-3 py-1.5 text-right text-gray-600 tabular-nums">
                      {(row.per_site[code]?.revenue ?? 0) > 0
                        ? Math.round(row.per_site[code].revenue).toLocaleString()
                        : '—'}
                    </td>
                  ))}
                  <td className="px-3 py-1.5 text-right font-semibold text-gray-800 tabular-nums">
                    {Math.round(row.total_revenue).toLocaleString()}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono font-semibold text-emerald-700 tabular-nums">
                    {row.arpu.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-gray-50 border-t font-semibold">
              <tr>
                <td className="px-3 py-2 sticky left-0 bg-gray-50">All Quarters</td>
                {siteCodes.map((code) => {
                  const siteTotal = arpu.reduce(
                    (sum, row) => sum + (row.per_site[code]?.revenue ?? 0),
                    0,
                  );
                  return (
                    <td key={code} className="px-3 py-2 text-right tabular-nums">
                      {siteTotal > 0 ? Math.round(siteTotal).toLocaleString() : '—'}
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-right tabular-nums">
                  {Math.round(totalRevenue).toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right font-mono text-emerald-700 tabular-nums">
                  {(totalRevenue / Math.max(arpu.reduce((s, r) => s + r.active_customers, 0) / arpu.length, 1)).toFixed(2)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </Figure>

      {/* Footer */}
      <div className="text-center text-xs text-gray-400 py-6 border-t mt-6">
        <p>Data source: 1PWR Customer Care Portal (ACCDB)</p>
        <p className="mt-1">
          ARPU = Total Quarterly Revenue / Active Customers in Quarter
        </p>
      </div>
    </div>
  );
}
