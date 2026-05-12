import { useEffect, useState } from 'react';
import {
  getOnboardingDashboardSummary,
  getOnboardingMonthlyDashboard,
  type OnboardingDashboardSummary,
  type OnboardingMonthlySite,
} from '../lib/api';

const SITE_CODES = ['ALL', 'MAT', 'TLH', 'MAK', 'SHG', 'MAS', 'SEH', 'KET', 'LSB'];

export default function OnboardingDashboardPage() {
  const [site, setSite] = useState('ALL');
  const [summary, setSummary] = useState<OnboardingDashboardSummary | null>(null);
  const [monthly, setMonthly] = useState<OnboardingMonthlySite[]>([]);
  const [year, setYear] = useState(new Date().getFullYear());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getOnboardingDashboardSummary(site === 'ALL' ? undefined : site),
      getOnboardingMonthlyDashboard(year),
    ])
      .then(([summaryRes, monthlyRes]) => {
        if (cancelled) return;
        setSummary(summaryRes);
        setMonthly(monthlyRes.sites);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [site, year]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold text-gray-800">Onboarding dashboard</h1>
        <div className="flex items-center gap-2">
          <select
            value={site}
            onChange={e => setSite(e.target.value)}
            className="text-sm border rounded-lg px-3 py-2"
          >
            {SITE_CODES.map(code => (
              <option key={code} value={code}>{code}</option>
            ))}
          </select>
          <input
            type="number"
            value={year}
            onChange={e => setYear(Number(e.target.value))}
            className="w-24 text-sm border rounded-lg px-3 py-2"
          />
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="space-y-6">
          {summary && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {[
                ['Registered', summary.registered],
                ['Connected', summary.connected],
                ['Pending', summary.pending],
                ['Meter installed', summary.meter_installed],
                ['Commissioned', summary.commissioned],
              ].map(([label, value]) => (
                <div key={label} className="bg-white rounded-xl border p-4">
                  <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
                  <p className="text-2xl font-bold text-gray-800 mt-1">{value}</p>
                </div>
              ))}
            </div>
          )}

          <div className="bg-white rounded-xl border overflow-hidden">
            <div className="px-4 py-3 border-b text-sm font-semibold text-gray-600">
              Monthly commissioned ({year})
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                    <th className="px-4 py-3">Site</th>
                    <th className="px-4 py-3">Months</th>
                    <th className="px-4 py-3 text-right">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {monthly.map(row => {
                    const total = row.months.reduce((sum, m) => sum + m.commissioned, 0);
                    const monthText = row.months
                      .map(m => `${m.month.slice(0, 7)}: ${m.commissioned}`)
                      .join(', ');
                    return (
                      <tr key={row.site} className="border-t border-gray-100">
                        <td className="px-4 py-3 font-medium">{row.site}</td>
                        <td className="px-4 py-3 text-gray-600">{monthText || '—'}</td>
                        <td className="px-4 py-3 text-right font-semibold">{total}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
