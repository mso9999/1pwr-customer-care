import { useEffect, useMemo, useState } from 'react';
import {
  getFleetLive,
  getProvisionedMeters,
  installGateway,
  listGatewayInstallations,
  type GatewayInstallation,
  type InstallGatewayResult,
  type ProvisionedMeter,
} from '../lib/api';
import UGPPolePicker from './UGPPolePicker';
import InstallTroubleshoot, { type TroubleshootContext } from './InstallTroubleshoot';

/**
 * Field install workflow: bind a provisioned gateway to a pole's PTB at
 * installation, then verify the gateway is live on the cloud. Meters are
 * assigned to the PTB's channels later (meter edit modal).
 *
 * Binding step: gateway (provisioned unit) + pole (map picker) → find-or-create
 * the PTB, record the install. Verification step: the installations list
 * re-checks the fleet index and flips awaiting_contact → verified on first
 * cloud contact.
 */
export default function FieldInstall() {
  const [site, setSite] = useState('MAK');
  const [prov, setProv] = useState<ProvisionedMeter[]>([]);
  const [installs, setInstalls] = useState<GatewayInstallation[]>([]);
  const [summary, setSummary] = useState<{ total: number; verified: number; awaiting_contact: number; connected_now: number } | null>(null);
  const [gateway, setGateway] = useState('');
  const [poleId, setPoleId] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InstallGatewayResult | null>(null);
  const [error, setError] = useState('');
  const [loadErr, setLoadErr] = useState('');
  const [trouble, setTrouble] = useState<TroubleshootContext | null>(null);
  const [watching, setWatching] = useState(false);
  const [recentUnits, setRecentUnits] = useState<{ thing: string; ageSec: number }[]>([]);

  const refreshInstalls = async (s: string) => {
    try {
      const r = await listGatewayInstallations(s);
      setInstalls(r.installations);
      setSummary(r.summary);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    setLoadErr('');
    getProvisionedMeters(site)
      .then((r) => setProv(r.meters || []))
      .catch(() => setProv([]));
    refreshInstalls(site);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site]);

  // Keep polling while any install is awaiting first cloud contact.
  useEffect(() => {
    if (!installs.some((i) => i.status === 'awaiting_contact')) return;
    const t = setInterval(() => refreshInstalls(site), 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installs, site]);

  // "Watch for a unit coming online": poll the fleet and surface only units that
  // CONNECTED within the last few minutes — that's how you tell the unit you just
  // powered from everything already online (differentiated by connect recency).
  useEffect(() => {
    if (!watching) return;
    const poll = async () => {
      try {
        const r = await getFleetLive();
        const nowMs = Date.now();
        const recent = (r.units || [])
          .filter((u) => u.connected && u.connect_ts && nowMs - u.connect_ts < 3 * 60 * 1000)
          .map((u) => ({ thing: u.thing_name, ageSec: Math.round((nowMs - (u.connect_ts || nowMs)) / 1000) }))
          .sort((a, b) => a.ageSec - b.ageSec);
        setRecentUnits(recent);
      } catch { /* keep polling */ }
    };
    poll();
    const t = setInterval(poll, 15000);
    return () => clearInterval(t);
  }, [watching]);

  // Gateway options: provisioned, non-test units; unassigned first, taken ones labeled.
  const gwOptions = useMemo(() => {
    const installedThings = new Set(installs.map((i) => i.gateway_thing));
    const rows = prov
      .filter((m) => (m.thing_name || '').includes('-GW-') && !m.is_test)
      .map((m) => {
        const name = String(m.thing_name);
        const linkedTo = m.account_number || (m.meter_serial ? `meter ${m.meter_serial}` : '');
        const installed = installedThings.has(name);
        const taken = installed || !!linkedTo;
        const label = installed
          ? `${name} — installed on ${installs.find((i) => i.gateway_thing === name)?.pole_id || 'a pole'}`
          : linkedTo
            ? `${name} — linked to ${linkedTo}`
            : name;
        return { name, taken, label };
      });
    rows.sort((a, b) => Number(a.taken) - Number(b.taken) || a.name.localeCompare(b.name));
    return rows;
  }, [prov, installs]);

  // Build the troubleshooter context from what CC already knows about the unit.
  const openTrouble = (gatewayThing: string, pole: string) => {
    const reg = prov.find((m) => m.thing_name === gatewayThing);
    const inst = installs.find((i) => i.gateway_thing === gatewayThing);
    setTrouble({
      gateway_thing: gatewayThing,
      pole_id: pole,
      site,
      expected_ssid: reg?.deployment_wifi_ssid || undefined,
      prov_status: reg?.status || undefined,
      ever_online: !!(inst?.first_online_at || inst?.connected),
      neighbors_online: installs.some((i) => i.gateway_thing !== gatewayThing && i.connected),
    });
  };

  const submit = async () => {
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const res = await installGateway({ site, gateway_thing: gateway, pole_id: poleId.trim() });
      setResult(res);
      refreshInstalls(site);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const statusBadge = (i: GatewayInstallation) => {
    if (i.status === 'verified') {
      return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">verified online</span>;
    }
    return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">awaiting contact</span>;
  };

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800">Install a gateway on a pole</h3>
          <p className="text-xs text-gray-500 mt-1">
            Bind the provisioned gateway unit to the pole's PTB when it's physically installed and powered.
            CC then verifies the gateway is live on the cloud. Meters are assigned to its channels afterwards.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Site</label>
            <input value={site} onChange={(e) => setSite(e.target.value.toUpperCase())} className="w-full px-3 py-2.5 border rounded-lg text-sm bg-white" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Gateway unit (provisioned)</label>
            <select value={gateway} onChange={(e) => setGateway(e.target.value)} className="w-full px-3 py-2.5 border rounded-lg text-sm bg-white">
              <option value="">Select gateway…</option>
              {gwOptions.map((g) => (
                <option key={g.name} value={g.name} disabled={g.taken}>{g.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Identify the physical unit with no cables: power it and watch which
            Thing connects. Differentiated from already-online units by connect
            recency — only units that connected in the last few minutes show. */}
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="text-xs text-gray-600">
              <span className="font-medium text-gray-700">Don't know which unit it is?</span> Power it and watch — the one that just connected is yours.
            </div>
            <button
              onClick={() => setWatching((w) => !w)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium ${watching ? 'bg-green-600 text-white' : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-100'}`}
            >
              {watching ? 'Watching… (tap to stop)' : 'Watch for a unit coming online'}
            </button>
          </div>
          {watching && (
            <div className="mt-2.5">
              {recentUnits.length === 0 ? (
                <div className="text-xs text-gray-500 py-1.5 flex items-center gap-2">
                  <span className="animate-spin inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full" />
                  Listening… power the unit now. Nothing new in the last 3 min.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {recentUnits.map((u) => (
                    <div key={u.thing} className="flex items-center justify-between gap-2 bg-white border border-green-200 rounded-lg px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                        <span className="font-mono text-sm font-medium text-gray-800">{u.thing}</span>
                        <span className="text-xs text-gray-500">connected {u.ageSec < 60 ? `${u.ageSec}s` : `${Math.round(u.ageSec / 60)}m`} ago</span>
                      </div>
                      <button
                        onClick={() => { setGateway(u.thing); setWatching(false); }}
                        className="px-2.5 py-1 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700"
                      >
                        Use this unit
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Pole (physical location)</label>
          <div className="flex gap-2">
            <input
              value={poleId}
              onChange={(e) => setPoleId(e.target.value)}
              placeholder="e.g. MAK_01_DA12"
              className="flex-1 px-3 py-2.5 border rounded-lg text-sm bg-white font-mono"
            />
            <button
              onClick={() => setPickerOpen(true)}
              className="px-3 py-2.5 bg-gray-100 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-200"
            >
              Pick on map
            </button>
          </div>
        </div>

        <button
          onClick={submit}
          disabled={busy || !gateway || !poleId.trim()}
          className="w-full py-3 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? 'Installing…' : 'Install & verify'}
        </button>

        {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm whitespace-pre-wrap">{error}</div>}

        {result && (
          <div className={`p-4 rounded-lg border ${result.verified ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}`}>
            <div className={`text-sm font-semibold ${result.verified ? 'text-green-800' : 'text-amber-800'}`}>
              {result.verified ? 'Installed — gateway is live on the cloud' : 'Installed — awaiting first cloud contact'}
            </div>
            <dl className="text-sm mt-2 space-y-1">
              <div className="flex justify-between"><dt className="text-gray-500">Gateway</dt><dd className="font-mono">{result.gateway_thing}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">Pole</dt><dd className="font-mono">{result.pole_id}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">PTB</dt><dd className="font-mono">{result.ptb_id}{result.ptb_created ? ' (created)' : ' (existing)'}</dd></div>
              {(result.meters_linked ?? 0) > 0 && (
                <div className="flex justify-between"><dt className="text-gray-500">Meters linked</dt><dd className="font-medium text-green-700">{result.meters_linked} on this pole completed</dd></div>
              )}
            </dl>
            <p className="text-xs text-gray-600 mt-2">{result.note}</p>
            {!result.verified && (
              <button
                onClick={() => openTrouble(result.gateway_thing, result.pole_id)}
                className="mt-3 w-full py-2.5 bg-amber-600 text-white rounded-xl text-sm font-semibold hover:bg-amber-700"
              >
                Run guided troubleshooting
              </button>
            )}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold text-gray-800">Installations</h3>
          <button onClick={() => refreshInstalls(site)} className="px-3 py-1.5 bg-gray-100 border border-gray-300 rounded-lg text-xs font-medium hover:bg-gray-200">
            Refresh
          </button>
        </div>
        {summary && (
          <div className="flex gap-4 text-xs text-gray-600 mb-3 flex-wrap">
            <span>{summary.total} installed</span>
            <span className="text-green-700 font-medium">{summary.verified} verified</span>
            <span className="text-amber-700 font-medium">{summary.awaiting_contact} awaiting contact</span>
            <span className="text-gray-400">{summary.connected_now} connected now</span>
          </div>
        )}
        {loadErr && <div className="p-2 text-xs text-red-600">{loadErr}</div>}
        <div className="divide-y divide-gray-100">
          {installs.length === 0 && <div className="py-6 text-center text-sm text-gray-400">No gateway installations recorded for {site} yet.</div>}
          {installs.map((i) => (
            <div key={i.gateway_thing} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="font-mono text-sm font-medium text-gray-800">{i.gateway_thing}</div>
                <div className="text-xs text-gray-500">
                  pole {i.pole_id}
                  {i.installed_at ? ` · installed ${new Date(i.installed_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}` : ''}
                  {i.last_contact_age_h != null && ` · last contact ${i.last_contact_age_h < 1 ? '<1' : i.last_contact_age_h}h ago`}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {i.connected && <span className="inline-block w-2 h-2 rounded-full bg-green-500" title="Connected now" />}
                {statusBadge(i)}
                {i.status === 'awaiting_contact' && (
                  <button
                    onClick={() => openTrouble(i.gateway_thing, i.pole_id)}
                    className="px-2.5 py-1 bg-amber-600 text-white rounded-lg text-xs font-medium hover:bg-amber-700"
                  >
                    Troubleshoot
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {pickerOpen && (
        <UGPPolePicker
          site={site}
          onSelect={(p) => { setPoleId(p.pole_id); setPickerOpen(false); }}
          onClose={() => setPickerOpen(false)}
        />
      )}

      {trouble && (
        <InstallTroubleshoot
          context={trouble}
          onClose={() => setTrouble(null)}
          onRecheck={() => refreshInstalls(site)}
        />
      )}
    </div>
  );
}
