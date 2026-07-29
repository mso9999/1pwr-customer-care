import { useEffect, useState } from 'react';
import {
  updateDeviceConfig,
  getFactoryOtaReadiness,
  getOtaPromotionStatus,
  getProvisioningSiteCodes,
  getProvisioningRegistry,
  getProvisionedMeters,
  reconcileProvisioning,
  downloadProvisioningStation,
  type UpdateConfigResult,
  type OtaReadiness,
  type OtaPromotionStatus,
  type ProvisioningSiteCode,
  type ProvisioningRegistryRow,
  type ProvisionedMeter,
} from '../lib/api';

type Mode = 'guide' | 'config' | 'meters' | 'registry';
type ValidationNetworkMode = 'site' | 'mirror';
type GuideCheckKey =
  | 'sealed'
  | 'provisioningNetwork'
  | 'starlinkOnline'
  | 'credentialsTested'
  | 'terminalReady'
  | 'stationSignedIn'
  | 'canarySelected'
  | 'bootstrapDelivered'
  | 'otaQueued'
  | 'otaIdConfirmed'
  | 'otaSucceeded'
  | 'firmwareVerified';

const inputCls =
  'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none';
const labelCls = 'block text-xs font-medium text-gray-500 mb-1';

export default function ProvisioningPage() {
  const [mode, setMode] = useState<Mode>('guide');
  const [otaReadiness, setOtaReadiness] = useState<OtaReadiness | null>(null);
  const [otaReadinessError, setOtaReadinessError] = useState('');
  const [guideStep, setGuideStep] = useState(1);
  const [guideSites, setGuideSites] = useState<ProvisioningSiteCode[]>([]);
  const [guideSite, setGuideSite] = useState('');
  const [guideDownloaded, setGuideDownloaded] = useState(false);
  const [validationNetworkMode, setValidationNetworkMode] = useState<ValidationNetworkMode>('site');
  const [commandCopied, setCommandCopied] = useState(false);
  const [guideChecks, setGuideChecks] = useState<Record<GuideCheckKey, boolean>>({
    sealed: false,
    provisioningNetwork: false,
    starlinkOnline: false,
    credentialsTested: false,
    terminalReady: false,
    stationSignedIn: false,
    canarySelected: false,
    bootstrapDelivered: false,
    otaQueued: false,
    otaIdConfirmed: false,
    otaSucceeded: false,
    firmwareVerified: false,
  });
  const [trackedOtaId, setTrackedOtaId] = useState('');
  const [otaProgress, setOtaProgress] = useState<OtaPromotionStatus | null>(null);
  const [otaProgressError, setOtaProgressError] = useState('');

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
      setGuideDownloaded(true);
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

  useEffect(() => {
    getProvisioningSiteCodes()
      .then(setGuideSites)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!guideSite) {
      setOtaReadiness(null);
      setOtaReadinessError('');
      return;
    }
    let cancelled = false;
    setOtaReadiness(null);
    setOtaReadinessError('');
    getFactoryOtaReadiness(guideSite)
      .then((r) => { if (!cancelled) setOtaReadiness(r); })
      .catch((e) => {
        if (!cancelled) setOtaReadinessError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [guideSite]);

  useEffect(() => {
    if (guideStep < 5 || !guideSite) return;
    let cancelled = false;
    const discoverUpdate = () => {
      getProvisionedMeters(guideSite)
        .then((r) => {
          if (cancelled) return;
          const withOta = r.meters
            .filter((m) => m.ota_update_id)
            .sort((a, b) => String(b.ota_updated_at || b.provisioned_at || '')
              .localeCompare(String(a.ota_updated_at || a.provisioned_at || '')));
          if (withOta[0]?.ota_update_id) {
            setTrackedOtaId((current) => current || withOta[0].ota_update_id || '');
            setGuideChecks((prev) => ({ ...prev, otaQueued: true }));
          }
        })
        .catch(() => {});
    };
    discoverUpdate();
    const timer = window.setInterval(discoverUpdate, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [guideStep, guideSite]);

  useEffect(() => {
    if (!trackedOtaId) {
      setOtaProgress(null);
      return;
    }
    let cancelled = false;
    const poll = () => {
      getOtaPromotionStatus(trackedOtaId)
        .then((r) => {
          if (cancelled) return;
          setOtaProgress(r);
          setOtaProgressError('');
          const expected = r.executions.length || r.targets.length;
          const succeeded = r.executions.filter((x) => x.status === 'SUCCEEDED').length;
          if (expected > 0 && succeeded === expected) {
            setGuideChecks((prev) => ({ ...prev, otaSucceeded: true }));
          }
        })
        .catch((e) => {
          if (!cancelled) setOtaProgressError(e instanceof Error ? e.message : String(e));
        });
    };
    poll();
    const timer = window.setInterval(poll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [trackedOtaId]);

  const toggleGuideCheck = (key: GuideCheckKey) => {
    setGuideChecks((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const copyStationCommand = async () => {
    await navigator.clipboard.writeText(`py -3 provisioning_station.py --cc ${window.location.origin}`);
    setCommandCopied(true);
    window.setTimeout(() => setCommandCopied(false), 2000);
  };

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

  const guideSteps = [
    'Choose site',
    'Prepare networks',
    'Start station',
    'Discover canary',
    'Provision & OTA',
    'Verify & finish',
  ];
  const otaExpected = otaProgress?.executions.length || otaProgress?.targets.length || 0;
  const otaSucceeded = otaProgress?.executions.filter((x) => x.status === 'SUCCEEDED').length || 0;
  const otaInProgress = otaProgress?.executions.filter((x) => x.status === 'IN_PROGRESS').length || 0;
  const otaFailed = otaProgress?.executions.filter((x) =>
    ['FAILED', 'REJECTED', 'CANCELED', 'REMOVED'].includes(x.status || '')).length || 0;
  const otaQueued = Math.max(0, otaExpected - otaSucceeded - otaInProgress - otaFailed);
  const otaPercent = otaExpected ? Math.round((otaSucceeded / otaExpected) * 100) : 0;

  return (
    <div className="max-w-5xl mx-auto p-4 sm:p-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-gray-900">1Meter Provisioning</h1>
        <p className="text-sm text-gray-500 mt-1">
          Bring factory-boot gateways onto their recognized provisioning LAN, give them stable
          <code className="text-gray-700"> &lt;SITE&gt;-GW-####</code> identities, and promote them to
          approved full firmware over signed OTA. Gateway names never change.
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
        <div className="space-y-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
              {guideSteps.map((label, index) => {
                const number = index + 1;
                const active = number === guideStep;
                const complete = number < guideStep;
                return (
                  <button
                    key={label}
                    onClick={() => { if (number <= guideStep) setGuideStep(number); }}
                    className={`rounded-lg border px-2 py-2 text-left transition-colors ${
                      active ? 'border-blue-500 bg-blue-50 text-blue-800'
                        : complete ? 'border-green-200 bg-green-50 text-green-800'
                          : 'border-gray-200 bg-gray-50 text-gray-400'
                    }`}
                  >
                    <div className="text-xs font-semibold">{complete ? '✓' : number} · {label}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5">
            {guideStep === 1 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Step 1 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Choose the destination site</h2>
                  <p className="text-sm text-gray-600 mt-1">
                    CC checks the exact signed firmware approved for this site before allowing the operator to continue.
                  </p>
                </div>
                <div className="max-w-xl">
                  <label className={labelCls}>Destination site</label>
                  <select className={inputCls} value={guideSite} onChange={(e) => {
                    setGuideSite(e.target.value);
                    setTrackedOtaId('');
                    setOtaProgress(null);
                    setGuideChecks((prev) => ({ ...prev, otaIdConfirmed: false, otaSucceeded: false }));
                  }}>
                    <option value="">Select the actual deployment site…</option>
                    {guideSites.map((site) => (
                      <option key={site.code} value={site.code}>
                        {site.code} — {site.name}{site.country ? ` (${site.country})` : ''}
                      </option>
                    ))}
                  </select>
                </div>
                {!guideSite ? (
                  <div className="p-3 rounded-lg border border-gray-200 bg-gray-50 text-sm text-gray-600">
                    Select a site to run the release, artifact, signer, and anti-rollback checks.
                  </div>
                ) : !otaReadiness && !otaReadinessError ? (
                  <div className="p-3 rounded-lg border border-blue-200 bg-blue-50 text-sm text-blue-800">
                    Checking this site’s approved OTA release…
                  </div>
                ) : otaReadiness?.ready ? (
                  <div className="p-4 rounded-lg border border-green-200 bg-green-50 text-sm text-green-900">
                    <div className="font-semibold">✓ Site release ready</div>
                    <div className="mt-1">
                      Target firmware <b>{otaReadiness.release.target_firmware_version}</b> is newer than factory baseline
                      {' '}<b>{otaReadiness.release.factory_baseline_version}</b>; the immutable artifact and active signing profile passed.
                    </div>
                  </div>
                ) : (
                  <div className="p-4 rounded-lg border border-red-200 bg-red-50 text-sm text-red-900">
                    <div className="font-semibold">Stop — this site is not ready for provisioning</div>
                    <div className="mt-1">
                      {otaReadinessError || `Missing/failed: ${[
                        ...(otaReadiness?.missing || []),
                        ...Object.entries(otaReadiness?.checks || {}).filter(([, value]) => !value.ok).map(([key]) => key),
                      ].join(', ') || 'unknown readiness check'}.`}
                      {' '}Engineering must approve a valid release before the portal unlocks the next step.
                    </div>
                  </div>
                )}
                <div className="flex justify-end">
                  <button
                    disabled={!otaReadiness?.ready}
                    onClick={() => setGuideStep(2)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-40"
                  >
                    Continue to network preparation
                  </button>
                </div>
              </div>
            )}

            {guideStep === 2 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Step 2 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Prepare both Wi-Fi networks</h2>
                  <p className="text-sm text-gray-600 mt-1">
                    The gateway starts on <b>1Meter</b>, then leaves it and joins the selected site’s actual Starlink network.
                  </p>
                </div>
                <div className="grid md:grid-cols-2 gap-4">
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                    <div className="font-semibold text-blue-900">1 · Provisioning network</div>
                    <div className="text-sm text-blue-800 mt-1">SSID <code>1Meter</code> · password <code>1Meter00</code></div>
                    <div className="text-xs text-blue-700 mt-2">Laptop and sealed gateways must be on this LAN; client isolation must be off.</div>
                  </div>
                  <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                    <div className="font-semibold text-indigo-900">2 · OTA handoff network</div>
                    <div className="text-sm text-indigo-800 mt-1">Use the real {guideSite} Starlink, or optionally mirror its exact credentials on an HQ access point.</div>
                    <div className="text-xs text-indigo-700 mt-2">The operator enters the credentials only in the station. CC never stores the password.</div>
                  </div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-800 mb-2">Where will this canary receive its OTA?</div>
                  <div className="grid md:grid-cols-2 gap-3">
                    <label className={`p-4 rounded-lg border cursor-pointer ${validationNetworkMode === 'site' ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                      <div className="flex gap-2">
                        <input type="radio" name="validationNetwork" checked={validationNetworkMode === 'site'}
                          onChange={() => {
                            setValidationNetworkMode('site');
                            setGuideChecks((prev) => ({ ...prev, starlinkOnline: false, credentialsTested: false }));
                          }} />
                        <div>
                          <div className="text-sm font-semibold text-gray-900">Actual site Starlink</div>
                          <div className="text-xs text-gray-600 mt-1">Preferred when provisioning at the deployment site. This tests the real router and internet path.</div>
                        </div>
                      </div>
                    </label>
                    <label className={`p-4 rounded-lg border cursor-pointer ${validationNetworkMode === 'mirror' ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                      <div className="flex gap-2">
                        <input type="radio" name="validationNetwork" checked={validationNetworkMode === 'mirror'}
                          onChange={() => {
                            setValidationNetworkMode('mirror');
                            setGuideChecks((prev) => ({ ...prev, starlinkOnline: false, credentialsTested: false }));
                          }} />
                        <div>
                          <div className="text-sm font-semibold text-gray-900">Optional HQ mirrored network</div>
                          <div className="text-xs text-gray-600 mt-1">Reproduce the site SSID/password at HQ to validate credential handoff and OTA before shipment.</div>
                        </div>
                      </div>
                    </label>
                  </div>
                </div>
                {validationNetworkMode === 'mirror' && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                    <div className="font-semibold">Set up the HQ mirror</div>
                    <ol className="list-decimal ml-5 mt-2 space-y-1.5">
                      <li>Use a controlled spare router/access point that supports <b>2.4 GHz</b>.</li>
                      <li>Set its Wi-Fi name to the site Starlink SSID <b>exactly</b>, including capitalization and spaces.</li>
                      <li>Set the identical WPA2 password. Do not enter or save that password in CC, chat, screenshots, or documentation.</li>
                      <li>Give the mirror internet access; disable captive portal, guest isolation, and client isolation. Allow DNS/NTP plus outbound AWS IoT MQTT on TCP 8883.</li>
                      <li>Test the mirrored credentials and internet from a phone/laptop, then remove that test device.</li>
                      <li>Keep the mirror within range during bootstrap and OTA. Avoid broadcasting both the real and mirrored SSID in the same test area.</li>
                      <li>After the OTA succeeds, power down and label the mirror as test-only. The actual site Starlink must still be validated during installation.</li>
                    </ol>
                    <div className="mt-3 font-medium">This proves the gateway accepts the site credentials and can complete OTA. It does not prove the real Starlink router, site RF conditions, or site internet path.</div>
                  </div>
                )}
                <div className="space-y-2">
                  {([
                    ['sealed', 'The gateways are sealed factory units; no USB cable will be used.'],
                    ['provisioningNetwork', 'The laptop and canary gateway are on the 1Meter provisioning LAN, with CC internet access.'],
                    ['starlinkOnline', validationNetworkMode === 'site'
                      ? `The actual ${guideSite} Starlink router is powered, online, and within range.`
                      : 'The controlled HQ mirror is powered, online, within range, and no competing copy of that SSID is present.'],
                    ['credentialsTested', validationNetworkMode === 'site'
                      ? 'The exact Starlink SSID/password were tested successfully on another device.'
                      : 'The mirrored SSID/password exactly match the site record and internet access was tested.'],
                  ] as [GuideCheckKey, string][]).map(([key, label]) => (
                    <label key={key} className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                      <input type="checkbox" className="mt-0.5" checked={guideChecks[key]} onChange={() => toggleGuideCheck(key)} />
                      <span className="text-sm text-gray-700">{label}</span>
                    </label>
                  ))}
                </div>
                <div className="flex justify-between">
                  <button onClick={() => setGuideStep(1)} className="px-4 py-2 rounded-lg border text-sm">Back</button>
                  <button
                    disabled={!['sealed', 'provisioningNetwork', 'starlinkOnline', 'credentialsTested']
                      .every((key) => guideChecks[key as GuideCheckKey])}
                    onClick={() => setGuideStep(3)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-40"
                  >
                    Networks ready — continue
                  </button>
                </div>
              </div>
            )}

            {guideStep === 3 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Step 3 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Download and start the station</h2>
                  <p className="text-sm text-gray-600 mt-1">The local station lets the laptop reach factory gateways without exposing device credentials to the browser.</p>
                </div>
                <div className="flex flex-wrap gap-3">
                  <button onClick={handleDownloadStation} disabled={downloading}
                    className="py-2.5 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-50">
                    {downloading ? 'Preparing…' : guideDownloaded ? '✓ Download again' : '1. Download station (.zip)'}
                  </button>
                  <div className="text-sm text-gray-600 self-center">Extract the entire ZIP before running it.</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-800 mb-1">2. Run in PowerShell from the extracted folder</div>
                  <div className="flex gap-2">
                    <code className="flex-1 p-3 rounded-lg bg-gray-900 text-gray-100 text-xs overflow-x-auto">
                      py -3 provisioning_station.py --cc {window.location.origin}
                    </code>
                    <button onClick={copyStationCommand} className="px-3 rounded-lg border text-xs">
                      {commandCopied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                </div>
                <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-900">
                  A terminal ending with <code>Open: http://localhost:8787</code> is working correctly. It is supposed to remain open and appear idle.
                </div>
                <label className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                  <input type="checkbox" className="mt-0.5" checked={guideChecks.terminalReady} onChange={() => toggleGuideCheck('terminalReady')} />
                  <span className="text-sm text-gray-700">The terminal shows <code>Open: http://localhost:8787</code> and remains open.</span>
                </label>
                <div className="flex justify-between">
                  <button onClick={() => setGuideStep(2)} className="px-4 py-2 rounded-lg border text-sm">Back</button>
                  <button
                    disabled={!guideDownloaded || !guideChecks.terminalReady}
                    onClick={() => setGuideStep(4)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-40"
                  >
                    Station running — continue
                  </button>
                </div>
              </div>
            )}

            {guideStep === 4 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Step 4 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Discover one canary gateway</h2>
                  <p className="text-sm text-gray-600 mt-1">Start with one known unit. Do not select a row with an unknown MAC or an uncertain serial match.</p>
                </div>
                <a href="http://localhost:8787" target="_blank" rel="noreferrer"
                  className="inline-flex px-4 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-medium">
                  Open station at localhost:8787
                </a>
                <ol className="list-decimal ml-5 space-y-2 text-sm text-gray-700">
                  <li>Sign in to the station with the same CC employee account.</li>
                  <li>Confirm the subnet is the provisioning LAN and click <b>Scan for gateways</b>.</li>
                  <li>Match the printed factory serial to the STA/PCB MAC from the shipment manifest.</li>
                  <li>Select exactly one known <b>virgin</b> gateway as the canary.</li>
                  <li>Select destination site <b>{guideSite}</b> and enter that site’s exact Starlink SSID/password. {validationNetworkMode === 'mirror' ? 'The HQ mirror must be broadcasting those identical credentials.' : 'The actual site router must be broadcasting them.'}</li>
                </ol>
                <div className="space-y-2">
                  {([
                    ['stationSignedIn', 'The station is signed in and its OTA readiness card says ready.'],
                    ['canarySelected', 'Exactly one verified virgin gateway is selected as the canary.'],
                  ] as [GuideCheckKey, string][]).map(([key, label]) => (
                    <label key={key} className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                      <input type="checkbox" className="mt-0.5" checked={guideChecks[key]} onChange={() => toggleGuideCheck(key)} />
                      <span className="text-sm text-gray-700">{label}</span>
                    </label>
                  ))}
                </div>
                <div className="flex justify-between">
                  <button onClick={() => setGuideStep(3)} className="px-4 py-2 rounded-lg border text-sm">Back</button>
                  <button
                    disabled={!guideChecks.stationSignedIn || !guideChecks.canarySelected}
                    onClick={() => setGuideStep(5)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-40"
                  >
                    Canary selected — continue
                  </button>
                </div>
              </div>
            )}

            {guideStep === 5 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Step 5 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Provision and monitor the OTA</h2>
                  <p className="text-sm text-gray-600 mt-1">
                    Confirm provisioning in the station. CC automatically discovers the newest {guideSite} OTA update and refreshes its AWS status every five seconds.
                  </p>
                </div>
                <div className="p-3 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-900">
                  Keep the gateway and {validationNetworkMode === 'mirror' ? 'HQ mirrored access point' : 'site Starlink router'} powered. “Bootstrap delivered” is not completion.
                </div>
                <label className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                  <input type="checkbox" className="mt-0.5" checked={guideChecks.bootstrapDelivered} onChange={() => toggleGuideCheck('bootstrapDelivered')} />
                  <span className="text-sm text-gray-700">The station shows identity/bootstrap delivery completed and the gateway rebooted.</span>
                </label>
                <div>
                  <label className={labelCls}>OTA update ID</label>
                  <input
                    className={inputCls}
                    value={trackedOtaId}
                    onChange={(e) => {
                      setTrackedOtaId(e.target.value.trim());
                      setGuideChecks((prev) => ({ ...prev, otaIdConfirmed: false, otaSucceeded: false }));
                    }}
                    placeholder="Auto-detecting from CC, or paste the ID shown by the station"
                  />
                  <p className="text-xs text-gray-400 mt-1">Auto-detection uses the newest OTA recorded for site {guideSite}. Confirm the ID matches the station.</p>
                </div>
                {trackedOtaId && (
                  <label className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                    <input type="checkbox" className="mt-0.5" checked={guideChecks.otaIdConfirmed} onChange={() => toggleGuideCheck('otaIdConfirmed')} />
                    <span className="text-sm text-gray-700">This OTA update ID exactly matches the ID shown by the station for this canary.</span>
                  </label>
                )}

                {!trackedOtaId ? (
                  <div className="p-4 rounded-lg border border-blue-200 bg-blue-50 text-sm text-blue-800">
                    Waiting for CC to record the OTA update…
                  </div>
                ) : otaProgressError ? (
                  <div className="p-4 rounded-lg border border-red-200 bg-red-50 text-sm text-red-900">
                    Could not read OTA status: {otaProgressError}
                  </div>
                ) : (
                  <div className="rounded-xl border border-gray-200 overflow-hidden">
                    <div className="p-4 bg-gray-50 border-b border-gray-200">
                      <div className="flex justify-between gap-4 text-sm">
                        <div>
                          <div className="font-semibold text-gray-900">Full-firmware OTA {otaProgress?.target_version || ''}</div>
                          <div className="font-mono text-xs text-gray-500 mt-0.5">{trackedOtaId}</div>
                        </div>
                        <div className={`font-semibold ${otaFailed ? 'text-red-700' : otaPercent === 100 ? 'text-green-700' : 'text-blue-700'}`}>
                          {otaPercent}% complete
                        </div>
                      </div>
                      <div className="h-3 bg-gray-200 rounded-full overflow-hidden mt-3">
                        <div
                          className={`h-full transition-all duration-500 ${otaFailed ? 'bg-red-500' : otaPercent === 100 ? 'bg-green-500' : 'bg-blue-600'}`}
                          style={{ width: `${otaPercent}%` }}
                        />
                      </div>
                      <div className="grid grid-cols-4 gap-2 mt-3 text-center text-xs">
                        <div className="rounded bg-gray-100 p-2"><b>{otaQueued}</b><br />Queued</div>
                        <div className="rounded bg-blue-100 text-blue-800 p-2"><b>{otaInProgress}</b><br />In progress</div>
                        <div className="rounded bg-green-100 text-green-800 p-2"><b>{otaSucceeded}</b><br />Succeeded</div>
                        <div className="rounded bg-red-100 text-red-800 p-2"><b>{otaFailed}</b><br />Failed</div>
                      </div>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="text-xs uppercase text-gray-500 bg-white">
                          <tr><th className="text-left px-4 py-2">Gateway</th><th className="text-left px-4 py-2">Status</th><th className="text-left px-4 py-2">Last update</th></tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {(otaProgress?.executions || []).map((execution) => (
                            <tr key={execution.thing_name}>
                              <td className="px-4 py-2 font-mono">{execution.thing_name}</td>
                              <td className="px-4 py-2">
                                <span className={`px-2 py-0.5 rounded-full text-xs ${
                                  execution.status === 'SUCCEEDED' ? 'bg-green-100 text-green-800'
                                    : execution.status === 'IN_PROGRESS' ? 'bg-blue-100 text-blue-800'
                                      : ['FAILED', 'REJECTED', 'CANCELED', 'REMOVED'].includes(execution.status || '')
                                        ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-700'
                                }`}>{execution.status || 'QUEUED'}</span>
                              </td>
                              <td className="px-4 py-2 text-xs text-gray-500">{execution.last_updated_at || execution.queued_at || '—'}</td>
                            </tr>
                          ))}
                          {!otaProgress?.executions.length && (
                            <tr><td colSpan={3} className="px-4 py-5 text-center text-gray-400">AWS created the update; waiting for device Job executions.</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {otaFailed > 0 && (
                  <div className="p-4 rounded-lg border border-red-200 bg-red-50 text-sm text-red-900">
                    <b>Stop.</b> At least one gateway failed or rejected the OTA. Preserve the update ID and do not create another Thing or update blindly.
                  </div>
                )}
                <div className="flex justify-between">
                  <button onClick={() => setGuideStep(4)} className="px-4 py-2 rounded-lg border text-sm">Back</button>
                  <button
                    disabled={!guideChecks.bootstrapDelivered || !guideChecks.otaIdConfirmed || !guideChecks.otaSucceeded || otaFailed > 0}
                    onClick={() => setGuideStep(6)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-40"
                  >
                    OTA succeeded — verify
                  </button>
                </div>
              </div>
            )}

            {guideStep === 6 && (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-green-600">Step 6 of 6</div>
                  <h2 className="text-lg font-semibold text-gray-900 mt-1">Verify and release the canary</h2>
                  <p className="text-sm text-gray-600 mt-1">The OTA Job succeeded. Complete the operational checks before starting a larger batch.</p>
                </div>
                <div className="p-4 rounded-lg border border-green-200 bg-green-50 text-sm text-green-900">
                  <div className="font-semibold">✓ Signed firmware installed on {otaSucceeded}/{otaExpected} gateway(s)</div>
                  <div className="mt-1">Target: <b>{otaProgress?.target_version}</b> · Update: <span className="font-mono">{trackedOtaId}</span></div>
                </div>
                <label className="flex gap-3 items-start p-3 rounded-lg border border-gray-200 cursor-pointer">
                  <input type="checkbox" className="mt-0.5" checked={guideChecks.firmwareVerified} onChange={() => toggleGuideCheck('firmwareVerified')} />
                  <span className="text-sm text-gray-700">In Provisioned meters, the Thing, site, Starlink SSID, OTA ID/status, and installed target version are correct.</span>
                </label>
                {guideChecks.firmwareVerified && (
                  <div className="p-4 rounded-lg border border-green-300 bg-green-50 text-green-900">
                    <div className="font-semibold">{validationNetworkMode === 'mirror' ? 'HQ OTA validation complete' : 'Canary provisioning complete'}</div>
                    <div className="text-sm mt-1">
                      Record the result. {validationNetworkMode === 'mirror'
                        ? 'This gateway still requires an actual-site Starlink connectivity check during installation. '
                        : ''}
                      For a larger batch, return to Step 4 and increase gradually only after the canary remains healthy.
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap justify-between gap-2">
                  <button onClick={() => setGuideStep(5)} className="px-4 py-2 rounded-lg border text-sm">Back to OTA monitor</button>
                  <button onClick={() => setMode('meters')} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium">
                    Open Provisioned meters
                  </button>
                </div>
              </div>
            )}
          </div>

          <details className="bg-white rounded-xl border border-gray-200 p-4">
            <summary className="text-sm font-medium text-gray-800 cursor-pointer">Why CC uses a local station</summary>
            <p className="text-sm text-gray-600 mt-3">
              The CC page is HTTPS, while an unprovisioned gateway exposes a private-LAN HTTP API and has no cloud certificate.
              The station bridges that first local step. CC remains the system of record, release gate, OTA controller, and live status monitor.
              The downloaded folder also contains <b>START_HERE_FACTORY_VIRGIN_GATEWAYS.md</b>.
            </p>
          </details>
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
                  <th className="text-left px-4 py-2">Starlink Wi-Fi</th>
                  <th className="text-left px-4 py-2">Installed FW</th>
                  <th className="text-left px-4 py-2">OTA</th>
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
                    <td className="px-4 py-2 text-xs">
                      {r.deployment_wifi_ssid || '—'}
                      {r.wifi_config_version ? <span className="text-gray-400"> (cfg v{r.wifi_config_version})</span> : null}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">{r.fw_version || '—'}</td>
                    <td className="px-4 py-2 text-xs">
                      <div>{r.ota_status || '—'}{r.ota_target_version ? ` → ${r.ota_target_version}` : ''}</div>
                      {r.ota_update_id ? <div className="font-mono text-gray-400">{r.ota_update_id}</div> : null}
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
                  <tr><td colSpan={11} className="px-4 py-8 text-center text-gray-400">No provisioned meters yet.</td></tr>
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
