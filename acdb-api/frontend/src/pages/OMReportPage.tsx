import { useEffect, useState, useRef, useCallback } from 'react';
import {
  BarChart, Bar, Line, LineChart, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart, Cell,
} from 'recharts';
import {
  getOMOverview, getCustomerStatsBySite, getCustomerGrowth,
  getConsumptionBySite, getSalesBySite, getCumulativeTrends,
  getAvgConsumptionTrend, getSiteOverview, getLoadCurvesByType,
  getDailyLoadProfiles,
} from '../lib/api';
import type {
  OMOverview, CustomerSiteStat, CustomerGrowthPoint,
  SiteConsumption, CumulativeTrend, AvgConsumptionTrend, SiteOverviewItem,
  LoadCurve, LoadCurveResponse, LoadProfileResponse,
} from '../lib/api';
import html2canvas from 'html2canvas';
import { jsPDF } from 'jspdf';

// Colors palette for charts
const COLORS = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed',
  '#0891b2', '#be185d', '#65a30d', '#ea580c', '#4f46e5',
  '#0d9488', '#b91c1c',
];

// ---------------------------------------------------------------------------
// PDF Export Utilities
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
  pdf.text('Operations & Maintenance', pageW / 2, 60, { align: 'center' });
  pdf.text('Quarterly Report', pageW / 2, 75, { align: 'center' });
  pdf.setFontSize(14);
  pdf.setTextColor(100, 100, 100);
  pdf.text('Sotho Minigrid Portfolio (SMP)', pageW / 2, 95, { align: 'center' });
  pdf.text(`Generated: ${new Date().toLocaleDateString()}`, pageW / 2, 110, { align: 'center' });
  pdf.setFontSize(12);
  pdf.text('OnePower Lesotho', pageW / 2, 130, { align: 'center' });

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

  pdf.save('OM_Quarterly_Report.pdf');
}

// ---------------------------------------------------------------------------
// Export Button Component
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

// ---------------------------------------------------------------------------
// Figure Wrapper (captures ref for export)
// ---------------------------------------------------------------------------

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
// Main Page
// ---------------------------------------------------------------------------

export default function OMReportPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);

  // Data states
  const [overview, setOverview] = useState<OMOverview | null>(null);
  const [customerStats, setCustomerStats] = useState<CustomerSiteStat[]>([]);
  const [customerTotals, setCustomerTotals] = useState<Record<string, number>>({});
  const [growth, setGrowth] = useState<CustomerGrowthPoint[]>([]);
  const [consumption, setConsumption] = useState<SiteConsumption[]>([]);
  const [sales, setSales] = useState<SiteConsumption[]>([]);
  const [cumulative, setCumulative] = useState<CumulativeTrend[]>([]);
  const [avgTrend, setAvgTrend] = useState<AvgConsumptionTrend[]>([]);
  const [siteOverview, setSiteOverview] = useState<SiteOverviewItem[]>([]);
  const [loadCurves, setLoadCurves] = useState<LoadCurve[]>([]);
  const [loadCurveQuarterly, setLoadCurveQuarterly] = useState<Record<string, unknown>[]>([]);
  const [loadCurveTypes, setLoadCurveTypes] = useState<string[]>([]);
  const [loadProfiles, setLoadProfiles] = useState<Record<string, unknown>[]>([]);
  const [loadProfileTypes, setLoadProfileTypes] = useState<string[]>([]);
  const [profileSite, setProfileSite] = useState<string>('');
  const [profileLoading, setProfileLoading] = useState(false);

  // Figure refs for PDF export
  const figRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const setFigRef = useCallback((key: string) => (el: HTMLDivElement | null) => {
    figRefs.current[key] = el;
  }, []);

  useEffect(() => {
    async function loadAll() {
      setLoading(true);
      setError('');
      try {
        const [ov, cs, gr, co, sa, cu, av, so, lc, lp] = await Promise.all([
          getOMOverview(),
          getCustomerStatsBySite(),
          getCustomerGrowth(),
          getConsumptionBySite(),
          getSalesBySite(),
          getCumulativeTrends(),
          getAvgConsumptionTrend(),
          getSiteOverview(),
          getLoadCurvesByType().catch(() => ({ curves: [], quarterly: [], customer_types: [] } as LoadCurveResponse)),
          getDailyLoadProfiles().catch(() => ({ profiles: [], chart_data: [], customer_types: [] } as LoadProfileResponse)),
        ]);
        setOverview(ov);
        setCustomerStats(cs.sites);
        setCustomerTotals(cs.totals);
        setGrowth(gr.growth);
        setConsumption(co.sites);
        setSales(sa.sites as any);
        setCumulative(cu.trends);
        setAvgTrend(av.trends);
        setSiteOverview(so.sites);
        setLoadCurves(lc.curves || []);
        setLoadCurveQuarterly(lc.quarterly || []);
        setLoadCurveTypes(lc.customer_types || []);
        setLoadProfiles(lp.chart_data || []);
        setLoadProfileTypes(lp.customer_types || []);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    loadAll();
  }, []);

  // Site filter for load profiles
  const handleProfileSiteChange = async (site: string) => {
    setProfileSite(site);
    setProfileLoading(true);
    try {
      const lp = await getDailyLoadProfiles(site || undefined);
      setLoadProfiles(lp.chart_data || []);
      setLoadProfileTypes(lp.customer_types || []);
    } catch {
      setLoadProfiles([]);
      setLoadProfileTypes([]);
    } finally {
      setProfileLoading(false);
    }
  };

  // Known site codes for the dropdown
  const SITE_CODES = ['MAK', 'MAS', 'SHG', 'LEB', 'SEH', 'MAT', 'TLH', 'TOS', 'SEB', 'RIB', 'KET', 'RTE', 'PTA', 'FSI', 'MTK'];

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
          <div className="animate-spin w-10 h-10 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-gray-500">Loading O&M Report data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return <div className="bg-red-50 text-red-700 px-6 py-4 rounded-xl">{error}</div>;
  }

  // Prepare chart data
  const customerBarData = customerStats.map(s => ({
    name: s.concession.length > 10 ? s.concession.substring(0, 10) + '...' : s.concession,
    fullName: s.concession,
    Total: s.total,
    Active: s.active,
    New: s.new,
  }));

  const consumptionBarData = consumption.map(s => ({
    name: s.site,
    fullName: s.name,
    kwh: Math.round(s.total_kwh),
  }));

  const salesBarData = (sales as any[]).map((s: any) => ({
    name: s.site,
    fullName: s.name,
    lsl: Math.round(s.total_lsl || s.total_kwh || 0),
  }));

  return (
    <div>
      {/* Report Header */}
      <div className="bg-gradient-to-r from-blue-700 to-blue-900 rounded-xl shadow-lg p-6 sm:p-8 mb-6 text-white">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">Operations & Maintenance Report</h1>
            <p className="text-blue-200 mt-1">Sotho Minigrid Portfolio (SMP)</p>
            <p className="text-blue-300 text-sm mt-1">Auto-generated from Customer Care Portal data</p>
          </div>
          <button
            onClick={handleExportAll}
            disabled={exporting}
            className="flex items-center gap-2 px-5 py-3 bg-white text-blue-800 font-semibold rounded-lg hover:bg-blue-50 transition shadow disabled:opacity-50"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            {exporting ? 'Generating PDF...' : 'Export All as PDF'}
          </button>
        </div>
      </div>

      {/* Overview Cards */}
      {overview && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 mb-6">
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-blue-500">
            <p className="text-xs text-gray-500 uppercase font-medium">Total Customers</p>
            <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">{overview.total_customers.toLocaleString()}</p>
            <p className="text-xs text-green-600 mt-1">{overview.active_customers.toLocaleString()} active</p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-green-500">
            <p className="text-xs text-gray-500 uppercase font-medium">Total MWh</p>
            <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">{overview.total_mwh.toLocaleString()}</p>
            <p className="text-xs text-gray-400 mt-1">consumed all-time</p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-amber-500">
            <p className="text-xs text-gray-500 uppercase font-medium">&apos;000 LSL Revenue</p>
            <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">{overview.total_lsl_thousands.toLocaleString()}</p>
            <p className="text-xs text-gray-400 mt-1">sold all-time</p>
          </div>
          <div className="bg-white rounded-xl shadow p-4 border-l-4 border-purple-500">
            <p className="text-xs text-gray-500 uppercase font-medium">Active Sites</p>
            <p className="text-2xl sm:text-3xl font-bold text-gray-800 mt-1">{overview.total_sites}</p>
            <p className="text-xs text-gray-400 mt-1">concessions</p>
          </div>
        </div>
      )}

      {/* Site Overview Table */}
      {siteOverview.length > 0 && (
        <Figure
          id="fig-site-overview"
          title="Table 1: Portfolio Site Overview"
          subtitle="List of minigrids and PIH clinics with districts"
          figureRef={setFigRef('site-overview')}
          onExport={handleExportFigure('site-overview', 'Site_Overview')}
        >
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-4 py-2 text-left font-medium text-gray-600">#</th>
                  <th className="px-4 py-2 text-left font-medium text-gray-600">Concession</th>
                  <th className="px-4 py-2 text-left font-medium text-gray-600">Abbrev.</th>
                  <th className="px-4 py-2 text-left font-medium text-gray-600">District</th>
                  <th className="px-4 py-2 text-right font-medium text-gray-600">Customers</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {siteOverview.map((s, i) => (
                  <tr key={s.concession} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-400">{i + 1}</td>
                    <td className="px-4 py-2 font-medium text-gray-800">{s.concession}</td>
                    <td className="px-4 py-2 font-mono text-gray-600">{s.abbreviation || '—'}</td>
                    <td className="px-4 py-2 text-gray-600">{s.district || '—'}</td>
                    <td className="px-4 py-2 text-right font-semibold text-gray-800">{s.customer_count}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-gray-50 font-semibold">
                <tr>
                  <td className="px-4 py-2" colSpan={4}>Total</td>
                  <td className="px-4 py-2 text-right">{siteOverview.reduce((a, s) => a + s.customer_count, 0)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Figure>
      )}

      {/* Customer Statistics per Site (Figure 14) */}
      {customerStats.length > 0 && (
        <Figure
          id="fig-customer-stats"
          title="Figure 1: Customer Statistics by Concession"
          subtitle={`Total: ${customerTotals.total?.toLocaleString()} customers, ${customerTotals.active?.toLocaleString()} active (${overview ? Math.round(customerTotals.active / customerTotals.total * 100) : 0}% activation)`}
          figureRef={setFigRef('customer-stats')}
          onExport={handleExportFigure('customer-stats', 'Customer_Statistics')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <BarChart data={customerBarData} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" angle={-35} textAnchor="end" tick={{ fontSize: 11 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any, name: any) => [value, name]}
                labelFormatter={(label: any, payload: any) => payload?.[0]?.payload?.fullName || label}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Bar dataKey="Total" fill="#93c5fd" radius={[4, 4, 0, 0]} />
              <Bar dataKey="Active" fill="#2563eb" radius={[4, 4, 0, 0]} />
              <Bar dataKey="New" fill="#16a34a" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Customer Growth Over Time (Figure 15) */}
      {growth.length > 0 && (
        <Figure
          id="fig-customer-growth"
          title="Figure 2: Customer Connection Growth"
          subtitle="Quarterly new connections and cumulative total since first site commissioned"
          figureRef={setFigRef('customer-growth')}
          onExport={handleExportFigure('customer-growth', 'Customer_Growth')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <ComposedChart data={growth} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Bar yAxisId="left" dataKey="new_customers" name="New Customers" fill="#93c5fd" radius={[4, 4, 0, 0]} />
              <Line yAxisId="right" dataKey="cumulative" name="Cumulative" stroke="#2563eb" strokeWidth={2.5} dot={{ r: 3 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Consumption by Site (Figure 12) */}
      {consumptionBarData.length > 0 && (
        <Figure
          id="fig-consumption-site"
          title="Figure 3: Electricity Consumption by Site"
          subtitle={`Total: ${Math.round(consumption.reduce((a, s) => a + s.total_kwh, 0)).toLocaleString()} kWh`}
          figureRef={setFigRef('consumption-site')}
          onExport={handleExportFigure('consumption-site', 'Consumption_By_Site')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <BarChart data={consumptionBarData} margin={{ top: 5, right: 20, left: 0, bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : String(v)} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`${Number(value).toLocaleString()} kWh`, 'Consumption']}
                labelFormatter={(label: any, payload: any) => payload?.[0]?.payload?.fullName || label}
              />
              <Bar dataKey="kwh" name="kWh Consumed" radius={[4, 4, 0, 0]}>
                {consumptionBarData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Sales by Site */}
      {salesBarData.length > 0 && (
        <Figure
          id="fig-sales-site"
          title="Figure 4: Revenue by Site"
          subtitle={`Total: LSL ${Math.round(salesBarData.reduce((a, s) => a + s.lsl, 0)).toLocaleString()}`}
          figureRef={setFigRef('sales-site')}
          onExport={handleExportFigure('sales-site', 'Revenue_By_Site')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <BarChart data={salesBarData} margin={{ top: 5, right: 20, left: 0, bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : String(v)} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`LSL ${Number(value).toLocaleString()}`, 'Revenue']}
                labelFormatter={(label: any, payload: any) => payload?.[0]?.payload?.fullName || label}
              />
              <Bar dataKey="lsl" name="LSL Revenue" radius={[4, 4, 0, 0]}>
                {salesBarData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Cumulative Consumption (Figure 3 in report) */}
      {cumulative.length > 0 && (
        <Figure
          id="fig-cumulative-consumption"
          title="Figure 5: Cumulative Electricity Consumed"
          subtitle="Running total since first site was commissioned"
          figureRef={setFigRef('cumulative-consumption')}
          onExport={handleExportFigure('cumulative-consumption', 'Cumulative_Consumption')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <AreaChart data={cumulative} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${(v/1000).toFixed(0)}k`} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`${Number(value).toLocaleString()} kWh`, '']}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Area dataKey="cumulative_kwh" name="Cumulative kWh" stroke="#2563eb" fill="#93c5fd" fillOpacity={0.4} strokeWidth={2} />
              <Area dataKey="kwh" name="Quarterly kWh" stroke="#16a34a" fill="#bbf7d0" fillOpacity={0.3} strokeWidth={1.5} />
            </AreaChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Cumulative Sales (Figure 4 in report) */}
      {cumulative.length > 0 && (
        <Figure
          id="fig-cumulative-sales"
          title="Figure 6: Cumulative Electricity Sales"
          subtitle="Running total revenue since first site was commissioned"
          figureRef={setFigRef('cumulative-sales')}
          onExport={handleExportFigure('cumulative-sales', 'Cumulative_Sales')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <AreaChart data={cumulative} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${(v/1000).toFixed(0)}k`} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`LSL ${Number(value).toLocaleString()}`, '']}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Area dataKey="cumulative_lsl" name="Cumulative LSL" stroke="#d97706" fill="#fde68a" fillOpacity={0.4} strokeWidth={2} />
              <Area dataKey="lsl" name="Quarterly LSL" stroke="#ea580c" fill="#fed7aa" fillOpacity={0.3} strokeWidth={1.5} />
            </AreaChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Quarterly Consumption (Figure 5 in report) */}
      {cumulative.length > 0 && (
        <Figure
          id="fig-quarterly-consumption"
          title="Figure 7: Quarterly Consumption"
          subtitle="Total electricity consumed per quarter"
          figureRef={setFigRef('quarterly-consumption')}
          onExport={handleExportFigure('quarterly-consumption', 'Quarterly_Consumption')}
        >
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={cumulative} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${(v/1000).toFixed(0)}k`} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`${Number(value).toLocaleString()} kWh`, 'Consumption']}
              />
              <Bar dataKey="kwh" name="kWh" fill="#2563eb" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Quarterly Sales (Figure 6 in report) */}
      {cumulative.length > 0 && (
        <Figure
          id="fig-quarterly-sales"
          title="Figure 8: Quarterly Sales"
          subtitle="Total revenue per quarter"
          figureRef={setFigRef('quarterly-sales')}
          onExport={handleExportFigure('quarterly-sales', 'Quarterly_Sales')}
        >
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={cumulative} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${(v/1000).toFixed(0)}k`} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any) => [`LSL ${Number(value).toLocaleString()}`, 'Revenue']}
              />
              <Bar dataKey="lsl" name="LSL" fill="#d97706" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Average Consumption Trend (Figures 8, 9 in report) */}
      {avgTrend.length > 0 && (
        <Figure
          id="fig-avg-consumption"
          title="Figure 9: Average Daily Consumption per Customer"
          subtitle="Trend in average daily kWh consumption per customer over time"
          figureRef={setFigRef('avg-consumption')}
          onExport={handleExportFigure('avg-consumption', 'Avg_Consumption_Trend')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <ComposedChart data={avgTrend} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} label={{ value: 'kWh/day/customer', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} label={{ value: 'Customers', angle: 90, position: 'insideRight', style: { fontSize: 10 } }} />
              <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Bar yAxisId="right" dataKey="customers" name="Customers" fill="#e2e8f0" radius={[4, 4, 0, 0]} />
              <Line yAxisId="left" dataKey="avg_daily_kwh_per_customer" name="Avg kWh/day" stroke="#dc2626" strokeWidth={2.5} dot={{ r: 3 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Average Sales Trend */}
      {avgTrend.length > 0 && (
        <Figure
          id="fig-avg-sales"
          title="Figure 10: Average Daily Sales per Customer"
          subtitle="Trend in average daily LSL revenue per customer over time"
          figureRef={setFigRef('avg-sales')}
          onExport={handleExportFigure('avg-sales', 'Avg_Sales_Trend')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <ComposedChart data={avgTrend} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} label={{ value: 'LSL/day/customer', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} label={{ value: 'Customers', angle: 90, position: 'insideRight', style: { fontSize: 10 } }} />
              <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              <Bar yAxisId="right" dataKey="customers" name="Customers" fill="#e2e8f0" radius={[4, 4, 0, 0]} />
              <Line yAxisId="left" dataKey="avg_daily_lsl_per_customer" name="Avg LSL/day" stroke="#d97706" strokeWidth={2.5} dot={{ r: 3 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* ================================================================ */}
      {/* 24-Hour Daily Load Profiles                                     */}
      {/* ================================================================ */}

      <Figure
        id="fig-daily-load-profiles"
        title="Figure 11: Average Daily Load Curves by Customer Type"
        subtitle="Average power demand (kW) by hour of day, derived from 10-minute meter readings"
        figureRef={setFigRef('daily-load-profiles')}
        onExport={handleExportFigure('daily-load-profiles', 'Daily_Load_Profiles')}
      >
        {/* Site filter dropdown */}
        <div className="flex items-center gap-3 mb-4">
          <label className="text-sm font-medium text-gray-600">Site:</label>
          <select
            value={profileSite}
            onChange={e => handleProfileSiteChange(e.target.value)}
            className="border rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none bg-white"
          >
            <option value="">All Sites</option>
            {SITE_CODES.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          {profileLoading && (
            <div className="animate-spin w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
          )}
        </div>

        {loadProfiles.length > 0 && loadProfileTypes.length > 0 ? (
          <ResponsiveContainer width="100%" height={400}>
            <LineChart data={loadProfiles} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="hour" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} label={{ value: 'Avg kW', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
              <Tooltip
                contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                formatter={(value: any, name: any) => [`${Number(value).toFixed(4)} kW`, name]}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              {loadProfileTypes.map((t, i) => (
                <Line
                  key={t}
                  type="monotone"
                  dataKey={t}
                  name={t}
                  stroke={COLORS[i % COLORS.length]}
                  strokeWidth={2.5}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-center py-8 text-gray-400 text-sm">
            {profileLoading ? 'Loading meter data...' : 'No meter reading data available for the selected site.'}
          </div>
        )}
      </Figure>

      {/* ================================================================ */}
      {/* Customer Type Analytics (consumption/sales totals)              */}
      {/* ================================================================ */}

      {loadCurves.length > 0 && (
        <>
          {/* Average Daily Consumption by Customer Type (bar chart) */}
          <Figure
            id="fig-consumption-by-type"
            title="Figure 11: Average Daily Consumption by Customer Type"
            subtitle={`Based on ${loadCurves.reduce((a, c) => a + c.customer_count, 0)} typed customers from uGridPLAN sync`}
            figureRef={setFigRef('consumption-by-type')}
            onExport={handleExportFigure('consumption-by-type', 'Consumption_By_Type')}
          >
            <ResponsiveContainer width="100%" height={350}>
              <BarChart
                data={loadCurves}
                margin={{ top: 5, right: 20, left: 0, bottom: 40 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="type" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 11 }} label={{ value: 'kWh/day/customer', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
                <Tooltip
                  contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                  formatter={(value: any, name: any) => {
                    if (name === 'avg_daily_kwh_per_customer') return [`${Number(value).toFixed(3)} kWh`, 'Avg Daily per Customer'];
                    if (name === 'customer_count') return [value, 'Customers'];
                    return [value, name];
                  }}
                />
                <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
                <Bar dataKey="avg_daily_kwh_per_customer" name="Avg kWh/day/customer" radius={[4, 4, 0, 0]}>
                  {loadCurves.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Figure>

          {/* Consumption share by type (pie-like horizontal bar) */}
          <Figure
            id="fig-consumption-share"
            title="Figure 12: Total Consumption by Customer Type"
            subtitle="Electricity consumed (kWh) and revenue (LSL) by end-user category"
            figureRef={setFigRef('consumption-share')}
            onExport={handleExportFigure('consumption-share', 'Consumption_Share_By_Type')}
          >
            <ResponsiveContainer width="100%" height={350}>
              <BarChart
                data={loadCurves}
                layout="vertical"
                margin={{ top: 5, right: 80, left: 40, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : String(Math.round(v))} />
                <YAxis type="category" dataKey="type" tick={{ fontSize: 12, fontWeight: 600 }} width={50} />
                <Tooltip
                  contentStyle={{ borderRadius: '8px', fontSize: '12px' }}
                  formatter={(value: any) => [`${Number(value).toLocaleString()} kWh`, 'Total Consumption']}
                />
                <Legend wrapperStyle={{ fontSize: '12px' }} />
                <Bar dataKey="total_kwh" name="Total kWh" radius={[0, 4, 4, 0]}>
                  {loadCurves.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            {/* Summary table below */}
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium text-gray-600">Type</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Customers</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Total kWh</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Total LSL</th>
                    <th className="px-3 py-2 text-right font-medium text-gray-600">Avg kWh/day</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {loadCurves.map((c, i) => (
                    <tr key={c.type}>
                      <td className="px-3 py-1.5">
                        <span className="inline-block w-3 h-3 rounded-sm mr-2" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
                        <span className="font-semibold">{c.type}</span>
                      </td>
                      <td className="px-3 py-1.5 text-right text-gray-700">{c.customer_count}</td>
                      <td className="px-3 py-1.5 text-right text-gray-700">{c.total_kwh.toLocaleString()}</td>
                      <td className="px-3 py-1.5 text-right text-gray-700">{c.total_lsl.toLocaleString()}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-gray-600">{c.avg_daily_kwh_per_customer.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Figure>
        </>
      )}

      {/* Quarterly consumption stacked by type */}
      {loadCurveQuarterly.length > 0 && loadCurveTypes.length > 0 && (
        <Figure
          id="fig-quarterly-by-type"
          title="Figure 13: Quarterly Consumption by Customer Type"
          subtitle="Stacked breakdown of electricity consumed per quarter by end-user category"
          figureRef={setFigRef('quarterly-by-type')}
          onExport={handleExportFigure('quarterly-by-type', 'Quarterly_By_Type')}
        >
          <ResponsiveContainer width="100%" height={350}>
            <BarChart data={loadCurveQuarterly} margin={{ top: 5, right: 20, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="quarter" angle={-35} textAnchor="end" tick={{ fontSize: 10 }} interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : String(Math.round(v))} />
              <Tooltip contentStyle={{ borderRadius: '8px', fontSize: '12px' }} />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }} />
              {loadCurveTypes.map((t, i) => (
                <Bar key={t} dataKey={t} stackId="type" fill={COLORS[i % COLORS.length]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </Figure>
      )}

      {/* Note if no type data */}
      {loadCurves.length === 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6 text-sm text-amber-800">
          <strong>Customer type analytics not yet available.</strong> The ACCDB meter table
          ("Copy Of tblmeter") may not contain customer type data for all meters.
          Check the Tables view to verify meter records exist.
        </div>
      )}

      {/* Footer note */}
      <div className="text-center text-xs text-gray-400 py-6 border-t mt-6">
        <p>Data source: 1PWR Customer Care Portal (ACCDB) + uGridPLAN connection metadata</p>
        <p className="mt-1">
          Generation data, site availability, and maintenance logs require integration with the
          Generation Data Platform and O&M ticketing system.
        </p>
      </div>
    </div>
  );
}
