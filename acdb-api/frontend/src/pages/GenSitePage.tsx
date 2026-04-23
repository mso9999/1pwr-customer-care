import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import {
  getGensiteSite,
  getGensiteSeries,
  listGensiteAlarms,
  ackGensiteAlarm,
  openUgpTicketForAlarm,
  verifyGensiteCredential,
  type GensiteSiteDetail,
  type GensiteSeriesResponse,
  type GensiteAlarm,
} from '../lib/api';

function formatTs(ts: string | null | undefined, withSeconds = false): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    const iso = d.toISOString().replace('T', ' ');
    return (withSeconds ? iso.slice(0, 19) : iso.slice(0, 16)) + ' UTC';
  } catch {
    return ts;
  }
}

function num(v: number | null | undefined, digits = 2, unit = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}${unit ? ' ' + unit : ''}`;
}

const CHART_METRICS: Array<{ key: string; label: string; color: string }> = [
  { key: 'pv_kw',           label: 'PV (kW)',   color: '#eab308' },
  { key: 'ac_kw',           label: 'Load (kW)', color: '#0ea5e9' },
  { key: 'battery_kw',      label: 'Batt (kW)', color: '#22c55e' },
  { key: 'battery_soc_pct', label: 'SoC (%)',   color: '#a855f7' },
];

export default function GenSitePage() {
  const { code = '' } = useParams<{ code: string }>();
  const [params] = useSearchParams();
  const returnTo = params.get('return_to');

  const [detail, setDetail] = useState<GensiteSiteDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyMsg, setVerifyMsg] = useState<string>('');
  const [alarms, setAlarms] = useState<GensiteAlarm[]>([]);
  const [alarmState, setAlarmState] = useState<'open' | 'all'>('open');
  const [seriesWindow, setSeriesWindow] = useState<number>(24);
  const [series, setSeries] = useState<Record<string, GensiteSeriesResponse>>({});
  const [busyAlarm, setBusyAlarm] = useState<number | null>(null);

  async function reload() {
    try {
      setLoading(true);
      const d = await getGensiteSite(code);
      setDetail(d);
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function reloadAlarms() {
    try {
      const r = await listGensiteAlarms(code, alarmState);
      setAlarms(r.alarms);
    } catch (e) {
      console.warn('alarms fetch failed', e);
    }
  }

  async function reloadSeries() {
    try {
      const results = await Promise.all(
        CHART_METRICS.map(m => getGensiteSeries(code, m.key, seriesWindow).catch(() => null)),
      );
      const out: Record<string, GensiteSeriesResponse> = {};
      results.forEach((r, i) => { if (r) out[CHART_METRICS[i].key] = r; });
      setSeries(out);
    } catch (e) {
      console.warn('series fetch failed', e);
    }
  }

  useEffect(() => { if (code) reload(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [code]);
  useEffect(() => { if (code) reloadAlarms(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [code, alarmState]);
  useEffect(() => { if (code) reloadSeries(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [code, seriesWindow]);

  async function runVerify(vendor: string, backend: string) {
    const key = `${vendor}/${backend}`;
    setVerifying(key);
    setVerifyMsg('');
    try {
      const r = await verifyGensiteCredential(code, vendor, backend);
      setVerifyMsg(`${key}: ${r.ok ? '✅' : '❌'} ${r.message}`);
      await reload();
    } catch (e) {
      setVerifyMsg(`${key}: error — ${String(e)}`);
    } finally {
      setVerifying(null);
    }
  }

  async function ack(alarm: GensiteAlarm) {
    setBusyAlarm(alarm.id);
    try {
      await ackGensiteAlarm(alarm.id);
      await reloadAlarms();
    } catch (e) {
      alert(`Ack failed: ${e}`);
    } finally {
      setBusyAlarm(null);
    }
  }

  async function openTicket(alarm: GensiteAlarm) {
    setBusyAlarm(alarm.id);
    try {
      const r = await openUgpTicketForAlarm(alarm.id, {
        fault_description: alarm.vendor_msg || alarm.vendor_code || 'Inverter alarm',
      });
      await reloadAlarms();
      setVerifyMsg(`Ticket created: ${r.ticket_id_ugp} (internal id ${r.ticket_pg_id})`);
    } catch (e) {
      alert(`Ticket creation failed: ${e}`);
    } finally {
      setBusyAlarm(null);
    }
  }

  // Combine all four series into one chart dataset keyed by timestamp.
  const chartData = useMemo(() => {
    const byTs: Record<string, Record<string, number | string>> = {};
    for (const m of CHART_METRICS) {
      const s = series[m.key];
      if (!s) continue;
      // Aggregate across equipment with a simple sum for PV/load/batt, avg for SoC.
      const agg: Record<string, { sum: number; n: number }> = {};
      for (const p of s.points) {
        const k = p.ts;
        if (!agg[k]) agg[k] = { sum: 0, n: 0 };
        agg[k].sum += p.value;
        agg[k].n += 1;
      }
      for (const [ts, { sum, n }] of Object.entries(agg)) {
        if (!byTs[ts]) byTs[ts] = { ts: new Date(ts).toISOString().slice(11, 16) };
        byTs[ts][m.key] = m.key === 'battery_soc_pct' ? sum / n : sum;
      }
    }
    return Object.values(byTs).sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  }, [series]);

  if (loading && !detail) return <div className="p-6 text-sm text-gray-500">Loading…</div>;
  if (error) return <div className="p-6 text-sm text-red-600">{error}</div>;
  if (!detail) return null;

  const { site, equipment, credentials, latest_readings } = detail;
  const readingByEq = new Map(latest_readings.map(r => [r.equipment_id, r]));
  const openAlarmCount = alarms.filter(a => !a.cleared_at && !a.acknowledged_at).length;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {site.country} · {site.kind}
          </div>
          <h1 className="text-2xl font-semibold">
            {site.display_name} <span className="text-gray-400 font-mono">({site.code})</span>
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {site.district && <>District: {site.district} · </>}
            {site.commissioned_at && <>Commissioned {formatTs(site.commissioned_at)}</>}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/gensite" className="px-3 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm">All sites</Link>
          {returnTo && (
            <a href={returnTo} className="px-3 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg text-sm">
              ← Back to UGP
            </a>
          )}
        </div>
      </div>

      {verifyMsg && <div className="text-sm bg-gray-50 border rounded-lg px-3 py-2">{verifyMsg}</div>}

      {/* Alarms */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">
            Alarms {alarmState === 'open' && openAlarmCount > 0 && (
              <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                {openAlarmCount} open
              </span>
            )}
          </h2>
          <div className="flex gap-1 text-xs">
            <button
              onClick={() => setAlarmState('open')}
              className={`px-2 py-1 rounded ${alarmState === 'open' ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}
            >Open</button>
            <button
              onClick={() => setAlarmState('all')}
              className={`px-2 py-1 rounded ${alarmState === 'all' ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}
            >All</button>
          </div>
        </div>
        {alarms.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-lg p-4 bg-gray-50">
            {alarmState === 'open' ? 'No open alarms.' : 'No alarms recorded.'}
          </div>
        ) : (
          <div className="bg-white border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-3 py-2">Severity</th>
                  <th className="px-3 py-2">Raised</th>
                  <th className="px-3 py-2">Vendor · code</th>
                  <th className="px-3 py-2">Message</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {alarms.map(a => (
                  <tr key={a.id}>
                    <td className="px-3 py-2">
                      <SeverityBadge value={a.severity} />
                    </td>
                    <td className="px-3 py-2 text-xs">{formatTs(a.raised_at, true)}</td>
                    <td className="px-3 py-2 text-xs font-mono">{a.vendor || '—'}{a.vendor_code ? ` · ${a.vendor_code}` : ''}</td>
                    <td className="px-3 py-2 text-xs">{a.vendor_msg || '—'}</td>
                    <td className="px-3 py-2 text-xs">
                      {a.cleared_at ? <span className="text-gray-500">cleared</span>
                        : a.acknowledged_at ? <span className="text-gray-500">ack'd by {a.acknowledged_by}</span>
                        : <span className="text-red-600">open</span>}
                      {a.ticket_id_ugp && <div className="text-gray-500">ticket: {a.ticket_id_ugp}</div>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex gap-1 justify-end">
                        {!a.acknowledged_at && !a.cleared_at && (
                          <button
                            disabled={busyAlarm === a.id}
                            onClick={() => ack(a)}
                            className="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded disabled:opacity-50"
                          >Ack</button>
                        )}
                        {!a.ticket_id_ugp && (
                          <button
                            disabled={busyAlarm === a.id}
                            onClick={() => openTicket(a)}
                            className="px-2 py-1 text-xs bg-blue-50 hover:bg-blue-100 text-blue-700 rounded disabled:opacity-50"
                          >Open ticket</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Chart */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Telemetry</h2>
          <div className="flex gap-1 text-xs">
            {[6, 24, 24 * 7, 24 * 30].map(h => (
              <button
                key={h}
                onClick={() => setSeriesWindow(h)}
                className={`px-2 py-1 rounded ${seriesWindow === h ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}
              >
                {h === 6 ? '6h' : h === 24 ? '24h' : h === 168 ? '7d' : '30d'}
              </button>
            ))}
          </div>
        </div>
        <div className="bg-white border rounded-xl p-3" style={{ height: 280 }}>
          {chartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-sm text-gray-500">
              No telemetry yet — the gensite poller will populate this chart as readings arrive.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="ts" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {CHART_METRICS.map(m => (
                  <Area
                    key={m.key}
                    type="monotone"
                    dataKey={m.key}
                    name={m.label}
                    stroke={m.color}
                    fill={m.color}
                    fillOpacity={0.15}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>

      {/* Equipment + live tiles */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Installed equipment</h2>
        {equipment.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-lg p-4 bg-gray-50">
            No equipment registered yet.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {equipment.map(eq => {
              const r = readingByEq.get(eq.id);
              return (
                <div key={eq.id} className="border rounded-xl p-4 bg-white shadow-sm">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="text-xs uppercase text-gray-500">{eq.kind}</div>
                      <div className="font-semibold">{eq.vendor}{eq.model ? ` · ${eq.model}` : ''}</div>
                      <div className="text-xs text-gray-500 font-mono">{eq.serial || '—'}</div>
                    </div>
                    <div className="text-right text-xs text-gray-500">
                      {eq.nameplate_kw && <div>{eq.nameplate_kw} kW</div>}
                      {eq.nameplate_kwh && <div>{eq.nameplate_kwh} kWh</div>}
                      {eq.firmware_version && <div>fw {eq.firmware_version}</div>}
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
                    <Tile label="AC" value={num(r?.ac_kw ?? null, 2, 'kW')} />
                    <Tile label="PV"  value={num(r?.pv_kw ?? null, 2, 'kW')} />
                    <Tile label="SoC" value={num(r?.battery_soc_pct ?? null, 1, '%')} />
                    <Tile label="Batt" value={num(r?.battery_kw ?? null, 2, 'kW')} />
                    <Tile label="Grid" value={num(r?.grid_kw ?? null, 2, 'kW')} />
                    <Tile label="V"    value={num(r?.ac_v_avg ?? null, 1, 'V')} />
                  </div>
                  <div className="mt-2 text-xs text-gray-500">
                    {r ? `Updated ${formatTs(r.ts_utc, true)}` : 'No reading yet'}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Credentials */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Vendor credentials</h2>
        {credentials.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-lg p-4 bg-gray-50">
            No credentials stored. Commission the site to add them.
          </div>
        ) : (
          <div className="bg-white border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Vendor</th>
                  <th className="px-4 py-2">Backend</th>
                  <th className="px-4 py-2">User</th>
                  <th className="px-4 py-2">Secret</th>
                  <th className="px-4 py-2">Last verified</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {credentials.map(c => (
                  <tr key={c.id}>
                    <td className="px-4 py-2 font-medium">{c.vendor}</td>
                    <td className="px-4 py-2">{c.backend}</td>
                    <td className="px-4 py-2 font-mono text-xs">{c.username_masked || '—'}</td>
                    <td className="px-4 py-2 text-xs">
                      {c.has_secret ? '🔒 stored' : '—'}
                      {c.has_api_key ? ' · key' : ''}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {c.last_verified_ok === null ? '—'
                        : c.last_verified_ok ? <span className="text-green-700">✅ {formatTs(c.last_verified_at)}</span>
                        : <span className="text-red-700" title={c.last_verify_error || ''}>❌ {formatTs(c.last_verified_at)}</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => runVerify(c.vendor, c.backend)}
                        disabled={verifying === `${c.vendor}/${c.backend}`}
                        className="px-3 py-1 bg-gray-100 hover:bg-gray-200 rounded text-xs disabled:opacity-50"
                      >
                        {verifying === `${c.vendor}/${c.backend}` ? 'Testing…' : 'Test connection'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-gray-400 text-center">
        Telemetry is polled by <code>cc-gensite-poll.timer</code> on the CC host; this page reads 1PDB only
        and never renders vendor credentials. Sources:&nbsp;
        {credentials.map(c => `${c.vendor}/${c.backend}`).join(' · ') || 'none yet'}.
      </p>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-lg px-2 py-1.5">
      <div className="text-[10px] uppercase text-gray-500">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}

function SeverityBadge({ value }: { value: string }) {
  const v = (value || '').toLowerCase();
  const cls =
    v === 'critical' ? 'bg-red-100 text-red-700' :
    v === 'warning' ? 'bg-yellow-100 text-yellow-700' :
    'bg-gray-100 text-gray-600';
  return <span className={`text-xs px-2 py-0.5 rounded-full ${cls}`}>{v || 'info'}</span>;
}
