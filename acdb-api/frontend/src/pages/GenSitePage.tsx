import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid, LineChart, Line,
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
  if (v === null || v === undefined) return '—';
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(digits)}${unit ? ' ' + unit : ''}`;
}

function clampPct(v: number): number {
  if (!Number.isFinite(v)) return 0;
  if (v < 0) return 0;
  if (v > 100) return 100;
  return v;
}

const FLOW_CSS = `
.flow-line { stroke-dasharray: 8 8; }
.flow-forward { animation: flow-forward 1.1s linear infinite; }
.flow-reverse { animation: flow-reverse 1.1s linear infinite; }
@keyframes flow-forward { from { stroke-dashoffset: 0; } to { stroke-dashoffset: -24; } }
@keyframes flow-reverse { from { stroke-dashoffset: 0; } to { stroke-dashoffset: 24; } }
`;

const POWER_AND_STATE_METRICS: Array<{ key: string; label: string; color: string; yAxisId: 'power' | 'pct' }> = [
  { key: 'pv_kw',           label: 'PV (kW)',      color: '#eab308', yAxisId: 'power' },
  { key: 'ac_kw',           label: 'Load (kW)',    color: '#0ea5e9', yAxisId: 'power' },
  { key: 'battery_kw',      label: 'Battery (kW)', color: '#22c55e', yAxisId: 'power' },
  { key: 'genset_kw',       label: 'Genset (kW)',  color: '#ef4444', yAxisId: 'power' },
  { key: 'grid_kw',         label: 'Grid (kW)',    color: '#f97316', yAxisId: 'power' },
  { key: 'battery_soc_pct', label: 'SoC (%)',      color: '#a855f7', yAxisId: 'pct' },
];

const ENERGY_METRICS: Array<{ key: string; label: string; color: string }> = [
  { key: 'ac_kwh_total', label: 'AC Energy Total (kWh)', color: '#6366f1' },
];

function isGensetEquipment(kind: string | null | undefined, role: string | null | undefined): boolean {
  const k = (kind || '').toLowerCase();
  const r = (role || '').toLowerCase();
  return k.includes('genset') || k.includes('generator') || k.includes('diesel')
    || r.includes('genset') || r.includes('generator') || r.includes('diesel');
}

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
      const sourcePowerMetrics = POWER_AND_STATE_METRICS.filter(m => m.key !== 'genset_kw');
      const allMetrics = [...sourcePowerMetrics, ...ENERGY_METRICS];
      const results = await Promise.all(
        allMetrics.map(m => getGensiteSeries(code, m.key, seriesWindow).catch(() => null)),
      );
      const out: Record<string, GensiteSeriesResponse> = {};
      results.forEach((r, i) => { if (r) out[allMetrics[i].key] = r; });
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

  const buildChartData = (metrics: Array<{ key: string }>) => {
    const byTs: Record<string, Record<string, number | string>> = {};
    for (const m of metrics) {
      const s = series[m.key];
      if (!s) continue;
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
  };

  const hasExplicitGenset = useMemo(() => {
    const eq = detail?.equipment ?? [];
    return eq.some(item => isGensetEquipment(item.kind, item.role));
  }, [detail]);

  // OEM-agnostic series with derived genset channel when genset is wired through inverter AC input.
  const powerChartData = useMemo(() => {
    const baseMetrics = POWER_AND_STATE_METRICS.filter(m => m.key !== 'genset_kw');
    const data = buildChartData(baseMetrics) as Array<Record<string, number | string>>;
    return data.map(row => {
      const grid = typeof row.grid_kw === 'number' ? row.grid_kw : null;
      const derived = !hasExplicitGenset && grid !== null && grid > 0 ? grid : 0;
      return { ...row, genset_kw: derived };
    });
  }, [series, hasExplicitGenset]);
  const energyChartData = useMemo(() => buildChartData(ENERGY_METRICS), [series]);

  if (loading && !detail) return <div className="p-6 text-sm text-gray-500">Loading…</div>;
  if (error) return <div className="p-6 text-sm text-red-600">{error}</div>;
  if (!detail) return null;

  const { site, equipment, credentials, latest_readings } = detail;
  const readingByEq = new Map(latest_readings.map(r => [r.equipment_id, r]));
  const openAlarmCount = alarms.filter(a => !a.cleared_at && !a.acknowledged_at).length;
  const latestTsMs = latest_readings.reduce((maxTs, r) => {
    const t = Date.parse(r.ts_utc);
    return Number.isFinite(t) && t > maxTs ? t : maxTs;
  }, 0);
  const freshnessMin = latestTsMs > 0 ? Math.max(0, Math.round((Date.now() - latestTsMs) / 60000)) : null;
  const isLive = freshnessMin !== null && freshnessMin <= 20;
  const flowNow = (() => {
    const eqById = new Map(equipment.map(eq => [eq.id, eq]));
    let pv = 0;
    let load = 0;
    let battery = 0;
    let grid = 0;
    let genset = 0;
    let socSum = 0;
    let socCount = 0;
    for (const r of latest_readings) {
      const eq = eqById.get(r.equipment_id);
      if (typeof r.pv_kw === 'number') pv += r.pv_kw;
      if (typeof r.ac_kw === 'number') {
        load += r.ac_kw;
        if (isGensetEquipment(eq?.kind, eq?.role)) genset += r.ac_kw;
      }
      if (typeof r.battery_kw === 'number') battery += r.battery_kw;
      if (typeof r.grid_kw === 'number') grid += r.grid_kw;
      if (typeof r.battery_soc_pct === 'number') {
        socSum += r.battery_soc_pct;
        socCount += 1;
      }
    }
    const derivedGenset = !hasExplicitGenset && grid > 0 ? grid : 0;
    return {
      pv,
      load,
      battery,
      grid,
      genset: genset > 0 ? genset : derivedGenset,
      soc: socCount > 0 ? socSum / socCount : null,
    };
  })();
  const energyNow = (() => {
    const s = series.ac_kwh_total?.points || [];
    if (s.length < 2) return null;
    const vals = [...s].sort((a, b) => a.ts.localeCompare(b.ts));
    const delta = vals[vals.length - 1].value - vals[0].value;
    return Number.isFinite(delta) ? Math.max(0, delta) : null;
  })();
  const pvToLoadPct = clampPct(flowNow.load > 0 ? (flowNow.pv / flowNow.load) * 100 : 0);
  const batteryDischargeKw = flowNow.battery < 0 ? Math.abs(flowNow.battery) : 0;
  const batteryChargeKw = flowNow.battery > 0 ? flowNow.battery : 0;
  const batteryToLoadPct = clampPct(flowNow.load > 0 ? (batteryDischargeKw / flowNow.load) * 100 : 0);
  const gensetToLoadPct = clampPct(flowNow.load > 0 ? (flowNow.genset / flowNow.load) * 100 : 0);
  const gridImportKw = flowNow.grid > 0 ? flowNow.grid : 0;
  const gridExportKw = flowNow.grid < 0 ? Math.abs(flowNow.grid) : 0;
  const pvActive = flowNow.pv > 0.05;
  const gensetActive = flowNow.genset > 0.05;
  const loadActive = flowNow.load > 0.05;
  const batteryActive = Math.abs(flowNow.battery) > 0.05;
  const batteryDischarging = flowNow.battery < -0.05;
  const gridImportActive = gridImportKw > 0.05;
  const gridExportActive = gridExportKw > 0.05;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <style>{FLOW_CSS}</style>
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

      <section className="bg-white border rounded-xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm">
            <span className={`px-2 py-0.5 rounded-full text-xs ${isLive ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
              {isLive ? 'Live' : 'Stale'}
            </span>
            <span className="text-gray-500">
              Last update {latestTsMs ? formatTs(new Date(latestTsMs).toISOString(), true) : '—'}
            </span>
          </div>
          <div className="text-xs text-gray-500">
            {freshnessMin === null ? 'No telemetry yet' : `${freshnessMin} min freshness`}
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-3">
          <Tile label="Production now" value={num(flowNow.pv, 2, 'kW')} />
          <Tile label="Load now" value={num(flowNow.load, 2, 'kW')} />
          <Tile label="Battery SoC" value={num(flowNow.soc, 1, '%')} />
          <Tile label="Genset now" value={num(flowNow.genset, 2, 'kW')} />
          <Tile label="Energy in window" value={num(energyNow, 2, 'kWh')} />
        </div>
      </section>

      {verifyMsg && <div className="text-sm bg-gray-50 border rounded-lg px-3 py-2">{verifyMsg}</div>}

      <section>
        <div className="grid md:grid-cols-3 gap-3">
          <div className="md:col-span-2 border rounded-xl p-4 bg-white">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">Power flow now</h2>
              <div className="text-xs text-gray-500">Animated direction from normalized channels</div>
            </div>
            <div className="relative border rounded-xl bg-gray-50 p-2">
              <svg viewBox="0 0 860 360" className="w-full h-[260px]">
                <defs>
                  <marker id="arrowHead" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
                  </marker>
                </defs>

                {/* static guide lines */}
                <line x1="180" y1="90" x2="410" y2="180" stroke="#d1d5db" strokeWidth="3" />
                <line x1="180" y1="260" x2="410" y2="180" stroke="#d1d5db" strokeWidth="3" />
                <line x1="410" y1="180" x2="660" y2="180" stroke="#d1d5db" strokeWidth="3" />
                <line x1="410" y1="300" x2="410" y2="180" stroke="#d1d5db" strokeWidth="3" />
                <line x1="410" y1="50" x2="410" y2="180" stroke="#d1d5db" strokeWidth="3" />

                {/* animated flow lines */}
                {pvActive && (
                  <line
                    x1="180"
                    y1="90"
                    x2="410"
                    y2="180"
                    stroke="#eab308"
                    strokeWidth="6"
                    className="flow-line flow-forward"
                    markerEnd="url(#arrowHead)"
                  />
                )}
                {gensetActive && (
                  <line
                    x1="180"
                    y1="260"
                    x2="410"
                    y2="180"
                    stroke="#ef4444"
                    strokeWidth="6"
                    className="flow-line flow-forward"
                    markerEnd="url(#arrowHead)"
                  />
                )}
                {loadActive && (
                  <line
                    x1="410"
                    y1="180"
                    x2="660"
                    y2="180"
                    stroke="#0ea5e9"
                    strokeWidth="6"
                    className="flow-line flow-forward"
                    markerEnd="url(#arrowHead)"
                  />
                )}
                {batteryActive && (
                  <line
                    x1="410"
                    y1="300"
                    x2="410"
                    y2="180"
                    stroke="#22c55e"
                    strokeWidth="6"
                    className={`flow-line ${batteryDischarging ? 'flow-forward' : 'flow-reverse'}`}
                    markerEnd={batteryDischarging ? 'url(#arrowHead)' : undefined}
                    markerStart={!batteryDischarging ? 'url(#arrowHead)' : undefined}
                  />
                )}
                {(gridImportActive || gridExportActive) && (
                  <line
                    x1="410"
                    y1="50"
                    x2="410"
                    y2="180"
                    stroke="#f97316"
                    strokeWidth="6"
                    className={`flow-line ${gridImportActive ? 'flow-forward' : 'flow-reverse'}`}
                    markerEnd={gridImportActive ? 'url(#arrowHead)' : undefined}
                    markerStart={gridExportActive ? 'url(#arrowHead)' : undefined}
                  />
                )}

                {/* node boxes */}
                <NodeBox x={90} y={62} label="PV" value={num(flowNow.pv, 2, 'kW')} />
                <NodeBox x={58} y={232} label="Genset" value={num(flowNow.genset, 2, 'kW')} />
                <NodeBox x={340} y={154} label="Inverter" value={num(flowNow.load - flowNow.genset, 2, 'kW')} />
                <NodeBox x={675} y={154} label="Load" value={num(flowNow.load, 2, 'kW')} />
                <NodeBox x={338} y={304} label={batteryDischarging ? 'Battery out' : 'Battery in'} value={num(Math.abs(flowNow.battery), 2, 'kW')} />
                <NodeBox x={352} y={6} label={gridImportActive ? 'Grid in' : gridExportActive ? 'Grid out' : 'Grid'} value={num(flowNow.grid, 2, 'kW')} />
              </svg>
            </div>
          </div>
          <div className="border rounded-xl p-4 bg-white">
            <h3 className="font-semibold mb-3">Utilization</h3>
            <div className="space-y-3">
              <RingStat label="PV -> Load" value={pvToLoadPct} color="#eab308" />
              <RingStat label="Battery -> Load" value={batteryToLoadPct} color="#22c55e" />
              <RingStat label="Genset -> Load" value={gensetToLoadPct} color="#ef4444" />
              <RingStat label="Battery charging" value={clampPct(flowNow.load > 0 ? (batteryChargeKw / flowNow.load) * 100 : 0)} color="#10b981" />
              <RingStat label="Grid import" value={clampPct(flowNow.load > 0 ? (gridImportKw / flowNow.load) * 100 : 0)} color="#f97316" />
            </div>
          </div>
        </div>
      </section>

      {/* Power + state chart */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Power and state telemetry</h2>
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
          {powerChartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-sm text-gray-500">
              No telemetry yet — the gensite poller will populate this chart as readings arrive.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={powerChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="ts" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis yAxisId="power" tick={{ fontSize: 10 }} />
                <YAxis yAxisId="pct" orientation="right" tick={{ fontSize: 10 }} domain={[0, 100]} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {POWER_AND_STATE_METRICS.map(m => (
                  <Area
                    key={m.key}
                    type="monotone"
                    dataKey={m.key}
                    name={m.label}
                    yAxisId={m.yAxisId}
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

      {/* Energy chart */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Energy counters</h2>
        <div className="bg-white border rounded-xl p-3" style={{ height: 220 }}>
          {energyChartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-sm text-gray-500">
              No energy counter series available yet for this site.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={energyChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="ts" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {ENERGY_METRICS.map(m => (
                  <Line
                    key={m.key}
                    type="monotone"
                    dataKey={m.key}
                    name={m.label}
                    stroke={m.color}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
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
                    <Tile label="Load" value={num(r?.ac_kw ?? null, 2, 'kW')} />
                    <Tile label="PV"  value={num(r?.pv_kw ?? null, 2, 'kW')} />
                    <Tile label="SoC" value={num(r?.battery_soc_pct ?? null, 1, '%')} />
                    <Tile label="Battery" value={num(r?.battery_kw ?? null, 2, 'kW')} />
                    <Tile label="Grid" value={num(r?.grid_kw ?? null, 2, 'kW')} />
                    <Tile label="Energy" value={num(r?.ac_kwh_total ?? null, 2, 'kWh')} />
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

function NodeBox({ x, y, label, value }: { x: number; y: number; label: string; value: string }) {
  return (
    <g transform={`translate(${x},${y})`}>
      <rect rx="8" ry="8" width="128" height="52" fill="#ffffff" stroke="#e5e7eb" />
      <text x="10" y="20" fontSize="10" fill="#6b7280">{label}</text>
      <text x="10" y="38" fontSize="13" fill="#111827">{value}</text>
    </g>
  );
}

function RingStat({ label, value, color }: { label: string; value: number; color: string }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  const pct = clampPct(value);
  const offset = c - (pct / 100) * c;
  return (
    <div className="flex items-center gap-2">
      <svg width="42" height="42" viewBox="0 0 42 42" className="shrink-0">
        <circle cx="21" cy="21" r={r} stroke="#e5e7eb" strokeWidth="4" fill="none" />
        <circle
          cx="21"
          cy="21"
          r={r}
          stroke={color}
          strokeWidth="4"
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform="rotate(-90 21 21)"
        />
      </svg>
      <div className="text-sm">
        <div className="text-gray-500 text-xs">{label}</div>
        <div className="font-mono">{pct.toFixed(0)}%</div>
      </div>
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
