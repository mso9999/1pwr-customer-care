import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  coverageTrend,
  coverageUpstreamFreshness,
  liveCoverageAudit,
  listCoverageSnapshots,
  takeCoverageSnapshot,
  type CoverageAuditPayload,
  type CoverageSnapshotSummary,
  type CoverageTrendPoint,
} from '../lib/api';

const DEFAULT_COUNTRIES = ['LS', 'BN'];

function classNamesForDeficit(missingPct: number): string {
  if (missingPct >= 80) return 'bg-red-100 text-red-800';
  if (missingPct >= 50) return 'bg-amber-100 text-amber-800';
  return 'bg-yellow-50 text-yellow-700';
}

function MatrixHeatmap({ payload }: { payload: CoverageAuditPayload }) {
  const sites = useMemo(() => Object.keys(payload.monthly_coverage).sort(), [payload]);
  const months = useMemo(() => {
    const all = new Set<string>();
    Object.values(payload.monthly_coverage).forEach((bySite) => Object.keys(bySite).forEach((m) => all.add(m)));
    return Array.from(all).sort();
  }, [payload]);

  // Compute per-site row totals so we can colour by relative density.
  const siteMaxRows: Record<string, number> = {};
  sites.forEach((s) => {
    const vals = Object.values(payload.monthly_coverage[s] || {}).map((c) => c.rows);
    siteMaxRows[s] = vals.length ? Math.max(...vals) : 0;
  });

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="px-2 py-1 text-left bg-gray-50 sticky left-0 z-10">Site</th>
            {months.map((m) => (
              <th key={m} className="px-2 py-1 bg-gray-50 font-mono text-[10px]">{m}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sites.map((s) => (
            <tr key={s}>
              <td className="px-2 py-1 font-medium bg-white sticky left-0 z-10 border-r">{s}</td>
              {months.map((m) => {
                const cell = payload.monthly_coverage[s]?.[m];
                if (!cell) {
                  return <td key={m} className="px-2 py-1 text-gray-300 text-center">--</td>;
                }
                const max = siteMaxRows[s] || 1;
                const ratio = cell.rows / max;
                const bg =
                  ratio < 0.2 ? 'bg-red-100' :
                  ratio < 0.5 ? 'bg-amber-50' :
                  ratio < 0.8 ? 'bg-yellow-50' :
                  'bg-emerald-50';
                return (
                  <td key={m} className={`px-2 py-1 text-center ${bg}`} title={`${cell.rows} rows, ${cell.meters} meters`}>
                    <div className="font-mono">{cell.rows.toLocaleString()}</div>
                    <div className="text-[10px] text-gray-500">{cell.meters}m</div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrendSparkline({ points, getValue, label }: {
  points: CoverageTrendPoint[];
  getValue: (p: CoverageTrendPoint) => number;
  label: string;
}) {
  if (points.length < 2) {
    return <span className="text-xs text-gray-400">--</span>;
  }
  const values = points.map(getValue);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const w = 120;
  const h = 30;
  const step = w / (values.length - 1);
  const path = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`).join(' ');
  const last = values[values.length - 1];
  const first = values[0];
  const direction = last > first ? '↑' : last < first ? '↓' : '→';
  const directionColour = label === 'active' ? (last > first ? 'text-emerald-600' : 'text-red-600')
                                              : (last > first ? 'text-red-600' : 'text-emerald-600');
  return (
    <div className="flex items-center gap-2">
      <svg width={w} height={h} className="text-blue-500">
        <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} />
      </svg>
      <span className={`text-xs font-mono ${directionColour}`}>{direction} {last.toLocaleString()}</span>
    </div>
  );
}

export default function CoverageAuditPage() {
  const { t } = useTranslation(['coverage']);

  const [country, setCountry] = useState<string>('LS');
  const [windowMonths, setWindowMonths] = useState<number>(8);
  const [staleDays, setStaleDays] = useState<number>(30);
  const [deficitThreshold, setDeficitThreshold] = useState<number>(0.5);

  const [payload, setPayload] = useState<CoverageAuditPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string>('');

  const [snapshots, setSnapshots] = useState<CoverageSnapshotSummary[]>([]);
  const [snapshotting, setSnapshotting] = useState(false);
  const [snapshotMsg, setSnapshotMsg] = useState<string>('');
  const [snapshotNotes, setSnapshotNotes] = useState<string>('');
  const [snapshotIncludeUpstream, setSnapshotIncludeUpstream] = useState(false);

  const [trend, setTrend] = useState<CoverageTrendPoint[]>([]);
  const [upstream, setUpstream] = useState<Record<string, unknown> | null>(null);
  const [upstreamCachedAge, setUpstreamCachedAge] = useState<number | null>(null);
  const [upstreamErr, setUpstreamErr] = useState<string>('');
  const [upstreamLoading, setUpstreamLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setErr('');
    try {
      const p = await liveCoverageAudit({
        country, window_months: windowMonths, stale_days: staleDays,
        deficit_threshold: deficitThreshold,
      });
      setPayload(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const refreshSnapshots = async () => {
    try {
      const list = await listCoverageSnapshots(country, 30);
      setSnapshots(list);
    } catch {
      // non-fatal
    }
  };

  const refreshTrend = async () => {
    try {
      const t = await coverageTrend(country, 60);
      setTrend(t.points);
    } catch {
      setTrend([]);
    }
  };

  useEffect(() => {
    refresh();
    refreshSnapshots();
    refreshTrend();
    setUpstream(null);
    setUpstreamErr('');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country]);

  const onSnapshot = async () => {
    setSnapshotting(true);
    setSnapshotMsg('');
    try {
      const r = await takeCoverageSnapshot({
        country, window_months: windowMonths, stale_days: staleDays,
        deficit_threshold: deficitThreshold,
        notes: snapshotNotes.trim() || undefined,
        include_upstream: snapshotIncludeUpstream,
      });
      setSnapshotMsg(t('coverage:snapshotSaved', { id: r.snapshot_id }));
      setSnapshotNotes('');
      refreshSnapshots();
      refreshTrend();
    } catch (e) {
      setSnapshotMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSnapshotting(false);
    }
  };

  const onProbeUpstream = async (refresh = false) => {
    setUpstreamLoading(true);
    setUpstreamErr('');
    try {
      const r = await coverageUpstreamFreshness(country, refresh);
      setUpstream(r);
      setUpstreamCachedAge((r as { age_seconds?: number }).age_seconds ?? null);
    } catch (e) {
      setUpstreamErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUpstreamLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('coverage:title')}</h1>
        <p className="text-sm text-gray-500">{t('coverage:subtitle')}</p>
      </div>

      {/* Controls */}
      <div className="bg-white rounded-lg shadow p-4 flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('coverage:country')}</label>
          <select value={country} onChange={(e) => setCountry(e.target.value)} className="px-3 py-2 border rounded-lg text-sm bg-white">
            {DEFAULT_COUNTRIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('coverage:window')}</label>
          <input type="number" min={1} max={36} value={windowMonths} onChange={(e) => setWindowMonths(Number(e.target.value) || 8)} className="w-20 px-3 py-2 border rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('coverage:stale')}</label>
          <input type="number" min={1} max={365} value={staleDays} onChange={(e) => setStaleDays(Number(e.target.value) || 30)} className="w-20 px-3 py-2 border rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('coverage:deficit')}</label>
          <input type="number" min={0} max={1} step={0.05} value={deficitThreshold} onChange={(e) => setDeficitThreshold(Number(e.target.value) || 0.5)} className="w-20 px-3 py-2 border rounded-lg text-sm" />
        </div>
        <button onClick={refresh} disabled={loading} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          {loading ? t('coverage:refreshing') : t('coverage:refresh')}
        </button>
      </div>

      {err && <div className="bg-red-50 text-red-700 text-sm p-3 rounded">{err}</div>}

      {/* Headline totals + sparklines */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">{t('coverage:totalsSection')}</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {([
              ['totalActiveMeters', payload.totals.active_meters, (p: CoverageTrendPoint) => p.active_meters, 'active'],
              ['totalZeroCoverage', payload.totals.zero_coverage_meters, (p: CoverageTrendPoint) => p.zero_coverage_meters, 'zero'],
              ['totalStale', payload.totals.stale_meters, (p: CoverageTrendPoint) => p.stale_meters, 'stale'],
              ['totalDeficits', payload.totals.monthly_deficits_flagged, (p: CoverageTrendPoint) => p.monthly_deficits_flagged, 'deficits'],
              ['totalSitesActive', payload.totals.sites_with_active_meters, null, 'sitesA'],
              ['totalSitesData', payload.totals.sites_with_data, null, 'sitesD'],
            ] as Array<[string, number, ((p: CoverageTrendPoint) => number) | null, string]>).map(([key, val, getter, label]) => (
              <div key={key} className="border border-gray-100 rounded p-3">
                <div className="text-[11px] text-gray-500">{t(`coverage:${key}`)}</div>
                <div className="text-2xl font-bold text-gray-800 mt-0.5">{val.toLocaleString()}</div>
                {getter && trend.length > 1 && <TrendSparkline points={trend} getValue={getter} label={label} />}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Snapshot controls */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-700">{t('coverage:snapshotSection')}</h2>
            <p className="text-xs text-gray-500">{t('coverage:snapshotDesc')}</p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs text-gray-500 mb-1">{t('coverage:snapshotNotes')}</label>
              <input value={snapshotNotes} onChange={(e) => setSnapshotNotes(e.target.value)} className="w-full px-3 py-2 border rounded-lg text-sm" />
            </div>
            <label className="flex items-center gap-2 text-xs text-gray-600 mb-2">
              <input type="checkbox" checked={snapshotIncludeUpstream} onChange={(e) => setSnapshotIncludeUpstream(e.target.checked)} />
              {t('coverage:snapshotIncludeUpstream')}
            </label>
            <button onClick={onSnapshot} disabled={snapshotting} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
              {snapshotting ? t('coverage:takingSnapshot') : t('coverage:takeSnapshot')}
            </button>
          </div>
          {snapshotMsg && <div className="text-sm text-emerald-700 bg-emerald-50 rounded p-2">{snapshotMsg}</div>}
        </div>
      )}

      {/* Per-site overview */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-2">{t('coverage:perSiteSection')}</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">{t('coverage:colSite')}</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">{t('coverage:colActive')}</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">{t('coverage:colZero')}</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">{t('coverage:colZeroPct')}</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">{t('coverage:colStaleMeters', { days: payload.stale_days })}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {Object.keys(payload.active_counts).sort().map((s) => {
                  const zsum = payload.zero_coverage_summary[s];
                  const stale = payload.stale_meters.filter((m) => m.community === s).length;
                  return (
                    <tr key={s} className="hover:bg-gray-50">
                      <td className="px-3 py-1.5 font-mono">{s}</td>
                      <td className="px-3 py-1.5 text-right">{payload.active_counts[s]}</td>
                      <td className="px-3 py-1.5 text-right">{zsum?.zero_coverage_meters ?? 0}</td>
                      <td className="px-3 py-1.5 text-right">{zsum?.zero_coverage_pct ?? 0}%</td>
                      <td className="px-3 py-1.5 text-right">{stale}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Per-month coverage matrix */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700">{t('coverage:matrixSection')}</h2>
          <p className="text-xs text-gray-500 mb-3">{t('coverage:matrixDesc')}</p>
          <MatrixHeatmap payload={payload} />
        </div>
      )}

      {/* Deficits */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700">{t('coverage:deficitsSection')}</h2>
          <p className="text-xs text-gray-500 mb-3">{t('coverage:deficitsDesc')}</p>
          {payload.monthly_deficits.length === 0 ? (
            <div className="text-sm text-gray-400">{t('coverage:noDeficits')}</div>
          ) : (
            <>
              {(() => {
                const complete = payload.monthly_deficits.filter((d) => !d.in_progress);
                const inProgress = payload.monthly_deficits.filter((d) => d.in_progress);
                return (
                  <div className="space-y-3">
                    {complete.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-gray-600 mb-1">{t('coverage:completeMonths')}</p>
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs">
                            <thead className="bg-gray-50 border-b">
                              <tr>
                                <th className="px-3 py-1.5 text-left">{t('coverage:colSite')}</th>
                                <th className="px-3 py-1.5 text-left">{t('coverage:colMonth')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colRows')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colBaseline')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colMissingPct')}</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y">
                              {complete.map((d, i) => (
                                <tr key={i}>
                                  <td className="px-3 py-1 font-mono">{d.site}</td>
                                  <td className="px-3 py-1 font-mono">{d.month}</td>
                                  <td className="px-3 py-1 text-right">{d.rows.toLocaleString()}</td>
                                  <td className="px-3 py-1 text-right">{Math.round(d.baseline_median).toLocaleString()}</td>
                                  <td className="px-3 py-1 text-right">
                                    <span className={`px-1.5 py-0.5 rounded ${classNamesForDeficit(d.missing_pct)}`}>{d.missing_pct}%</span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    {inProgress.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-gray-600 mb-1">{t('coverage:inProgressMonth')}</p>
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs">
                            <thead className="bg-gray-50 border-b">
                              <tr>
                                <th className="px-3 py-1.5 text-left">{t('coverage:colSite')}</th>
                                <th className="px-3 py-1.5 text-left">{t('coverage:colMonth')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colRows')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colExpected')}</th>
                                <th className="px-3 py-1.5 text-right">{t('coverage:colMissingPct')}</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y">
                              {inProgress.map((d, i) => (
                                <tr key={i}>
                                  <td className="px-3 py-1 font-mono">{d.site}</td>
                                  <td className="px-3 py-1 font-mono">{d.month}</td>
                                  <td className="px-3 py-1 text-right">{d.rows.toLocaleString()}</td>
                                  <td className="px-3 py-1 text-right">{(d.expected_so_far ?? 0).toLocaleString()}</td>
                                  <td className="px-3 py-1 text-right">
                                    <span className={`px-1.5 py-0.5 rounded ${classNamesForDeficit(d.missing_pct)}`}>{d.missing_pct}%</span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}
            </>
          )}
        </div>
      )}

      {/* Last ingest per (site, source) */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">{t('coverage:lastIngestSection')}</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colSite')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colSource')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colLastReading')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colLastInsert')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:colRowsTotal')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {Object.keys(payload.last_ingest).sort().flatMap((s) =>
                  Object.entries(payload.last_ingest[s]).map(([src, info]) => (
                    <tr key={`${s}-${src}`}>
                      <td className="px-3 py-1 font-mono">{s}</td>
                      <td className="px-3 py-1">{src}</td>
                      <td className="px-3 py-1 font-mono text-gray-500">{info.last_reading?.slice(0, 10) || '--'}</td>
                      <td className="px-3 py-1 font-mono text-gray-500">{info.last_insert?.slice(0, 10) || '--'}</td>
                      <td className="px-3 py-1 text-right">{info.rows_total.toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Upstream freshness */}
      {payload && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-700">{t('coverage:upstreamSection')}</h2>
            <p className="text-xs text-gray-500">{t('coverage:upstreamDesc')}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => onProbeUpstream(false)} disabled={upstreamLoading} className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-50">
              {upstreamLoading ? t('coverage:probingUpstream') : t('coverage:probeUpstream')}
            </button>
            {upstream && upstreamCachedAge !== null && (
              <span className="text-xs text-gray-500 self-center">{t('coverage:upstreamCached', { age: upstreamCachedAge })}</span>
            )}
          </div>
          {upstreamErr && <div className="text-xs text-red-700 bg-red-50 p-2 rounded">{t('coverage:upstreamError', { error: upstreamErr })}</div>}
          {upstream && (
            <pre className="bg-gray-50 border border-gray-200 rounded p-3 text-[11px] overflow-auto max-h-72">{JSON.stringify(upstream, null, 2)}</pre>
          )}
        </div>
      )}

      {/* Zero-coverage meters */}
      {payload && payload.zero_coverage_meters.length > 0 && (
        <details className="bg-white rounded-lg shadow p-4">
          <summary className="cursor-pointer">
            <span className="text-sm font-semibold text-gray-700">{t('coverage:zeroSection')}</span>
            <span className="text-xs text-gray-500 ml-2">({payload.zero_coverage_meters.length})</span>
          </summary>
          <p className="text-xs text-gray-500 my-2">{t('coverage:zeroDesc')}</p>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b sticky top-0">
                <tr>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colSite')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colAccount')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colMeterId')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colRole')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colConnect')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {payload.zero_coverage_meters.map((m, i) => (
                  <tr key={i}>
                    <td className="px-3 py-1 font-mono">{m.community}</td>
                    <td className="px-3 py-1 font-mono">{m.account_number}</td>
                    <td className="px-3 py-1 font-mono">{m.meter_id}</td>
                    <td className="px-3 py-1">{m.role}</td>
                    <td className="px-3 py-1 text-gray-500">{m.customer_connect_date || '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* Stale meters */}
      {payload && payload.stale_meters.length > 0 && (
        <details className="bg-white rounded-lg shadow p-4">
          <summary className="cursor-pointer">
            <span className="text-sm font-semibold text-gray-700">{t('coverage:staleSection', { days: payload.stale_days })}</span>
            <span className="text-xs text-gray-500 ml-2">({payload.stale_meters.length})</span>
          </summary>
          <div className="overflow-x-auto max-h-96 mt-2">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b sticky top-0">
                <tr>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colSite')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colAccount')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colMeterId')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colLastReading')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:colDaysStale')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {payload.stale_meters.map((m, i) => (
                  <tr key={i}>
                    <td className="px-3 py-1 font-mono">{m.community}</td>
                    <td className="px-3 py-1 font-mono">{m.account_number}</td>
                    <td className="px-3 py-1 font-mono">{m.meter_id}</td>
                    <td className="px-3 py-1 font-mono text-gray-500">{m.last_reading?.slice(0, 10) || '--'}</td>
                    <td className="px-3 py-1 text-right">{m.stale_days ?? '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* Cross-country leak + orphans */}
      {payload && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="text-sm font-semibold text-gray-700">{t('coverage:crossSection')}</h3>
            <p className="text-xs text-gray-500 mb-2">{t('coverage:crossDesc')}</p>
            {payload.cross_country_meters.length === 0 ? (
              <div className="text-xs text-gray-400">{t('coverage:noCross')}</div>
            ) : (
              <ul className="text-xs space-y-1">
                {payload.cross_country_meters.map((c, i) => (
                  <li key={i} className="font-mono">
                    {c.community}: {c.meters} meters / {c.accounts} accts (in {c.this_db_country})
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="text-sm font-semibold text-gray-700">{t('coverage:declaredMissingSection')}</h3>
            <p className="text-xs text-gray-500 mb-2">{t('coverage:declaredMissingDesc')}</p>
            {payload.declared_sites_missing_data.length === 0 ? (
              <div className="text-xs text-gray-400">{t('coverage:noDeclaredMissing')}</div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {payload.declared_sites_missing_data.map((s) => (
                  <span key={s} className="px-2 py-0.5 bg-amber-50 text-amber-800 rounded text-xs font-mono">{s}</span>
                ))}
              </div>
            )}
          </div>
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="text-sm font-semibold text-gray-700">{t('coverage:orphanSection')}</h3>
            <p className="text-xs text-gray-500 mb-2">{t('coverage:orphanDesc')}</p>
            {payload.orphan_sites.length === 0 ? (
              <div className="text-xs text-gray-400">{t('coverage:noOrphan')}</div>
            ) : (
              <div className="flex flex-wrap gap-1">
                {payload.orphan_sites.map((s) => (
                  <span key={s} className="px-2 py-0.5 bg-red-50 text-red-700 rounded text-xs font-mono">{s}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Snapshots history */}
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700">{t('coverage:snapshotsSection')}</h2>
        <p className="text-xs text-gray-500 mb-3">{t('coverage:snapshotsDesc')}</p>
        {snapshots.length === 0 ? (
          <div className="text-xs text-gray-400">--</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-3 py-1.5 text-left">#</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colSnapshotAt')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:totalActiveMeters')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:totalZeroCoverage')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:totalStale')}</th>
                  <th className="px-3 py-1.5 text-right">{t('coverage:totalDeficits')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colTriggeredBy')}</th>
                  <th className="px-3 py-1.5 text-left">{t('coverage:colNotes')}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {snapshots.map((s) => (
                  <tr key={s.id}>
                    <td className="px-3 py-1 font-mono">{s.id}</td>
                    <td className="px-3 py-1 font-mono text-gray-500">{s.snapshot_at.slice(0, 16).replace('T', ' ')}</td>
                    <td className="px-3 py-1 text-right">{s.active_meters.toLocaleString()}</td>
                    <td className="px-3 py-1 text-right">{s.zero_coverage_meters}</td>
                    <td className="px-3 py-1 text-right">{s.stale_meters}</td>
                    <td className="px-3 py-1 text-right">{s.monthly_deficits_flagged}</td>
                    <td className="px-3 py-1 font-mono text-gray-500">{s.triggered_by}</td>
                    <td className="px-3 py-1 text-gray-500">{s.notes || '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
