import { useEffect, useMemo, useState } from 'react';
import {
  getProvisioningSiteCodes,
  provisionThing,
  rotateMeterIdentity,
  getProvisioningRegistry,
  type ProvisioningSiteCode,
  type ProvisionResult,
  type RotateResult,
  type ProvisioningRegistryRow,
} from '../lib/api';

type Mode = 'provision' | 'rotate' | 'registry';

const inputCls =
  'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none';
const labelCls = 'block text-xs font-medium text-gray-500 mb-1';

function deriveThingName(site: string, account: string): string {
  if (!site) return '';
  const m = account.trim().toUpperCase().match(/^(\d+)/);
  return m ? `${site}-${m[1]}` : `${site}-?`;
}

export default function ProvisioningPage() {
  const [mode, setMode] = useState<Mode>('provision');
  const [sites, setSites] = useState<ProvisioningSiteCode[]>([]);
  const [sitesError, setSitesError] = useState('');

  // shared form state
  const [siteCode, setSiteCode] = useState('');
  const [account, setAccount] = useState('');
  const [meterSerial, setMeterSerial] = useState('');
  const [pcbMac, setPcbMac] = useState('');
  const [wifiSsid, setWifiSsid] = useState('');
  const [wifiPassword, setWifiPassword] = useState('');
  const [legacyId, setLegacyId] = useState('');
  const [version, setVersion] = useState(1);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ProvisionResult | null>(null);
  const [rotateResult, setRotateResult] = useState<RotateResult | null>(null);

  const [registry, setRegistry] = useState<ProvisioningRegistryRow[]>([]);
  const [registryLoading, setRegistryLoading] = useState(false);

  const previewThing = useMemo(() => deriveThingName(siteCode, account), [siteCode, account]);

  useEffect(() => {
    getProvisioningSiteCodes()
      .then(setSites)
      .catch((e) => setSitesError(e instanceof Error ? e.message : String(e)));
  }, []);

  const loadRegistry = () => {
    setRegistryLoading(true);
    getProvisioningRegistry()
      .then((r) => setRegistry(r.rows))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setRegistryLoading(false));
  };

  useEffect(() => {
    if (mode === 'registry') loadRegistry();
  }, [mode]);

  const resetResults = () => {
    setError('');
    setResult(null);
    setRotateResult(null);
  };

  const handleProvision = async () => {
    resetResults();
    setBusy(true);
    try {
      const r = await provisionThing({
        site_code: siteCode,
        account,
        meter_serial: meterSerial,
        pcb_mac: pcbMac,
        wifi_ssid: wifiSsid,
        wifi_password: wifiPassword,
        version,
        legacy_id: legacyId || undefined,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRotate = async () => {
    resetResults();
    setBusy(true);
    try {
      const r = await rotateMeterIdentity({
        current_client_id: legacyId,
        site_code: siteCode,
        account,
        meter_serial: meterSerial,
        pcb_mac: pcbMac,
        version,
      });
      setRotateResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const bootstrapJson = result ? JSON.stringify(result.bootstrap, null, 2) : '';

  const copyBootstrap = () => {
    if (bootstrapJson) navigator.clipboard.writeText(bootstrapJson);
  };

  const downloadBootstrap = () => {
    if (!result) return;
    const blob = new Blob([bootstrapJson], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `bootstrap-${result.thing_name}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-5xl mx-auto p-4 sm:p-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-gray-900">1Meter Provisioning</h1>
        <p className="text-sm text-gray-500 mt-1">
          Create a canonical AWS IoT Thing (<code className="text-gray-700">&lt;SITE&gt;-&lt;account&gt;</code>),
          issue its certificate, and generate the device bootstrap payload — no AWS CLI on the laptop.
        </p>
      </div>

      <div className="flex gap-1 mb-5 border-b border-gray-200">
        {([
          ['provision', 'Provision new unit'],
          ['rotate', 'Migrate / rename online unit'],
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

      {sitesError && (
        <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-sm">
          Could not load site codes: {sitesError}
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm whitespace-pre-wrap">
          {error}
        </div>
      )}

      {mode === 'registry' ? (
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
            {mode === 'rotate' && (
              <div>
                <label className={labelCls}>Current MQTT client id (the unit's name today)</label>
                <input className={inputCls} value={legacyId} onChange={(e) => setLegacyId(e.target.value)}
                  placeholder="TestSite4 / OneMeter43" />
                <p className="text-xs text-gray-400 mt-1">
                  Unit must be online with rotation-capable firmware. It reboots into the new name.
                </p>
              </div>
            )}

            <div>
              <label className={labelCls}>Site code (canonical, from CC)</label>
              <select className={inputCls} value={siteCode} onChange={(e) => setSiteCode(e.target.value)}>
                <option value="">Select site…</option>
                {sites.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.code} — {s.name}{s.country ? ` (${s.country})` : ''}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className={labelCls}>Account (CustomerID)</label>
              <input className={inputCls} value={account} onChange={(e) => setAccount(e.target.value)}
                placeholder="0026MAK or 0026" />
            </div>

            <div className="p-2.5 rounded-lg bg-blue-50 border border-blue-100 text-sm">
              <span className="text-gray-500">Thing name: </span>
              <span className="font-mono font-semibold text-blue-800">{previewThing || '—'}</span>
            </div>

            <div>
              <label className={labelCls}>Meter serial (Modbus SN)</label>
              <input className={inputCls} value={meterSerial} onChange={(e) => setMeterSerial(e.target.value)}
                placeholder="23022613" />
            </div>

            <div>
              <label className={labelCls}>PCB MAC (registry key)</label>
              <input className={inputCls} value={pcbMac} onChange={(e) => setPcbMac(e.target.value)}
                placeholder="aa:bb:cc:dd:ee:ff" />
            </div>

            {mode === 'provision' && (
              <>
                <div>
                  <label className={labelCls}>Site Wi-Fi SSID</label>
                  <input className={inputCls} value={wifiSsid} onChange={(e) => setWifiSsid(e.target.value)}
                    placeholder="MAK_Wifi-ext" />
                </div>
                <div>
                  <label className={labelCls}>Site Wi-Fi password</label>
                  <input className={inputCls} value={wifiPassword} onChange={(e) => setWifiPassword(e.target.value)} />
                </div>
                <div>
                  <label className={labelCls}>Legacy id (optional, recorded as attribute)</label>
                  <input className={inputCls} value={legacyId} onChange={(e) => setLegacyId(e.target.value)}
                    placeholder="TestSite4" />
                </div>
              </>
            )}

            <div>
              <label className={labelCls}>Identity version</label>
              <input type="number" min={1} className={inputCls} value={version}
                onChange={(e) => setVersion(parseInt(e.target.value || '1', 10))} />
            </div>

            <button
              onClick={mode === 'provision' ? handleProvision : handleRotate}
              disabled={busy}
              className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition"
            >
              {busy ? 'Working…' : mode === 'provision' ? 'Provision + issue cert' : 'Issue cert + publish rename'}
            </button>
          </div>

          <div className="space-y-4">
            {result && (
              <div className="bg-white rounded-xl border border-green-200 p-5">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="text-sm font-semibold text-gray-900">
                    Provisioned {result.thing_name}
                  </span>
                </div>
                <dl className="text-xs text-gray-600 space-y-1 mb-3">
                  <div><span className="text-gray-400">Cert ID: </span><span className="font-mono">{result.certificate_id}</span></div>
                  <div><span className="text-gray-400">Policy: </span>{result.policy}</div>
                  <div><span className="text-gray-400">Endpoint: </span><span className="font-mono">{result.mqtt_endpoint}</span></div>
                </dl>
                <p className="text-xs text-gray-500 mb-2">{result.instructions}</p>
                <div className="flex gap-2 mb-2">
                  <button onClick={copyBootstrap} className="text-xs px-3 py-1.5 bg-gray-100 rounded-md hover:bg-gray-200">Copy bootstrap JSON</button>
                  <button onClick={downloadBootstrap} className="text-xs px-3 py-1.5 bg-gray-100 rounded-md hover:bg-gray-200">Download .json</button>
                </div>
                <textarea
                  readOnly
                  value={bootstrapJson}
                  className="w-full h-48 font-mono text-[11px] p-3 border border-gray-200 rounded-lg bg-gray-50"
                />
              </div>
            )}

            {rotateResult && (
              <div className="bg-white rounded-xl border border-green-200 p-5 text-sm">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="font-semibold text-gray-900">
                    Rename published: {rotateResult.from_client_id} → {rotateResult.new_thing_name}
                  </span>
                </div>
                <dl className="text-xs text-gray-600 space-y-1">
                  <div><span className="text-gray-400">Published to: </span><span className="font-mono">{rotateResult.published_topic}</span></div>
                  <div><span className="text-gray-400">Watch ack: </span><span className="font-mono">{rotateResult.ack_topic}</span></div>
                  <div><span className="text-gray-400">Cert ID: </span><span className="font-mono">{rotateResult.certificate_id}</span></div>
                </dl>
                <p className="text-xs text-gray-500 mt-2">{rotateResult.note}</p>
              </div>
            )}

            {!result && !rotateResult && (
              <div className="bg-gray-50 rounded-xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400">
                {mode === 'provision'
                  ? 'Fill the form and provision to get the device bootstrap payload here.'
                  : 'Rename an online unit in place by publishing a new identity to its current client id.'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
