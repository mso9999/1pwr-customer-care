import { useEffect, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import {
  getLpgSite,
  createLpgBatch,
  archiveLpgBatch,
  startLpgRun,
  stopLpgRun,
  getLpgLiveSoc,
  updateLpgSiteSettings,
  type LpgSiteSummary,
  type LpgBatch,
  type LpgRun,
} from '../lib/api';

function fmtDateTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  } catch {
    return ts;
  }
}

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtAgo(ts: string | null | undefined): string {
  if (!ts) return '';
  const ms = Date.now() - new Date(ts).getTime();
  if (!Number.isFinite(ms)) return '';
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function num(v: string): number | undefined {
  if (v.trim() === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function fuelEconomy(run: { cylinders_consumed: number; runtime_seconds: number | null; total_kwh: number | null }): {
  kg_per_hour: number | null;
  kg_per_kwh: number | null;
} {
  const kg = run.cylinders_consumed * 48;
  const hrs = (run.runtime_seconds ?? 0) / 3600;
  return {
    kg_per_hour: hrs > 0 && run.cylinders_consumed > 0 ? +(kg / hrs).toFixed(1) : null,
    kg_per_kwh: run.total_kwh != null && run.total_kwh > 0 && run.cylinders_consumed > 0
      ? +(kg / run.total_kwh).toFixed(3) : null,
  };
}

export default function LpgSitePage() {
  const { code = '' } = useParams();
  const { user } = useAuth();
  const canEdit = user?.role === 'superadmin' || user?.role === 'onm_team';

  const [summary, setSummary] = useState<LpgSiteSummary | null>(null);
  const [batches, setBatches] = useState<LpgBatch[]>([]);
  const [runs, setRuns] = useState<LpgRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [liveSoc, setLiveSoc] = useState<{ soc_pct: number | null; ts_utc: string | null }>({ soc_pct: null, ts_utc: null });

  const openRun = runs.find((r) => r.status === 'running') || null;
  const activeBatches = batches.filter((b) => b.status === 'active' && b.cylinders_remaining > 0);

  const load = useCallback(() => {
    setLoading(true);
    getLpgSite(code)
      .then((r) => {
        setSummary(r.summary);
        setBatches(r.batches);
        setRuns(r.runs);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [code]);

  useEffect(() => {
    load();
  }, [load]);

  const refreshSoc = useCallback(async () => {
    try {
      const r = await getLpgLiveSoc(code);
      setLiveSoc({ soc_pct: r.soc_pct, ts_utc: r.ts_utc });
      return r;
    } catch {
      return null;
    }
  }, [code]);

  useEffect(() => {
    refreshSoc();
  }, [refreshSoc]);

  // ---- Stock capture form ----
  const [bCyl, setBCyl] = useState('');
  const [bPrice, setBPrice] = useState('');
  const [bCurrency, setBCurrency] = useState('');
  const [bNotes, setBNotes] = useState('');

  const submitBatch = async () => {
    const cylinders_total = num(bCyl);
    if (!cylinders_total || cylinders_total <= 0) {
      setError('Enter the number of cylinders received.');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const r = await createLpgBatch(code, {
        cylinders_total,
        unit_price: num(bPrice),
        currency: bCurrency.trim() || undefined,
        notes: bNotes.trim() || undefined,
      });
      setNotice(`Recorded delivery ${r.batch.batch_number} (${r.batch.cylinders_total} cylinders).`);
      setBCyl('');
      setBPrice('');
      setBNotes('');
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- Start run form ----
  const [sBatch, setSBatch] = useState('');
  const [sSoc, setSSoc] = useState('');
  const [sReason, setSReason] = useState('');
  const [sOperator, setSOperator] = useState('');

  const submitStart = async () => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await startLpgRun(code, {
        batch_id: sBatch ? Number(sBatch) : undefined,
        start_soc_pct: num(sSoc),
        start_reason: sReason.trim() || undefined,
        start_operator: sOperator.trim() || undefined,
      });
      setNotice('Generator run started — timer running.');
      setSSoc('');
      setSReason('');
      setSOperator('');
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- Stop run form ----
  const [eSoc, setESoc] = useState('');
  const [eReason, setEReason] = useState('');
  const [eOperator, setEOperator] = useState('');
  const [eDepleted, setEDepleted] = useState(false);
  const [eConsumed, setEConsumed] = useState('1');

  // Auto-prefill SOC fields from live telemetry while they're still empty.
  useEffect(() => {
    if (liveSoc.soc_pct == null) return;
    const v = String(liveSoc.soc_pct);
    setSSoc((prev) => (prev === '' ? v : prev));
    setESoc((prev) => (prev === '' ? v : prev));
  }, [liveSoc]);

  const socHint = (target: 'start' | 'stop') => {
    if (liveSoc.soc_pct == null) return null;
    return (
      <button
        type="button"
        onClick={async () => {
          const r = await refreshSoc();
          if (r?.soc_pct != null) (target === 'start' ? setSSoc : setESoc)(String(r.soc_pct));
        }}
        className="text-xs text-blue-600 hover:underline mt-1"
        title="Refresh from live telemetry"
      >
        ↻ live: {liveSoc.soc_pct}% {liveSoc.ts_utc ? `(${fmtAgo(liveSoc.ts_utc)})` : ''} · use
      </button>
    );
  };

  const submitStop = async () => {
    if (!openRun) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const r = await stopLpgRun(openRun.id, {
        stop_soc_pct: num(eSoc),
        stop_reason: eReason.trim() || undefined,
        stop_operator: eOperator.trim() || undefined,
        lpg_depleted: eDepleted,
        cylinders_consumed: eDepleted ? num(eConsumed) ?? 1 : undefined,
      });
      let msg = `Run stopped (${fmtDuration(r.run.runtime_seconds)}).`;
      if (eDepleted) msg += ` ${r.site_remaining} cylinder(s) remaining at site.`;
      if (r.days_remaining != null) msg += ` ~${r.days_remaining} day(s) of LPG left at current burn rate.`;
      if (r.total_kwh != null) {
        msg += ` ⚡ ${r.total_kwh.toFixed(1)} kWh generated.`;
        if (r.kg_per_kwh != null) msg += ` ${r.kg_per_kwh} kg/kWh (fuel economy).`;
        if (r.kg_per_hour != null) msg += ` ${r.kg_per_hour} kg/hr burn rate.`;
      }
      if (r.critical_triggered) msg += ' ⚠️ Site is now CRITICAL — alert sent to O&M.';
      else if (r.low_runway_triggered) msg += ' ⚠️ Low runway — alert sent to O&M.';
      setNotice(msg);
      setESoc('');
      setEReason('');
      setEOperator('');
      setEDepleted(false);
      setEConsumed('1');
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- Per-site low-runway threshold ----
  const [warnDays, setWarnDays] = useState('');
  useEffect(() => {
    if (summary?.lpg_low_runway_warn_days != null) setWarnDays(String(summary.lpg_low_runway_warn_days));
  }, [summary?.lpg_low_runway_warn_days]);

  const saveWarnDays = async (clear: boolean) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (clear) {
        await updateLpgSiteSettings(code, { clear: true });
        setWarnDays('');
        setNotice('Low-runway threshold reset to the default (7 days).');
      } else {
        const d = num(warnDays);
        if (!d || d < 1) {
          setError('Enter a threshold of at least 1 day.');
          setBusy(false);
          return;
        }
        await updateLpgSiteSettings(code, { low_runway_warn_days: d });
        setNotice(`Low-runway threshold set to ${d} days.`);
      }
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleArchive = async (batchId: number) => {
    setBusy(true);
    setError('');
    try {
      await archiveLpgBatch(code, batchId);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const currencyHint = summary?.currency || '';
  const inputCls = 'border rounded-lg px-3 py-2 text-sm w-full';
  const labelCls = 'block text-xs font-medium text-gray-600 mb-1';
  const runwayStatus = summary?.runway_status ?? 'ok';
  const runwayCardCls =
    runwayStatus === 'critical' ? 'bg-red-50 border-red-200'
    : runwayStatus === 'warn' ? 'bg-amber-50 border-amber-200'
    : 'bg-white';
  const runwayTextCls =
    runwayStatus === 'critical' ? 'text-red-600'
    : runwayStatus === 'warn' ? 'text-amber-600'
    : '';

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="mb-4">
        <Link to="/lpg" className="text-sm text-blue-600 hover:underline">← All LPG sites</Link>
      </div>

      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold font-mono">{code.toUpperCase()}</h1>
          <p className="text-sm text-gray-500">{summary?.display_name || 'LPG generator-fuel tracking'}</p>
        </div>
        {summary?.is_critical && (
          <span className="text-sm px-3 py-1 rounded-full bg-red-100 text-red-700 font-medium">
            ⚠️ Critical — last cylinder
          </span>
        )}
      </div>

      {loading && <p className="text-sm text-gray-500">Loading…</p>}
      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
      {notice && <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2 mb-3">{notice}</p>}

      {!loading && (
        <>
          {/* Balance summary */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
            <div className="bg-white rounded-xl shadow-sm border p-4">
              <div className="text-xs uppercase tracking-wide text-gray-500">Cylinders left</div>
              <div className="text-2xl font-semibold mt-1">{summary?.cylinders_remaining ?? 0}</div>
            </div>
            <div className="bg-white rounded-xl shadow-sm border p-4">
              <div className="text-xs uppercase tracking-wide text-gray-500">kg remaining</div>
              <div className="text-2xl font-semibold mt-1">{(summary?.kg_remaining ?? 0).toLocaleString()}</div>
            </div>
            <div className={`rounded-xl shadow-sm border p-4 ${runwayCardCls}`}>
              <div className="text-xs uppercase tracking-wide text-gray-500">Days left (at burn rate)</div>
              <div className={`text-2xl font-semibold mt-1 ${runwayTextCls}`}>
                {summary?.days_remaining != null ? `${summary.days_remaining}d` : '—'}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {summary && summary.cylinders_per_day > 0 ? `${summary.cylinders_per_day}/day` : 'no recent burn'}
              </div>
            </div>
            <div className="bg-white rounded-xl shadow-sm border p-4">
              <div className="text-xs uppercase tracking-wide text-gray-500">Value remaining</div>
              <div className="text-2xl font-semibold mt-1">
                {summary?.value_remaining != null ? `${currencyHint} ${summary.value_remaining.toLocaleString()}` : '—'}
              </div>
            </div>
            <div className="bg-white rounded-xl shadow-sm border p-4">
              <div className="text-xs uppercase tracking-wide text-gray-500">Run status</div>
              <div className="text-2xl font-semibold mt-1">
                {openRun ? <span className="text-green-600">Running</span> : <span className="text-gray-400">Idle</span>}
              </div>
            </div>
          </div>

          {/* Low-runway threshold setting */}
          {canEdit && (
            <div className="bg-white rounded-xl shadow-sm border p-4 mb-6 flex flex-wrap items-end gap-3">
              <div>
                <label className={labelCls}>Low-runway warning threshold</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={120}
                    value={warnDays}
                    onChange={(e) => setWarnDays(e.target.value)}
                    placeholder="7"
                    className="border rounded-lg px-3 py-2 text-sm w-24"
                  />
                  <span className="text-sm text-gray-500">days of LPG left</span>
                </div>
              </div>
              <button onClick={() => saveWarnDays(false)} disabled={busy} className="px-3 py-2 bg-gray-800 hover:bg-gray-900 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                Save
              </button>
              {summary?.lpg_low_runway_warn_days != null && (
                <button onClick={() => saveWarnDays(true)} disabled={busy} className="px-3 py-2 text-gray-500 hover:text-gray-700 text-sm">
                  Reset to default (7)
                </button>
              )}
              <span className="text-xs text-gray-400 ml-auto">
                Effective: {summary?.low_runway_warn_days ?? 7} days
                {summary?.lpg_low_runway_warn_days == null ? ' (default)' : ' (custom)'}
              </span>
            </div>
          )}

          {/* Read-only notice: explain why capture forms are absent for non-writers */}
          {!canEdit && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6 text-sm text-amber-800">
              You're viewing LPG data in <strong>read-only</strong> mode. Recording deliveries
              and logging generator runs requires the <strong>onm_team</strong> or{' '}
              <strong>superadmin</strong> role — ask an administrator to update your account role.
            </div>
          )}

          {/* First-time onboarding (writers only): no deliveries recorded yet */}
          {!loading && canEdit && batches.length === 0 && (
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 mb-6">
              <h2 className="font-semibold text-blue-800 mb-2">👋 This site isn't tracking LPG yet</h2>
              <ol className="text-sm text-blue-700 space-y-1 list-decimal list-inside">
                <li>Record your first LPG delivery below — this creates a batch and enrolls the site in tracking.</li>
                <li>Once stock exists, you can start and stop generator runs to log consumption.</li>
                <li>The overview table on the LPG page will then include this site with runway and cost data.</li>
              </ol>
            </div>
          )}

          {/* Action panels */}
          {canEdit && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
              {/* Stock capture */}
              <div className="bg-white rounded-xl shadow-sm border p-5">
                <h2 className="font-semibold mb-1">Record LPG delivery</h2>
                <p className="text-xs text-gray-500 mb-4">Captures a new batch; a batch number and date are generated automatically.</p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={labelCls}>Cylinders (48 kg)</label>
                    <input type="number" min={1} value={bCyl} onChange={(e) => setBCyl(e.target.value)} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Price per cylinder</label>
                    <input type="number" min={0} step="0.01" value={bPrice} onChange={(e) => setBPrice(e.target.value)} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Currency</label>
                    <input value={bCurrency} onChange={(e) => setBCurrency(e.target.value)} placeholder={currencyHint || 'LSL'} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Notes</label>
                    <input value={bNotes} onChange={(e) => setBNotes(e.target.value)} className={inputCls} />
                  </div>
                </div>
                <button onClick={submitBatch} disabled={busy} className="mt-4 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                  Save delivery
                </button>
              </div>

              {/* Generator run */}
              <div className="bg-white rounded-xl shadow-sm border p-5">
                {openRun ? (
                  <>
                    <h2 className="font-semibold mb-1">Stop generator run</h2>
                    <p className="text-xs text-gray-500 mb-4">
                      Started {fmtDateTime(openRun.started_at)}
                      {openRun.batch_number ? ` · batch ${openRun.batch_number}` : ''}
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className={labelCls}>Battery SOC % (stop)</label>
                        <input type="number" min={0} max={100} step="0.1" value={eSoc} onChange={(e) => setESoc(e.target.value)} className={inputCls} />
                        {socHint('stop')}
                      </div>
                      <div>
                        <label className={labelCls}>Operator</label>
                        <input value={eOperator} onChange={(e) => setEOperator(e.target.value)} className={inputCls} />
                      </div>
                      <div className="col-span-2">
                        <label className={labelCls}>Stoppage reason</label>
                        <input value={eReason} onChange={(e) => setEReason(e.target.value)} className={inputCls} />
                      </div>
                    </div>
                    <label className="flex items-center gap-2 mt-3 text-sm">
                      <input type="checkbox" checked={eDepleted} onChange={(e) => setEDepleted(e.target.checked)} />
                      A cylinder was emptied during this run
                    </label>
                    {eDepleted && (
                      <div className="mt-2 w-40">
                        <label className={labelCls}>Cylinders consumed</label>
                        <input type="number" min={1} value={eConsumed} onChange={(e) => setEConsumed(e.target.value)} className={inputCls} />
                      </div>
                    )}
                    <button onClick={submitStop} disabled={busy} className="mt-4 px-4 py-2 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                      Stop run
                    </button>
                  </>
                ) : (
                  <>
                    <h2 className="font-semibold mb-1">Start generator run</h2>
                    <p className="text-xs text-gray-500 mb-4">Begins a timed run against the selected batch.</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="col-span-2">
                        <label className={labelCls}>Batch</label>
                        <select value={sBatch} onChange={(e) => setSBatch(e.target.value)} className={inputCls}>
                          <option value="">— select active batch —</option>
                          {activeBatches.map((b) => (
                            <option key={b.id} value={b.id}>
                              {b.batch_number} ({b.cylinders_remaining} left)
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>Battery SOC % (start)</label>
                        <input type="number" min={0} max={100} step="0.1" value={sSoc} onChange={(e) => setSSoc(e.target.value)} className={inputCls} />
                        {socHint('start')}
                      </div>
                      <div>
                        <label className={labelCls}>Operator</label>
                        <input value={sOperator} onChange={(e) => setSOperator(e.target.value)} className={inputCls} />
                      </div>
                      <div className="col-span-2">
                        <label className={labelCls}>Start reason</label>
                        <input value={sReason} onChange={(e) => setSReason(e.target.value)} className={inputCls} />
                      </div>
                    </div>
                    <button onClick={submitStart} disabled={busy || activeBatches.length === 0} className="mt-4 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
                      Start run
                    </button>
                    {activeBatches.length === 0 && (
                      <p className="text-xs text-amber-600 mt-2">No active batch with stock — record a delivery first.</p>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* Batches table */}
          <div className="bg-white rounded-xl shadow-sm border overflow-x-auto mb-8">
            <div className="px-4 py-3 border-b font-semibold text-sm">Batches</div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-3">Batch</th>
                  <th className="px-4 py-3">Arrived</th>
                  <th className="px-4 py-3 text-right">Total</th>
                  <th className="px-4 py-3 text-right">Remaining</th>
                  <th className="px-4 py-3 text-right">Unit price</th>
                  <th className="px-4 py-3">Status</th>
                  {canEdit && <th className="px-4 py-3"></th>}
                </tr>
              </thead>
              <tbody className="divide-y">
                {batches.length === 0 && (
                  <tr><td colSpan={canEdit ? 7 : 6} className="px-4 py-6 text-center text-gray-400">No batches yet.</td></tr>
                )}
                {batches.map((b) => (
                  <tr key={b.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono">{b.batch_number}</td>
                    <td className="px-4 py-3 text-gray-600">{fmtDateTime(b.arrived_at)}</td>
                    <td className="px-4 py-3 text-right">{b.cylinders_total}</td>
                    <td className="px-4 py-3 text-right font-semibold">{b.cylinders_remaining}</td>
                    <td className="px-4 py-3 text-right text-gray-600">{b.unit_price != null ? `${b.currency || ''} ${b.unit_price}` : '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${b.status === 'active' ? 'bg-green-100 text-green-700' : b.status === 'depleted' ? 'bg-gray-200 text-gray-600' : 'bg-gray-100 text-gray-400'}`}>
                        {b.status}
                      </span>
                    </td>
                    {canEdit && (
                      <td className="px-4 py-3 text-right">
                        {b.status !== 'archived' && (
                          <button onClick={() => handleArchive(b.id)} disabled={busy} className="text-xs text-gray-500 hover:text-red-600">
                            Archive
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Runs table */}
          <div className="bg-white rounded-xl shadow-sm border overflow-x-auto">
            <div className="px-4 py-3 border-b font-semibold text-sm">Generator runs</div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-3">Started</th>
                  <th className="px-4 py-3">Ended</th>
                  <th className="px-4 py-3 text-right">Runtime</th>
                  <th className="px-4 py-3 text-right">SOC start→stop</th>
                  <th className="px-4 py-3">Reason</th>
                  <th className="px-4 py-3">Operator</th>
                  <th className="px-4 py-3 text-right">Depleted</th>
                  <th className="px-4 py-3 text-right">kg/hr</th>
                  <th className="px-4 py-3 text-right">kg/kWh</th>
                  <th className="px-4 py-3">Batch</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {runs.length === 0 && (
                  <tr><td colSpan={10} className="px-4 py-6 text-center text-gray-400">No runs logged yet.</td></tr>
                )}
                {runs.map((r) => (
                  <tr key={r.id} className={`hover:bg-gray-50 ${r.status === 'running' ? 'bg-green-50' : ''}`}>
                    <td className="px-4 py-3 text-gray-600">{fmtDateTime(r.started_at)}</td>
                    <td className="px-4 py-3 text-gray-600">{r.status === 'running' ? <span className="text-green-600">running…</span> : fmtDateTime(r.ended_at)}</td>
                    <td className="px-4 py-3 text-right text-gray-600">{fmtDuration(r.runtime_seconds)}</td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {r.start_soc_pct ?? '—'} → {r.stop_soc_pct ?? '—'}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{r.stop_reason || r.start_reason || '—'}</td>
                    <td className="px-4 py-3 text-gray-600">{r.stop_operator || r.start_operator || '—'}</td>
                    <td className="px-4 py-3 text-right">{r.lpg_depleted ? `${r.cylinders_consumed} ⛽` : '—'}</td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {(() => { const fe = fuelEconomy(r); return fe.kg_per_hour != null ? fe.kg_per_hour : '—'; })()}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {(() => { const fe = fuelEconomy(r); return fe.kg_per_kwh != null ? fe.kg_per_kwh : '—'; })()}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-500">{r.batch_number || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
