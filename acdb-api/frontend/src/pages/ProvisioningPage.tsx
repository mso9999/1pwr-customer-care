import { useEffect, useState } from 'react';
import {
  updateDeviceConfig,
  getProvisioningRegistry,
  getProvisionedMeters,
  reconcileProvisioning,
  downloadProvisioningStation,
  type UpdateConfigResult,
  type ProvisioningRegistryRow,
  type ProvisionedMeter,
} from '../lib/api';

type Mode = 'guide' | 'config' | 'meters' | 'registry';

const inputCls =
  'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none';
const labelCls = 'block text-xs font-medium text-gray-500 mb-1';

export default function ProvisioningPage() {
  const [mode, setMode] = useState<Mode>('guide');

  // config form state
  const [thingName, setThingName] = useState('');
  const [pcbMac, setPcbMac] = useState('');
  const [wifiSsid, setWifiSsid] = useState('');
  const [wifiPassword, setWifiPassword] = useState('');
  const [softapSsid, setSoftapSsid] = useState('');
  const [softapPassword, setSoftapPassword] = useState('');
  const [version, setVersion] = useState(1);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [configResult, setConfigResult] = useState<UpdateConfigResult | null>(null);

  const [registry, setRegistry] = useState<ProvisioningRegistryRow[]>([]);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [configRegistry, setConfigRegistry] = useState<ProvisioningRegistryRow[]>([]);
  const [configRegistryLoading, setConfigRegistryLoading] = useState(false);

  const [meters, setMeters] = useState<ProvisionedMeter[]>([]);
  const [metersLoading, setMetersLoading] = useState(false);

  const loadRegistry = () => {
    setRegistryLoading(true);
    getProvisioningRegistry()
      .then((r) => setRegistry(r.rows))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setRegistryLoading(false));
  };

  const loadConfigRegistry = () => {
    setConfigRegistryLoading(true);
    getProvisioningRegistry()
      .then((r) => setConfigRegistry(r.rows))
      .catch(() => {})
      .finally(() => setConfigRegistryLoading(false));
  };

  const [downloading, setDownloading] = useState(false);
  const handleDownloadStation = async () => {
    setError('');
    setDownloading(true);
    try {
      await downloadProvisioningStation();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };

  const loadMeters = () => {
    setMetersLoading(true);
    getProvisionedMeters()
      .then((r) => setMeters(r.meters))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setMetersLoading(false));
  };

  const [reconciling, setReconciling] = useState(false);
  const handleReconcile = async () => {
    setError('');
    setReconciling(true);
    try {
      const r = await reconcileProvisioning();
      setError('');
      alert(`Reconcile complete: matched ${r.matched_things} online things, updated ${r.rows_updated} rows.`);
      loadMeters();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReconciling(false);
    }
  };

  useEffect(() => {
    if (mode === 'registry') loadRegistry();
    if (mode === 'meters') loadMeters();
    if (mode === 'config') loadConfigRegistry();
  }, [mode]);

  const handleConfigThingSelect = (name: string) => {
    setThingName(name);
    const row = configRegistry.find((r) => r.thing_name === name);
    if (row?.pcb_mac) setPcbMac(row.pcb_mac);
  };

  const resetResults = () => {
    setError('');
    setConfigResult(null);
  };

  const handleUpdateConfig = async () => {
    resetResults();
    setBusy(true);
    try {
      const r = await updateDeviceConfig({
        thing_name: thingName,
        wifi_ssid: wifiSsid,
        wifi_password: wifiPassword,
        softap_ssid: softapSsid || undefined,
        softap_password: softapPassword || undefined,
        version,
      });
      setConfigResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto p-4 sm:p-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-gray-900">1Meter Provisioning</h1>
        <p className="text-sm text-gray-500 mt-1">
          Provision gateway PCBs with stable <code className="text-gray-700">&lt;SITE&gt;-GW-####</code> identities via the
          provisioning station, then manage WiFi configuration here. Gateway names never change.
        </p>
      </div>

      <div className="flex gap-1 mb-5 border-b border-gray-200">
        {([
          ['guide', 'Guide & download'],
          ['config', 'Update Configuration'],
          ['meters', 'Provisioned meters'],
          ['registry', 'Registry'],
        ] as [Mode, string][]).map(([m, label]) => (
          <button
            key={m}
            onClick={() => { setMode(m); resetResults(); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              mode === m
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm whitespace-pre-wrap">
          {error}
        </div>
      )}

      {mode === 'guide' ? (
        <div className="grid md:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 style={{ marginTop: 0 }} className="text-sm font-semibold text-gray-900">Provisioning station (laptop app)</h3>
            <p className="text-sm text-gray-600 mt-1">
              Batch-provisioning happens from a small app the technician runs on a laptop that is on
              the <code>1Meter</code> provisioning Wi-Fi <b>and</b> has internet to CC. A virgin gateway
              has no certificate, so CC can't reach it directly — the station bridges the local network
              while CC issues the identities and records everything.
            </p>
            <div className="mt-3">
              <button onClick={handleDownloadStation} disabled={downloading}
                className="py-2.5 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                {downloading ? 'Preparing…' : 'Download provisioning station (.zip)'}
              </button>
            </div>
            <div className="mt-4 text-sm text-gray-600">
              <div className="font-medium text-gray-800 mb-1">Run it</div>
              <ol className="list-decimal ml-5 space-y-1">
                <li>Unzip; needs Python 3.9+ (no install).</li>
                <li>Join the <code>1Meter</code> / <code>1Meter00</code> provisioning Wi-Fi (keep internet).</li>
                <li><code>python3 provisioning_station.py --cc {window.location.origin}</code></li>
                <li>Open <code>http://localhost:8787</code> and sign in with your CC login.</li>
              </ol>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 style={{ marginTop: 0 }} className="text-sm font-semibold text-gray-900">How the workstream works</h3>
            <ol className="list-decimal ml-5 space-y-2 text-sm text-gray-600 mt-2">
              <li><b>Factory</b> flashes the universal image (LED heartbeat = good) and seals the units. No identity is set at the factory.</li>
              <li><b>Batch provision</b> at the depot with the station: it scans the network, lists virgin gateways, you pick a destination <b>site</b> + Wi-Fi, confirm, and CC issues stable <code>&lt;SITE&gt;-GW-####</code> Things + certs (no customer account yet). The station writes each bootstrap to the device.</li>
              <li><b>Install</b> the gateway in the meter box. On the site Wi-Fi it reaches AWS IoT and <b>auto-acquires</b> its meter serial from telemetry.</li>
              <li><b>Commission</b> in CC: link the meter serial to the customer account via the normal assign-meter flow. The gateway name never changes.</li>
            </ol>
            <p className="text-sm text-gray-500 mt-3">
              Lifecycle: <span className="state seg-unallocated">provisioned</span> →
              <span className="state seg-online"> online</span> →
              <span className="state seg-serial-acquired"> serial-acquired</span> →
              <span className="state seg-allocated"> allocated</span>. Track it in the
              <b> Provisioned meters</b> tab. Full detail: <b>Help → Provisioning</b> (<code>/help#provisioning</code>)
              and the operational SOP.
            </p>
          </div>
        </div>
      ) : mode === 'meters' ? (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <span className="text-sm font-medium text-gray-700">
              Provisioned meters &amp; locational assignment {meters.length ? `(${meters.length})` : ''}
            </span>
            <div className="flex items-center gap-3">
              <button onClick={handleReconcile} disabled={reconciling}
                className="text-xs px-3 py-1.5 bg-gray-100 rounded-md hover:bg-gray-200 disabled:opacity-50"
                title="Bind online gateways to their acquired meter serial (from telemetry)">
                {reconciling ? 'Reconciling…' : 'Reconcile from telemetry'}
              </button>
              <button onClick={loadMeters} className="text-xs text-blue-600 hover:underline">
                {metersLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2">Thing</th>
                  <th className="text-left px-4 py-2">Serial</th>
                  <th className="text-left px-4 py-2">Site</th>
                  <th className="text-left px-4 py-2">Account</th>
                  <th className="text-left px-4 py-2">Village</th>
                  <th className="text-left px-4 py-2">GPS</th>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-left px-4 py-2">Provisioned</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {meters.map((r, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-gray-900">{r.thing_name}</td>
                    <td className="px-4 py-2 font-mono">{r.meter_serial || '—'}</td>
                    <td className="px-4 py-2">{r.site || r.meter_community || '—'}</td>
                    <td className="px-4 py-2 font-mono">{r.account_number || '—'}</td>
                    <td className="px-4 py-2">{r.village_name || '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {r.latitude && r.longitude ? `${r.latitude}, ${r.longitude}` : '—'}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        r.status === 'provisioned' ? 'bg-green-100 text-green-700'
                          : r.status === 'rotating' ? 'bg-amber-100 text-amber-700'
                          : 'bg-gray-100 text-gray-600'
                      }`}>{String(r.status || '—')}</span>
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500">{r.provisioned_at || '—'}</td>
                  </tr>
                ))}
                {!meters.length && !metersLoading && (
                  <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">No provisioned meters yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : mode === 'registry' ? (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <span className="text-sm font-medium text-gray-700">
              Provisioning registry {registry.length ? `(${registry.length})` : ''}
            </span>
            <button onClick={loadRegistry} className="text-xs text-blue-600 hover:underline">
              {registryLoading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2">Thing</th>
                  <th className="text-left px-4 py-2">Meter serial</th>
                  <th className="text-left px-4 py-2">Site</th>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-left px-4 py-2">PCB MAC</th>
                  <th className="text-left px-4 py-2">Provisioned</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {registry.map((r, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-gray-900">{r.thing_name}</td>
                    <td className="px-4 py-2 font-mono">{r.meter_serial || '—'}</td>
                    <td className="px-4 py-2">{r.site || '—'}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        r.status === 'provisioned' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                      }`}>{String(r.status || '—')}</span>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-gray-500">{r.pcb_mac || '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">{r.provisioned_at || r.claimed_at || '—'}</td>
                  </tr>
                ))}
                {!registry.length && !registryLoading && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">No registry entries.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div>
              <label className={labelCls}>Gateway Thing (permanent identity)</label>
              <select className={inputCls} value={thingName} onChange={(e) => handleConfigThingSelect(e.target.value)}
                disabled={configRegistryLoading}>
                <option value="">{configRegistryLoading ? 'Loading registry…' : 'Select provisioned gateway…'}</option>
                {configRegistry.map((r) => (
                  <option key={r.thing_name} value={r.thing_name}>
                    {r.thing_name}{r.pcb_mac ? ` — ${r.pcb_mac}` : ''}
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-400 mt-1">
                Select the gateway's permanent Thing name. PCB MAC auto-fills from the registry.
                The Thing name is never changed — only WiFi/SoftAP settings are updated.
              </p>
            </div>

            <div>
              <label className={labelCls}>PCB MAC (read-only)</label>
              <input className={`${inputCls} bg-gray-50`} value={pcbMac} readOnly
                placeholder="auto-filled from registry" />
            </div>

            <div className="border-t border-gray-100 pt-3">
              <p className="text-xs font-medium text-gray-500 mb-3">Site Wi-Fi (STA mode)</p>
              <div className="space-y-3">
                <div>
                  <label className={labelCls}>Wi-Fi SSID</label>
                  <input className={inputCls} value={wifiSsid} onChange={(e) => setWifiSsid(e.target.value)}
                    placeholder="MAK_Wifi-ext" />
                </div>
                <div>
                  <label className={labelCls}>Wi-Fi password</label>
                  <input className={inputCls} value={wifiPassword} onChange={(e) => setWifiPassword(e.target.value)} />
                </div>
              </div>
            </div>

            <div className="border-t border-gray-100 pt-3">
              <p className="text-xs font-medium text-gray-500 mb-3">SoftAP (optional — device hotspot)</p>
              <div className="space-y-3">
                <div>
                  <label className={labelCls}>SoftAP SSID</label>
                  <input className={inputCls} value={softapSsid} onChange={(e) => setSoftapSsid(e.target.value)}
                    placeholder="1Meter_aabbcc" />
                </div>
                <div>
                  <label className={labelCls}>SoftAP password</label>
                  <input className={inputCls} value={softapPassword} onChange={(e) => setSoftapPassword(e.target.value)} />
                </div>
              </div>
            </div>

            <div>
              <label className={labelCls}>Config version</label>
              <input type="number" min={1} className={inputCls} value={version}
                onChange={(e) => setVersion(parseInt(e.target.value || '1', 10))} />
              <p className="text-xs text-gray-400 mt-1">
                Must be higher than the device's current config version for it to accept the update.
              </p>
            </div>

            <button
              onClick={handleUpdateConfig}
              disabled={busy || !thingName || !wifiSsid}
              className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
            >
              {busy ? 'Publishing…' : 'Publish config update'}
            </button>
          </div>

          <div className="space-y-4">
            {configResult && (
              <div className="bg-white rounded-xl border border-green-200 p-5 text-sm">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="text-sm font-semibold text-gray-900">
                    Config published to {configResult.thing_name}
                  </span>
                </div>
                <dl className="text-xs text-gray-600 space-y-1">
                  <div><span className="text-gray-400">Published to: </span><span className="font-mono">{configResult.published_topic}</span></div>
                  <div><span className="text-gray-400">Watch ack: </span><span className="font-mono">{configResult.ack_topic}</span></div>
                  <div><span className="text-gray-400">Version: </span>{configResult.version}</div>
                </dl>
                <p className="text-xs text-gray-500 mt-2">{configResult.note}</p>
              </div>
            )}

            {!configResult && (
              <div className="bg-gray-50 rounded-xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400">
                Select a provisioned gateway and enter the new WiFi settings. The config is published
                via MQTT — the device applies it and reconnects. The Thing name and certificates are not touched.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
