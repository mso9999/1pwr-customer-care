import { useState } from 'react';
import { getMakConnectivity, type MakConnectivityResponse } from '../lib/api';

const STATUS_CONFIG: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  online:           { bg: 'bg-green-50',  text: 'text-green-800',  dot: 'bg-green-500',  label: 'Online' },
  stale:            { bg: 'bg-yellow-50', text: 'text-yellow-800', dot: 'bg-yellow-500', label: 'Stale' },
  offline:          { bg: 'bg-orange-50', text: 'text-orange-800', dot: 'bg-orange-500', label: 'Offline' },
  offline_extended: { bg: 'bg-red-50',    text: 'text-red-700',    dot: 'bg-red-500',    label: 'Offline (30h+)' },
  unknown:          { bg: 'bg-gray-50',   text: 'text-gray-600',   dot: 'bg-gray-400',   label: 'Never Reported' },
};

function fmtHours(h: number | null): string {
  if (h == null) return '--';
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '--';
  try {
    const d = new Date(iso);
    return d.toLocaleString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border p-4 flex flex-col items-center">
      <span className={`text-2xl font-bold ${color}`}>{value}</span>
      <span className="text-xs text-gray-500 mt-1">{label}</span>
    </div>
  );
}

export default function MeterConnectivityPage() {
  const [data, setData] = useState<MakConnectivityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('all');

  const runCheck = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getMakConnectivity();
      setData(result);
    } catch (e: any) {
      setError(e.message || 'Failed to run connectivity check');
    } finally {
      setLoading(false);
    }
  };

  const filteredMeters = data?.meters.filter(m => {
    if (filter === 'all') return true;
    if (filter === 'problem') return m.status === 'offline' || m.status === 'offline_extended' || m.status === 'unknown';
    return m.status === filter;
  }) ?? [];

  const s = data?.summary;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-800">MAK Meter Connectivity</h1>
          <p className="text-sm text-gray-500 mt-0.5">Real-time status from 1PDB + AWS IoT Fleet Indexing</p>
        </div>
        <button
          onClick={runCheck}
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium text-sm hover:bg-blue-700 disabled:opacity-50 transition"
        >
          {loading ? 'Checking...' : 'Run Connectivity Check'}
        </button>
      </div>

      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-red-700 text-sm">
          {error}
        </div>
      )}

      {data?.iot_error && (
        <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-yellow-800 text-sm">
          <span className="font-semibold">AWS IoT warning:</span> {data.iot_error}
        </div>
      )}

      {s && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <SummaryCard label="Total Meters" value={s.total_meters} color="text-gray-800" />
          <SummaryCard label="Online" value={s.online} color="text-green-600" />
          <SummaryCard label="Stale" value={s.stale} color="text-yellow-600" />
          <SummaryCard label="Offline" value={s.offline} color="text-red-600" />
          <SummaryCard label="Never Reported" value={s.never_reported} color="text-gray-500" />
        </div>
      )}

      {data && (
        <>
          <div className="flex items-center gap-2 mb-4 text-sm">
            <span className="text-gray-500">Filter:</span>
            {['all', 'online', 'stale', 'problem'].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 rounded-lg font-medium capitalize transition ${
                  filter === f
                    ? 'bg-blue-600 text-white'
                    : 'bg-white border text-gray-600 hover:bg-gray-50'
                }`}
              >
                {f === 'problem' ? 'Problem Meters' : f}
              </button>
            ))}
            {data.checked_at && (
              <span className="ml-auto text-xs text-gray-400">
                Checked at {fmtDate(data.checked_at)}
              </span>
            )}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block bg-white rounded-xl shadow-sm border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-3">Meter ID</th>
                  <th className="text-left px-4 py-3">Account</th>
                  <th className="text-left px-4 py-3">Status</th>
                  <th className="text-left px-4 py-3">Last Seen</th>
                  <th className="text-left px-4 py-3">Hours Ago</th>
                  <th className="text-left px-4 py-3">Firmware</th>
                  <th className="text-left px-4 py-3">Energy (kWh)</th>
                  <th className="text-left px-4 py-3">IoT Connected</th>
                  <th className="text-left px-4 py-3">Disconnect Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredMeters.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-center text-gray-400 py-8">No meters match this filter</td>
                  </tr>
                ) : filteredMeters.map(m => {
                  const cfg = STATUS_CONFIG[m.status] ?? STATUS_CONFIG.unknown;
                  return (
                    <tr key={m.meter_id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-mono text-gray-800">{m.meter_id}</td>
                      <td className="px-4 py-3 text-gray-600">{m.account || '--'}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
                          <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
                          {cfg.label}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-600">{fmtDate(m.last_seen)}</td>
                      <td className="px-4 py-3 text-gray-600">{fmtHours(m.hours_ago)}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-500">{m.firmware || '--'}</td>
                      <td className="px-4 py-3 text-gray-600">{m.energy_kwh != null ? m.energy_kwh.toFixed(1) : '--'}</td>
                      <td className="px-4 py-3">
                        {m.iot_connected === true && <span className="text-green-600 font-medium">Yes</span>}
                        {m.iot_connected === false && <span className="text-red-600 font-medium">No</span>}
                        {m.iot_connected == null && <span className="text-gray-400">--</span>}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{m.iot_disconnect_reason || '--'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {filteredMeters.length === 0 ? (
              <p className="text-center text-gray-400 py-8">No meters match this filter</p>
            ) : filteredMeters.map(m => {
              const cfg = STATUS_CONFIG[m.status] ?? STATUS_CONFIG.unknown;
              return (
                <div key={m.meter_id} className="bg-white rounded-xl shadow-sm border p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-mono font-medium text-gray-800 text-sm">{m.meter_id}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{m.account || '--'}</p>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
                      <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
                      {cfg.label}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 mt-3 text-xs">
                    <div>
                      <span className="text-gray-400">Last seen:</span>
                      <span className="text-gray-700 ml-1">{fmtDate(m.last_seen)}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">Hours ago:</span>
                      <span className="text-gray-700 ml-1">{fmtHours(m.hours_ago)}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">Firmware:</span>
                      <span className="text-gray-700 ml-1 font-mono">{m.firmware || '--'}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">IoT:</span>
                      <span className={`ml-1 font-medium ${m.iot_connected ? 'text-green-600' : 'text-red-600'}`}>
                        {m.iot_connected === true ? 'Connected' : m.iot_connected === false ? 'Disconnected' : '--'}
                      </span>
                    </div>
                  </div>
                  {m.iot_disconnect_reason && (
                    <p className="text-xs text-gray-400 mt-2">Disconnect: {m.iot_disconnect_reason}</p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {!data && !loading && !error && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-sm">Click "Run Connectivity Check" to query 1PDB and AWS IoT for MAK meter status.</p>
        </div>
      )}
    </div>
  );
}
