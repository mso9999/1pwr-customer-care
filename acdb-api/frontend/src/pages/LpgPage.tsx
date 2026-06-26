import { useEffect, useState, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useCountry } from '../contexts/CountryContext';
import { listLpgSites, listAvailableLpgSites, downloadLpgReport, type LpgSiteSummary, type AvailableLpgSite } from '../lib/api';

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toISOString().slice(0, 10);
  } catch {
    return ts;
  }
}

function money(value: number | null | undefined, currency: string | null | undefined): string {
  if (value == null) return '—';
  return `${currency ? currency + ' ' : ''}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function runwayBadge(status: 'ok' | 'warn' | 'critical', days: number | null) {
  const label = days == null ? '—' : `${days}d`;
  if (status === 'critical') return <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700">{label}</span>;
  if (status === 'warn') return <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">{label}</span>;
  return <span className="text-gray-600">{label}</span>;
}

export default function LpgPage() {
  const { country } = useCountry();
  const { user } = useAuth();
  const navigate = useNavigate();
  const canEdit = user?.role === 'superadmin' || user?.role === 'onm_team';
  const [sites, setSites] = useState<LpgSiteSummary[]>([]);
  const [criticalCount, setCriticalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [days, setDays] = useState(30);
  const [exporting, setExporting] = useState(false);

  // "Add site" dropdown
  const [availableSites, setAvailableSites] = useState<AvailableLpgSite[]>([]);
  const [availableLoading, setAvailableLoading] = useState(false);
  const [selectedSite, setSelectedSite] = useState('');

  const countryFilter = country && country !== 'ALL' ? country : undefined;

  const load = useCallback(() => {
    setLoading(true);
    listLpgSites(countryFilter)
      .then((r) => {
        setSites(r.sites);
        setCriticalCount(r.critical_count);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [countryFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const totalKg = sites.reduce((acc, s) => acc + (s.kg_remaining || 0), 0);

  const handleExport = async () => {
    setExporting(true);
    try {
      await downloadLpgReport(countryFilter, days);
    } catch (e) {
      setError(String(e));
    } finally {
      setExporting(false);
    }
  };

  const loadAvailable = useCallback(async () => {
    setAvailableLoading(true);
    try {
      const r = await listAvailableLpgSites(countryFilter);
      setAvailableSites(r.sites);
    } catch {
      // Silently ignore — dropdown just stays empty.
    } finally {
      setAvailableLoading(false);
    }
  }, [countryFilter]);

  const goToSite = () => {
    if (!selectedSite) return;
    navigate(`/lpg/${encodeURIComponent(selectedSite)}`);
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">LPG tracking</h1>
          <p className="text-sm text-gray-500">
            Generator-fuel inventory, balance and cost per site. Capture deliveries and log generator runs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="border rounded-lg px-2 py-2 text-sm"
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
            <option value={365}>Last 365 days</option>
          </select>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
          >
            {exporting ? 'Exporting…' : 'Export report CSV'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-white rounded-xl shadow-sm border p-4">
          <div className="text-xs uppercase tracking-wide text-gray-500">Sites tracked</div>
          <div className="text-2xl font-semibold mt-1">{sites.length}</div>
        </div>
        <div className={`rounded-xl shadow-sm border p-4 ${criticalCount > 0 ? 'bg-red-50 border-red-200' : 'bg-white'}`}>
          <div className="text-xs uppercase tracking-wide text-gray-500">Critical (last cylinder)</div>
          <div className={`text-2xl font-semibold mt-1 ${criticalCount > 0 ? 'text-red-600' : ''}`}>{criticalCount}</div>
        </div>
        <div className="bg-white rounded-xl shadow-sm border p-4">
          <div className="text-xs uppercase tracking-wide text-gray-500">Total LPG remaining</div>
          <div className="text-2xl font-semibold mt-1">{totalKg.toLocaleString(undefined, { maximumFractionDigits: 0 })} kg</div>
        </div>
      </div>

      {/* Add site to LPG tracking (onm_team / superadmin only) */}
      {canEdit && (
        <div className="bg-white rounded-xl shadow-sm border p-4 mb-6 flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-medium text-gray-600 mb-1">Add a site to LPG tracking</label>
            <select
              value={selectedSite}
              onFocus={() => { if (availableSites.length === 0) loadAvailable(); }}
              onChange={(e) => setSelectedSite(e.target.value)}
              className="border rounded-lg px-3 py-2 text-sm w-full"
            >
              <option value="">— select a site —</option>
              {availableSites.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.code.toUpperCase()} — {s.display_name} ({s.country})
                </option>
              ))}
            </select>
            {availableLoading && <span className="text-xs text-gray-400 mt-1">Loading sites…</span>}
            {!availableLoading && availableSites.length === 0 && selectedSite === '' && (
              <span className="text-xs text-gray-400 mt-1">All sites are already enrolled in LPG tracking.</span>
            )}
          </div>
          <button
            onClick={goToSite}
            disabled={!selectedSite}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium"
          >
            Open site
          </button>
        </div>
      )}

      {loading && <p className="text-sm text-gray-500">Loading…</p>}
      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}

      {!loading && !error && sites.length === 0 && (
        <div className="bg-white rounded-xl shadow-sm border p-8 text-center text-gray-500">
          No LPG data yet. Open a site and record its first delivery to begin tracking.
        </div>
      )}

      {!loading && !error && sites.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Site</th>
                <th className="px-4 py-3">Country</th>
                <th className="px-4 py-3 text-right">Cylinders left</th>
                <th className="px-4 py-3 text-right">kg left</th>
                <th className="px-4 py-3 text-right">Days left</th>
                <th className="px-4 py-3 text-right">Value left</th>
                <th className="px-4 py-3 text-right">Used (30d)</th>
                <th className="px-4 py-3 text-right">Cost (30d)</th>
                <th className="px-4 py-3">Last delivery</th>
                <th className="px-4 py-3">Last run</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {sites.map((s) => (
                <tr key={s.code} className={`hover:bg-gray-50 ${s.is_critical ? 'bg-red-50' : ''}`}>
                  <td className="px-4 py-3">
                    <Link to={`/lpg/${encodeURIComponent(s.code)}`} className="font-mono font-semibold text-blue-700 hover:underline">
                      {s.code}
                    </Link>
                    <div className="text-xs text-gray-500">{s.display_name}</div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{s.country}</td>
                  <td className="px-4 py-3 text-right">
                    <span className="font-semibold">{s.cylinders_remaining}</span>
                    {s.is_critical && (
                      <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700">critical</span>
                    )}
                    {s.open_runs > 0 && (
                      <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700">running</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-600">{s.kg_remaining.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right">{runwayBadge(s.runway_status, s.days_remaining)}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{money(s.value_remaining, s.currency)}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{s.cylinders_consumed_30d}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{money(s.cost_30d, s.currency)}</td>
                  <td className="px-4 py-3 text-gray-600">{fmtTs(s.last_delivery_at)}</td>
                  <td className="px-4 py-3 text-gray-600">{fmtTs(s.last_run_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <Link to={`/lpg/${encodeURIComponent(s.code)}`} className="text-blue-600 hover:underline text-sm">
                      Open →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
