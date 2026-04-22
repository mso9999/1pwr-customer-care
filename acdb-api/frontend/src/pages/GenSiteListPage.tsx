import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listGensiteSites, type GensiteSite } from '../lib/api';

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  } catch {
    return ts;
  }
}

function staleBadge(ts: string | null | undefined) {
  if (!ts) return <span className="text-xs px-2 py-0.5 rounded-full bg-gray-200 text-gray-600">no data</span>;
  const age = Date.now() - new Date(ts).getTime();
  if (age < 10 * 60 * 1000) return <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700">live</span>;
  if (age < 60 * 60 * 1000) return <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700">stale</span>;
  return <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700">offline</span>;
}

export default function GenSiteListPage() {
  const [sites, setSites] = useState<GensiteSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    listGensiteSites()
      .then(r => setSites(r.sites))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">Generation sites</h1>
          <p className="text-sm text-gray-500">
            Per-site dashboards backed by inverter vendor APIs and portal scrapers.
          </p>
        </div>
        <Link
          to="/gensite/commission"
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
        >
          Commission site
        </Link>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading…</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3">Code</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Country</th>
                <th className="px-4 py-3">Kind</th>
                <th className="px-4 py-3">Last reading</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {sites.map(s => (
                <tr key={s.code} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono font-semibold">{s.code}</td>
                  <td className="px-4 py-3">{s.display_name}</td>
                  <td className="px-4 py-3">{s.country}</td>
                  <td className="px-4 py-3 text-gray-600">{s.kind}</td>
                  <td className="px-4 py-3 text-gray-600">{formatTs(s.last_reading_ts)}</td>
                  <td className="px-4 py-3">{staleBadge(s.last_reading_ts)}</td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/gensite/${encodeURIComponent(s.code)}`}
                      className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                    >
                      Open →
                    </Link>
                  </td>
                </tr>
              ))}
              {sites.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                  No sites yet. <Link to="/gensite/commission" className="text-blue-600">Commission one</Link>.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
