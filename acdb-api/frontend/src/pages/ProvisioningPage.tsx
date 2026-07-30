import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  updateDeviceConfig,
  getFactoryOtaReadiness,
  getOtaPromotionStatus,
  getProvisioningSiteCodes,
  getProvisioningRegistry,
  getProvisionedMeters,
  reconcileProvisioning,
  downloadProvisioningStation,
  downloadMeterValidationKit,
  startFactoryOtaCanary,
  startMeterValidation,
  getMeterValidation,
  observeMeterValidationLoad,
  applyMeterValidationPayment,
  completeMeterValidation,
  getCountryProvisioningReadiness,
  updateProvisioningActivationStep,
  approveFactoryOtaRelease,
  type UpdateConfigResult,
  type OtaReadiness,
  type OtaPromotionStatus,
  type ProvisioningSiteCode,
  type ProvisioningRegistryRow,
  type ProvisionedMeter,
  type MeterValidationStatus,
  type CountryProvisioningReadiness,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

type Mode = 'walkthrough' | 'readiness' | 'guide' | 'canary' | 'batch-test' | 'config' | 'meters' | 'registry';
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

function SiteAdditionGuide({
  countryCode,
  countryName,
  open,
  showAdminLinks,
}: {
  countryCode?: string;
  countryName?: string;
  open?: boolean;
  showAdminLinks?: boolean;
}) {
  return (
    <details open={open} className="rounded-xl border border-amber-300 bg-amber-50 p-4">
      <summary className="cursor-pointer font-semibold text-amber-950">
        Site missing from the list? How to add a deployment site
      </summary>
      <div className="mt-3 space-y-3 text-sm text-amber-950">
        <p>
          A canonical site cannot be created from the provisioning dropdown. For{' '}
          <b>{countryName || countryCode || 'this country'}</b>, complete this controlled sequence:
        </p>
        <ol className="list-decimal pl-5 space-y-2">
          <li>
            <b>Country lead approves the site package:</b> official display name, proposed unique
            three-letter uppercase code, district/province, GPS coordinates if known, deployment
            lead, expected go-live date, and approved tariff/fees.
          </li>
          <li>
            <b>Engineering confirms the system mappings:</b> metering organisation and site ID,
            uGridPLAN project ID if used, payment scope, and the signed OTA release assignment.
          </li>
          <li>
            <b>Engineering adds and deploys the canonical country configuration.</b> This is the
            step that makes the site appear in CC. The uGridPLAN Sync and generation-site pages do
            not create the canonical code.
          </li>
          <li>
            After deployment, return here, select the country again, click <b>Refresh evidence</b>,
            and confirm the new site appears before creating customers, gateways, meters, or payments.
          </li>
          <li>
            Only after the code appears: add its uGridPLAN mapping where applicable, commission
            generation equipment/credentials, configure the tariff and metering IDs, and register
            the site-specific OTA candidate.
          </li>
        </ol>
        <div className="rounded-lg border border-red-200 bg-white p-3 text-red-800">
          Do not borrow another country&apos;s code, create a first customer to force a dropdown entry,
          or put Starlink/provider passwords in chat or evidence notes.
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/help#sites" className="px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-semibold">
            Open full site setup instructions
          </Link>
          {showAdminLinks && (
            <>
              <Link to="/sync" className="px-3 py-2 rounded-lg border bg-white text-xs font-semibold">
                Map uGridPLAN after activation
              </Link>
              <Link to="/gensite/commission" className="px-3 py-2 rounded-lg border bg-white text-xs font-semibold">
                Commission equipment after activation
              </Link>
            </>
          )}
        </div>
      </div>
    </details>
  );
}

export default function ProvisioningPage() {
  const { user } = useAuth();
  const [mode, setMode] = useState<Mode>('walkthrough');
  const [countryReadiness, setCountryReadiness] = useState<CountryProvisioningReadiness | null>(null);
  const [countryReadinessLoading, setCountryReadinessLoading] = useState(false);
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
  const [canaryTarget, setCanaryTarget] = useState('');
  const [canaryConfirmation, setCanaryConfirmation] = useState('');
  const [canaryNote, setCanaryNote] = useState('');
  const [canaryStarting, setCanaryStarting] = useState(false);
  const [approvalValidationId, setApprovalValidationId] = useState('');
  const [approvalWaive, setApprovalWaive] = useState(false);
  const [approvalWaiverReason, setApprovalWaiverReason] = useState('');
  const [approvalConfirmation, setApprovalConfirmation] = useState('');
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalSuccess, setApprovalSuccess] = useState('');
  const [validationTarget, setValidationTarget] = useState('');
  const [batchReference, setBatchReference] = useState('');
  const [startingCredit, setStartingCredit] = useState(0.01);
  const [validationRun, setValidationRun] = useState<MeterValidationStatus | null>(null);
  const [validationBusy, setValidationBusy] = useState(false);
  const [kitDownloading, setKitDownloading] = useState(false);
  const [activationStepBusy, setActivationStepBusy] = useState('');
  const [activationEvidence, setActivationEvidence] = useState<Record<string, string>>({});

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

  const loadCountryReadiness = () => {
    setCountryReadinessLoading(true);
    getCountryProvisioningReadiness()
      .then(setCountryReadiness)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setCountryReadinessLoading(false));
  };

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
    if (mode === 'walkthrough' || mode === 'readiness') loadCountryReadiness();
    if (mode === 'registry') loadRegistry();
    if (mode === 'meters') loadMeters();
    if (mode === 'config') loadConfigRegistry();
  }, [mode]);

  useEffect(() => {
    getProvisioningSiteCodes()
      .then((sites) => {
        setGuideSites(sites);
        if (sites.length === 1) setGuideSite((current) => current || sites[0].code);
      })
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

  useEffect(() => {
    const sessionId = validationRun?.session.id;
    if (!sessionId || validationRun?.session.status === 'passed') return;
    let cancelled = false;
    const poll = () => {
      getMeterValidation(sessionId)
        .then((result) => { if (!cancelled) setValidationRun(result); })
        .catch(() => {});
    };
    const timer = window.setInterval(poll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [validationRun?.session.id, validationRun?.session.status]);

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

  const handleStartCanary = async () => {
    setError('');
    setCanaryStarting(true);
    try {
      const result = await startFactoryOtaCanary({
        site_code: guideSite,
        thing_name: canaryTarget,
        confirmation: canaryConfirmation,
        note: canaryNote || undefined,
      });
      setTrackedOtaId(result.ota_update_id);
      setGuideChecks((prev) => ({ ...prev, otaQueued: true }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCanaryStarting(false);
    }
  };

  const handleApproveRelease = async () => {
    if (!otaReadiness?.release.target_firmware_version) return;
    setError('');
    setApprovalSuccess('');
    setApprovalBusy(true);
    try {
      const result = await approveFactoryOtaRelease({
        site_code: guideSite,
        canary_ota_update_id: trackedOtaId,
        validation_session_id: approvalWaive ? undefined : approvalValidationId || undefined,
        waive_physical_validation: approvalWaive,
        waiver_reason: approvalWaive ? approvalWaiverReason : undefined,
        confirmation: approvalConfirmation,
      });
      setApprovalSuccess(result.note);
      setOtaReadiness(await getFactoryOtaReadiness(guideSite));
      loadCountryReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setApprovalBusy(false);
    }
  };

  const runValidationAction = async (action: () => Promise<MeterValidationStatus>) => {
    setError('');
    setValidationBusy(true);
    try {
      setValidationRun(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setValidationBusy(false);
    }
  };

  const handleActivationStep = async (stepKey: string, completed: boolean) => {
    if (!guideSite) return;
    setError('');
    setActivationStepBusy(stepKey);
    try {
      await updateProvisioningActivationStep({
        site_code: guideSite,
        step_key: stepKey,
        completed,
        evidence_note: activationEvidence[stepKey]
          || countryReadiness?.site_progress?.[guideSite]?.operator_steps?.[stepKey]?.evidence_note
          || undefined,
      });
      loadCountryReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivationStepBusy('');
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
  const canApproveRelease = ['superadmin', 'engineering'].includes(String(user?.role || ''));
  const canConfigureSiteSystems = ['superadmin', 'engineering'].includes(String(user?.role || ''));
  const selectedSiteProgress = guideSite ? countryReadiness?.site_progress?.[guideSite] : undefined;
  const foundationKeys = new Set(['sites', 'tariff', 'metering', 'payment_ingest', 'meter_credit']);
  const foundationsReady = Boolean(countryReadiness?.gates
    .filter((gate) => foundationKeys.has(gate.key))
    .every((gate) => gate.ready));
  const operatorStep = (key: string) => selectedSiteProgress?.operator_steps?.[key];
  const physicalValidationPassed = (selectedSiteProgress?.passed_validations || 0) > 0;
  const walkthroughSteps: Array<{
    key: string;
    title: string;
    owner: string;
    done: boolean;
    description: string;
    actionLabel: string;
    mode?: Mode;
    route?: string;
    secondaryRoute?: string;
    secondaryLabel?: string;
    manualKey?: string;
    evidenceRequired?: boolean;
    evidenceHint?: string;
    note?: string;
  }> = [
    {
      key: 'country-foundations',
      title: 'Activate the country foundations',
      owner: 'Country lead, Finance, O&M, Engineering',
      done: foundationsReady,
      description: 'Approve the site roster and tariff, then configure metering, payment ingestion, and meter credit. CC keeps provisioning fail-closed until these gates are ready.',
      actionLabel: 'Review country gates',
      mode: 'readiness' as Mode,
    },
    {
      key: 'site-release',
      title: 'Select the deployment site and OTA candidate',
      owner: 'Firmware / Engineering',
      done: Boolean(selectedSiteProgress?.ota_candidate_ready),
      description: 'Choose the real destination site. CC must find an immutable signed full-firmware candidate for that exact site.',
      actionLabel: 'Review site release',
      mode: 'readiness' as Mode,
    },
    {
      key: 'deployment_wifi_ready',
      title: 'Prepare the site Starlink credentials',
      owner: 'Country O&M',
      done: Boolean(operatorStep('deployment_wifi_ready')?.completed),
      description: 'Have the exact site SSID/password available. Optionally create a controlled 2.4 GHz HQ mirror with identical credentials and test its internet access. Never paste the password into CC notes.',
      actionLabel: 'Open network preparation',
      mode: 'guide' as Mode,
      manualKey: 'deployment_wifi_ready',
      evidenceRequired: false,
      evidenceHint: 'Optional reference only—never enter the Wi-Fi password',
    },
    {
      key: 'first-gateway',
      title: 'Allocate one factory gateway as the canary',
      owner: 'Country O&M',
      done: (selectedSiteProgress?.test_gateways || 0) > 0,
      description: 'Download the station, select the country and site, scan the provisioning LAN, match the printed unit, and allocate exactly one factory v1.1.56 gateway.',
      actionLabel: 'Start gateway guide',
      mode: 'guide' as Mode,
    },
    {
      key: 'ota-canary',
      title: 'Complete and verify the v1.1.57 OTA canary',
      owner: 'Country O&M + Firmware',
      done: (selectedSiteProgress?.ota_succeeded || 0) > 0,
      description: 'Keep the gateway online while CC displays queued, in-progress, succeeded, or failed. Confirm the installed firmware telemetry reports the target version after reboot.',
      actionLabel: 'Open OTA monitor',
      mode: 'canary' as Mode,
    },
    {
      key: 'meter_string_ready',
      title: 'Address meters and connect the protected test load',
      owner: 'Country O&M',
      done: Boolean(operatorStep('meter_string_ready')?.completed),
      description: 'Download the addressing kit, address and label meters one at a time, verify the sorted RS485 string, power down, then connect the string and a protected dummy load.',
      actionLabel: 'Open addressing and validation',
      mode: 'batch-test' as Mode,
      manualKey: 'meter_string_ready',
      evidenceRequired: true,
      evidenceHint: 'Meter serials, batch label, or bench/photo reference',
    },
    {
      key: 'physical-validation',
      title: 'Prove consumption, zero-balance shutoff, and payment restart',
      owner: 'Country O&M',
      done: physicalValidationPassed || Boolean(selectedSiteProgress?.ota_batch_approved),
      description: 'Run the isolated dummy-customer test: observe positive load, consume the synthetic balance, verify relay open, apply synthetic payment, verify relay close, and confirm the load restarts.',
      actionLabel: 'Run batch validation',
      mode: 'batch-test' as Mode,
      note: !physicalValidationPassed && selectedSiteProgress?.ota_batch_approved
        ? 'Physical validation was waived during release approval; the waiver remains in the audit trail.'
        : undefined,
    },
    {
      key: 'release-approval',
      title: 'Approve the immutable release for controlled batches',
      owner: 'Engineering / Superadmin',
      done: Boolean(selectedSiteProgress?.ota_batch_approved),
      description: 'Review the successful canary and validation session. Approval is bound to the exact artifact version and target firmware.',
      actionLabel: 'Review and approve release',
      mode: 'canary' as Mode,
    },
    {
      key: 'controlled-batch',
      title: 'Provision the controlled gateway batch',
      owner: 'Country O&M',
      done: (selectedSiteProgress?.production_gateways || 0) > 0,
      description: 'Return to the station, provision a controlled batch, and do not move forward until every gateway reports a successful OTA and the expected site/SSID.',
      actionLabel: 'Open batch provisioning guide',
      mode: 'guide' as Mode,
    },
    {
      key: 'test_customer_assigned',
      title: 'Onboard the test customer and assign the meter',
      owner: 'Country O&M',
      done: Boolean(operatorStep('test_customer_assigned')?.completed),
      description: 'Create or identify the approved test customer, reconcile the acquired meter serial, assign the meter/gateway to that account, and record the account reference below.',
      actionLabel: 'Assign meter',
      route: '/assign-meter',
      secondaryRoute: '/customers/new',
      secondaryLabel: 'Create customer',
      manualKey: 'test_customer_assigned',
      evidenceRequired: true,
      evidenceHint: 'Test account number and assigned meter serial',
    },
    {
      key: 'site_commissioning_verified',
      title: 'Verify actual-site Starlink and complete commissioning',
      owner: 'Country O&M',
      done: Boolean(operatorStep('site_commissioning_verified')?.completed),
      description: 'At the real site, confirm cloud reconnect, fresh consumption, the correct customer mapping, a test payment, relay behavior, and final commissioning records.',
      actionLabel: 'Open commissioning',
      route: '/commission',
      secondaryRoute: '/record-payment',
      secondaryLabel: 'Record test payment',
      manualKey: 'site_commissioning_verified',
      evidenceRequired: true,
      evidenceHint: 'Commissioning/customer reference and actual-site test result',
    },
  ];
  const walkthroughDone = walkthroughSteps.filter((step) => step.done).length;
  const walkthroughPercent = Math.round((walkthroughDone / walkthroughSteps.length) * 100);

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
          ['walkthrough', 'Operator walkthrough'],
          ['readiness', 'Country readiness'],
          ['guide', 'Guide & download'],
          ['canary', 'OTA canary'],
          ['batch-test', 'Batch validation'],
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

      {mode === 'walkthrough' ? (
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-200 bg-blue-50 p-5">
            <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
              <div>
                <div className="text-xs uppercase tracking-wide font-semibold text-blue-700">
                  Guided country and site activation
                </div>
                <h2 className="text-xl font-semibold text-gray-900 mt-1">
                  {countryReadiness?.country_name || 'Loading country…'} operator walkthrough
                </h2>
                <p className="text-sm text-gray-700 mt-1 max-w-2xl">
                  Work from top to bottom. CC completes cloud-observable steps automatically and records
                  your confirmation only where a physical bench or site check cannot be observed remotely.
                </p>
              </div>
              <div className="min-w-48">
                <div className="flex justify-between text-xs font-semibold text-gray-700 mb-1">
                  <span>{walkthroughDone} of {walkthroughSteps.length} complete</span>
                  <span>{walkthroughPercent}%</span>
                </div>
                <div className="h-3 bg-white rounded-full overflow-hidden border border-blue-100">
                  <div className="h-full bg-blue-600 transition-all" style={{ width: `${walkthroughPercent}%` }} />
                </div>
              </div>
            </div>
            <div className="grid md:grid-cols-[1fr_auto] gap-3 items-end mt-5">
              <div>
                <label className={labelCls}>Deployment site</label>
                <select
                  className={inputCls}
                  value={guideSite}
                  onChange={(e) => {
                    setGuideSite(e.target.value);
                    setActivationEvidence({});
                    setCanaryTarget('');
                    setValidationTarget('');
                  }}
                >
                  <option value="">Select the site this equipment will serve…</option>
                  {guideSites.map((site) => (
                    <option key={site.code} value={site.code}>{site.code} — {site.name}</option>
                  ))}
                </select>
              </div>
              <button
                onClick={loadCountryReadiness}
                disabled={countryReadinessLoading}
                className="px-4 py-2 rounded-lg border bg-white text-sm font-medium disabled:opacity-50"
              >
                {countryReadinessLoading ? 'Refreshing…' : 'Refresh evidence'}
              </button>
            </div>
          </div>

          {!countryReadinessLoading && (
            <SiteAdditionGuide
              countryCode={countryReadiness?.country_code}
              countryName={countryReadiness?.country_name}
              open={!guideSites.length}
              showAdminLinks={canConfigureSiteSystems}
            />
          )}

          <div className="space-y-3">
            {walkthroughSteps.map((step, index) => {
              const manual = step.manualKey ? operatorStep(step.manualKey) : undefined;
              const evidence = step.manualKey
                ? activationEvidence[step.manualKey] ?? manual?.evidence_note ?? ''
                : '';
              const blockedBySite = index > 0 && !guideSite;
              return (
                <div
                  key={step.key}
                  className={`rounded-xl border p-4 ${
                    step.done
                      ? 'border-green-200 bg-green-50'
                      : blockedBySite
                        ? 'border-gray-200 bg-gray-50'
                        : 'border-amber-200 bg-white'
                  }`}
                >
                  <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
                    <div className="flex gap-3 min-w-0">
                      <span className={`shrink-0 inline-flex w-8 h-8 items-center justify-center rounded-full text-sm font-bold ${
                        step.done
                          ? 'bg-green-600 text-white'
                          : blockedBySite
                            ? 'bg-gray-200 text-gray-500'
                            : 'bg-amber-100 text-amber-800'
                      }`}>
                        {step.done ? '✓' : index + 1}
                      </span>
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-semibold text-gray-900">{step.title}</h3>
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                            step.done ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'
                          }`}>
                            {step.done ? 'Complete' : blockedBySite ? 'Select site first' : 'Action required'}
                          </span>
                        </div>
                        <div className="text-xs text-gray-500 mt-1">Owner: {step.owner}</div>
                        <p className="text-sm text-gray-700 mt-2">{step.description}</p>
                        {step.note && (
                          <div className="text-xs text-amber-800 mt-2">{step.note}</div>
                        )}
                        {manual?.completed && (
                          <div className="text-xs text-green-800 mt-2">
                            Confirmed by {manual.completed_by || 'operator'}
                            {manual.completed_at ? ` · ${manual.completed_at}` : ''}
                            {manual.evidence_note ? ` · ${manual.evidence_note}` : ''}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0 flex flex-wrap gap-2">
                      {step.mode && (
                        <button
                          onClick={() => setMode(step.mode!)}
                          disabled={blockedBySite && step.key !== 'country-foundations'}
                          className="px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-semibold disabled:opacity-40"
                        >
                          {step.actionLabel}
                        </button>
                      )}
                      {step.route && (
                        <Link
                          to={step.route}
                          className={`px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-semibold ${
                            blockedBySite ? 'pointer-events-none opacity-40' : ''
                          }`}
                        >
                          {step.actionLabel}
                        </Link>
                      )}
                      {step.secondaryRoute && (
                        <Link
                          to={step.secondaryRoute}
                          className={`px-3 py-2 rounded-lg border bg-white text-xs font-semibold ${
                            blockedBySite ? 'pointer-events-none opacity-40' : ''
                          }`}
                        >
                          {step.secondaryLabel}
                        </Link>
                      )}
                    </div>
                  </div>

                  {step.manualKey && !blockedBySite && (
                    <div className="mt-4 ml-0 sm:ml-11 p-3 rounded-lg border bg-white">
                      <label className={labelCls}>
                        {step.evidenceRequired ? 'Evidence/reference required' : 'Operator reference (optional)'}
                      </label>
                      <div className="flex flex-col sm:flex-row gap-2">
                        <input
                          className={inputCls}
                          value={evidence}
                          placeholder={step.evidenceHint}
                          onChange={(e) => setActivationEvidence((current) => ({
                            ...current,
                            [step.manualKey!]: e.target.value,
                          }))}
                        />
                        <button
                          onClick={() => handleActivationStep(step.manualKey!, !step.done)}
                          disabled={activationStepBusy === step.manualKey
                            || (!step.done && Boolean(step.evidenceRequired) && evidence.trim().length < 5)}
                          className={`shrink-0 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 ${
                            step.done
                              ? 'border border-gray-300 bg-white text-gray-700'
                              : 'bg-green-600 text-white'
                          }`}
                        >
                          {activationStepBusy === step.manualKey
                            ? 'Saving…'
                            : step.done
                              ? 'Reopen step'
                              : 'Confirm complete'}
                        </button>
                      </div>
                      {step.manualKey === 'deployment_wifi_ready' && (
                        <p className="text-xs text-red-700 mt-2">
                          Do not enter the Starlink password here. It is entered only in the local provisioning station.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {walkthroughDone === walkthroughSteps.length && (
            <div className="p-5 rounded-xl border border-green-300 bg-green-50 text-green-950">
              <div className="font-semibold text-lg">Site activation walkthrough complete</div>
              <p className="text-sm mt-1">
                CC has the cloud evidence and operator confirmations for this site. Retain the validation,
                release, customer, and commissioning references with the deployment record.
              </p>
            </div>
          )}
        </div>
      ) : mode === 'readiness' ? (
        <div className="space-y-5">
          {countryReadinessLoading && !countryReadiness ? (
            <div className="p-5 rounded-xl border bg-white text-sm text-gray-500">
              Checking country activation gates…
            </div>
          ) : countryReadiness && (
            <>
              <div className={`p-5 rounded-xl border ${
                countryReadiness.end_to_end_ready
                  ? 'border-green-300 bg-green-50'
                  : countryReadiness.field_batch_ready
                    ? 'border-amber-300 bg-amber-50'
                    : 'border-red-300 bg-red-50'
              }`}>
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-wide font-semibold text-gray-600">
                      {countryReadiness.country_code} · {countryReadiness.currency}
                    </div>
                    <h2 className="text-lg font-semibold text-gray-900 mt-1">
                      {countryReadiness.country_name} activation
                    </h2>
                    <p className="text-sm text-gray-700 mt-1">
                      {countryReadiness.end_to_end_ready
                        ? 'Provisioning, commissioning, payments, and meter control gates are ready.'
                        : countryReadiness.field_batch_ready
                          ? 'Gateway batches are enabled, but commissioning/payment gates still need action.'
                          : 'Do not release a field batch until every provisioning gate below is green.'}
                    </p>
                  </div>
                  <button onClick={loadCountryReadiness} disabled={countryReadinessLoading}
                    className="px-3 py-2 rounded-lg border bg-white text-sm disabled:opacity-50">
                    {countryReadinessLoading ? 'Refreshing…' : 'Refresh checks'}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {([
                  ['Provisioned', countryReadiness.stats.provisioned_gateways],
                  ['OTA succeeded', countryReadiness.stats.ota_succeeded],
                  ['Test gateways', countryReadiness.stats.test_gateways],
                  ['Validations passed', countryReadiness.stats.passed_validations],
                  ['Commissioned', countryReadiness.stats.commissioned],
                ] as [string, number][]).map(([label, value]) => (
                  <div key={label} className="p-3 rounded-xl border bg-white">
                    <div className="text-2xl font-semibold text-gray-900">{value}</div>
                    <div className="text-xs text-gray-500 mt-1">{label}</div>
                  </div>
                ))}
              </div>

              <div className="space-y-3">
                {countryReadiness.gates.map((gate) => (
                  <div key={gate.key} className={`rounded-xl border p-4 ${
                    gate.ready ? 'border-green-200 bg-green-50' : 'border-red-200 bg-white'
                  }`}>
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
                      <div className="flex gap-3">
                        <span className={`mt-0.5 inline-flex w-6 h-6 items-center justify-center rounded-full text-xs font-bold ${
                          gate.ready ? 'bg-green-600 text-white' : 'bg-red-100 text-red-700'
                        }`}>{gate.ready ? '✓' : '!'}</span>
                        <div>
                          <div className="font-semibold text-gray-900">{gate.label}</div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            Owner: {gate.owner} · Scope: {gate.scope}
                          </div>
                          <div className="text-sm text-gray-700 mt-2">{gate.action}</div>
                        </div>
                      </div>
                      {gate.route && (
                        <Link to={gate.route}
                          className="shrink-0 px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-semibold">
                          {gate.key === 'sites' ? 'How to add a site' : 'Open required page'}
                        </Link>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <SiteAdditionGuide
                countryCode={countryReadiness.country_code}
                countryName={countryReadiness.country_name}
                open={!Object.keys(countryReadiness.sites).length}
                showAdminLinks={canConfigureSiteSystems}
              />
            </>
          )}
        </div>
      ) : mode === 'batch-test' ? (
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-blue-600">Optional · recommended for every batch</div>
              <h2 className="text-lg font-semibold text-gray-900 mt-1">Meter, load, balance, and relay validation</h2>
              <p className="text-sm text-gray-600 mt-1">
                Uses an isolated synthetic ledger and dummy customer label. It never creates customer revenue or financial transactions.
              </p>
            </div>
            <button
              onClick={async () => {
                setKitDownloading(true);
                try { await downloadMeterValidationKit(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                finally { setKitDownloading(false); }
              }}
              disabled={kitDownloading}
              className="w-full px-4 py-3 rounded-lg border border-blue-300 bg-blue-50 text-blue-800 text-sm font-semibold disabled:opacity-50"
            >
              {kitDownloading ? 'Preparing kit…' : 'Download meter addressing SOP + code'}
            </button>
            <div>
              <label className={labelCls}>Site release</label>
              <select className={inputCls} value={guideSite} onChange={(e) => {
                setGuideSite(e.target.value);
                setValidationTarget('');
              }}>
                <option value="">Select site…</option>
                {guideSites.map((site) => <option key={site.code} value={site.code}>{site.code} — {site.name}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Authorized physical test gateway</label>
              <select className={inputCls} value={validationTarget} onChange={(e) => setValidationTarget(e.target.value)}>
                <option value="">Select one gateway…</option>
                {(otaReadiness?.canary_things || []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Batch or shipment reference</label>
              <input className={inputCls} value={batchReference} onChange={(e) => setBatchReference(e.target.value)} placeholder="Example: Benin batch 2026-07" />
            </div>
            <div>
              <label className={labelCls}>Small synthetic starting credit (kWh)</label>
              <input type="number" min="0.001" max="1" step="0.001" className={inputCls} value={startingCredit}
                onChange={(e) => setStartingCredit(Number(e.target.value))} />
              <p className="text-xs text-gray-500 mt-1">Choose an amount the protected dummy load can consume during the bench test.</p>
            </div>
            <button
              disabled={validationBusy || !validationTarget || !batchReference || Boolean(validationRun)}
              onClick={() => runValidationAction(() => startMeterValidation({
                thing_name: validationTarget,
                batch_reference: batchReference,
                starting_credit_kwh: startingCredit,
              }))}
              className="w-full px-4 py-3 bg-blue-600 text-white rounded-lg text-sm font-semibold disabled:opacity-40"
            >
              Start isolated batch validation
            </button>
            <ol className="list-decimal pl-5 space-y-2 text-sm text-gray-700">
              <li>Address and label meters one at a time; scan the combined RS485 string.</li>
              <li>Connect the string and protected dummy load; reconcile the expected serial.</li>
              <li>Confirm signed OTA, firmware version, telemetry, and the synthetic test identity.</li>
              <li>Run the load until the synthetic balance reaches zero and relay read-back is 0.</li>
              <li>Apply the synthetic payment and confirm relay read-back is 1 and the load restarts.</li>
            </ol>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="text-lg font-semibold text-gray-900">Validation evidence</h2>
            {!validationRun ? (
              <p className="text-sm text-gray-500">Start a session after the addressed meter string and dummy load are safely connected.</p>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div className="p-3 rounded bg-gray-50 col-span-2"><b>Evidence session ID</b><br /><span className="font-mono text-xs">{validationRun.session.id}</span></div>
                  <div className="p-3 rounded bg-gray-50"><b>Gateway</b><br /><span className="font-mono">{validationRun.session.thing_name}</span></div>
                  <div className="p-3 rounded bg-gray-50"><b>Meter</b><br /><span className="font-mono">{validationRun.session.meter_id}</span></div>
                  <div className="p-3 rounded bg-gray-50"><b>Load delta</b><br />{validationRun.session.load_delta_kwh.toFixed(4)} kWh</div>
                  <div className="p-3 rounded bg-gray-50"><b>Synthetic balance</b><br />{validationRun.session.simulated_balance_kwh.toFixed(4)} kWh</div>
                  <div className="p-3 rounded bg-gray-50"><b>Relay telemetry</b><br />{validationRun.telemetry.relay ?? '—'}</div>
                  <div className="p-3 rounded bg-gray-50"><b>Status</b><br />{validationRun.session.status}</div>
                </div>
                <button
                  disabled={validationBusy || Boolean(validationRun.session.reconnect_cmd_id)}
                  onClick={() => runValidationAction(() => observeMeterValidationLoad(validationRun.session.id))}
                  className="w-full px-4 py-3 rounded-lg bg-indigo-600 text-white text-sm font-semibold disabled:opacity-40"
                >
                  Observe load and update synthetic balance
                </button>
                <div className="p-3 rounded-lg border text-sm">
                  <b>Zero-balance disconnect:</b>{' '}
                  {validationRun.disconnect_command
                    ? `${validationRun.disconnect_command.status} · relay ${validationRun.disconnect_command.relay_after ?? '?'} · ${validationRun.disconnect_command.cmd_id}`
                    : 'waiting for positive load and zero balance'}
                </div>
                <button
                  disabled={validationBusy
                    || validationRun.disconnect_command?.status !== 'completed'
                    || validationRun.disconnect_command?.relay_after !== '0'
                    || Boolean(validationRun.reconnect_command)}
                  onClick={() => runValidationAction(() => applyMeterValidationPayment(validationRun.session.id, 0.05))}
                  className="w-full px-4 py-3 rounded-lg bg-green-600 text-white text-sm font-semibold disabled:opacity-40"
                >
                  Apply synthetic payment and reconnect
                </button>
                <div className="p-3 rounded-lg border text-sm">
                  <b>Payment reconnect:</b>{' '}
                  {validationRun.reconnect_command
                    ? `${validationRun.reconnect_command.status} · relay ${validationRun.reconnect_command.relay_after ?? '?'} · ${validationRun.reconnect_command.cmd_id}`
                    : 'not started'}
                </div>
                <button
                  disabled={validationBusy
                    || validationRun.reconnect_command?.status !== 'completed'
                    || validationRun.reconnect_command?.relay_after !== '1'
                    || validationRun.session.load_delta_kwh <= 0}
                  onClick={() => runValidationAction(async () => {
                    const result = await completeMeterValidation(validationRun.session.id);
                    if (result.session.status === 'passed') setApprovalValidationId(result.session.id);
                    return result;
                  })}
                  className="w-full px-4 py-3 rounded-lg bg-gray-900 text-white text-sm font-semibold disabled:opacity-40"
                >
                  Complete and record passing validation
                </button>
                {validationRun.session.status === 'passed' && (
                  <div className="p-4 rounded-lg border border-green-300 bg-green-50 text-green-900 font-semibold">
                    Batch validation passed and evidence was recorded. Use session
                    <span className="font-mono"> {validationRun.session.id}</span> for OTA release approval.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      ) : mode === 'canary' ? (
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-amber-600">Single test gateway only</div>
              <h2 className="text-lg font-semibold text-gray-900 mt-1">Start a signed OTA canary</h2>
              <p className="text-sm text-gray-600 mt-1">
                This does not approve a batch or field rollout. CC permits only a server-authorized test gateway and requires exact typed confirmation.
              </p>
            </div>
            <div>
              <label className={labelCls}>Destination site release</label>
              <select className={inputCls} value={guideSite} onChange={(e) => {
                setGuideSite(e.target.value);
                setCanaryTarget('');
                setCanaryConfirmation('');
              }}>
                <option value="">Select site…</option>
                {guideSites.map((site) => <option key={site.code} value={site.code}>{site.code} — {site.name}</option>)}
              </select>
            </div>
            {!guideSite ? null : otaReadiness?.canary_ready ? (
              <div className="p-3 rounded-lg border border-green-200 bg-green-50 text-sm text-green-900">
                Candidate <b>{otaReadiness.release.target_firmware_version}</b> passed artifact, anti-rollback, and signer checks.
                {otaReadiness.release.canary_only ? ' Batch promotion remains locked.' : ''}
              </div>
            ) : (
              <div className="p-3 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-900">
                {otaReadinessError || 'No authorized canary target and candidate release are configured for this site.'}
              </div>
            )}
            <div>
              <label className={labelCls}>Authorized test gateway</label>
              <select className={inputCls} value={canaryTarget} onChange={(e) => {
                setCanaryTarget(e.target.value);
                setCanaryConfirmation('');
              }} disabled={!otaReadiness?.canary_ready}>
                <option value="">Select one gateway…</option>
                {(otaReadiness?.canary_things || []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Type CANARY {canaryTarget || '&lt;gateway&gt;'}</label>
              <input className={inputCls} value={canaryConfirmation} onChange={(e) => setCanaryConfirmation(e.target.value)} />
            </div>
            <div>
              <label className={labelCls}>Operator note (optional)</label>
              <input className={inputCls} value={canaryNote} onChange={(e) => setCanaryNote(e.target.value)} placeholder="Batch, shipment, or test bench reference" />
            </div>
            <button
              onClick={handleStartCanary}
              disabled={canaryStarting || !guideSite || !canaryTarget || canaryConfirmation !== `CANARY ${canaryTarget}` || !otaReadiness?.canary_ready}
              className="w-full px-4 py-3 bg-amber-600 text-white rounded-lg text-sm font-semibold disabled:opacity-40"
            >
              {canaryStarting ? 'Creating OTA canary…' : 'Start one-gateway OTA canary'}
            </button>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Live OTA status</h2>
              <p className="text-sm text-gray-500 mt-1">{trackedOtaId || 'No canary started in this session.'}</p>
            </div>
            {trackedOtaId && (
              <>
                <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
                  <div className={`h-full ${otaFailed ? 'bg-red-500' : otaPercent === 100 ? 'bg-green-600' : 'bg-blue-600'}`} style={{ width: `${otaPercent}%` }} />
                </div>
                <div className="text-sm font-semibold">{otaPercent}% complete</div>
                {(otaProgress?.executions || []).map((execution) => (
                  <div key={execution.thing_name} className="flex justify-between gap-4 p-3 rounded-lg bg-gray-50 border text-sm">
                    <span className="font-mono">{execution.thing_name}</span>
                    <span className={execution.status === 'SUCCEEDED' ? 'text-green-700 font-semibold' : otaFailed ? 'text-red-700 font-semibold' : 'text-blue-700'}>
                      {execution.status || 'QUEUED'}
                    </span>
                  </div>
                ))}
                {otaProgressError && <div className="text-sm text-red-700">{otaProgressError}</div>}
                {otaPercent === 100 && (
                  <div className="p-3 rounded-lg border border-green-200 bg-green-50 text-sm text-green-900">
                    OTA succeeded. Continue to the recommended batch validation, then record release approval below.
                  </div>
                )}
              </>
            )}
            {otaReadiness?.release.approval && (
              <div className="p-4 rounded-lg border border-green-300 bg-green-50 text-sm text-green-900">
                <div className="font-semibold">✓ Release approved for controlled batches</div>
                <div className="mt-1">
                  Approved by {otaReadiness.release.approval.approved_by || 'authorized reviewer'}
                  {otaReadiness.release.approval.approved_at ? ` at ${otaReadiness.release.approval.approved_at}` : ''}.
                </div>
              </div>
            )}
            {otaPercent === 100 && trackedOtaId && !otaReadiness?.release.approval && (
              <div className="border-t pt-4 space-y-3">
                <div>
                  <h3 className="font-semibold text-gray-900">Approve immutable release for batches</h3>
                  <p className="text-xs text-gray-500 mt-1">
                    Engineering/superadmin only. Physical validation is recommended; skipping it requires an audited reason.
                  </p>
                </div>
                {!canApproveRelease ? (
                  <div className="p-3 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-900">
                    Canary evidence is ready. Engineering or a superadmin must review and approve the release.
                  </div>
                ) : (
                  <>
                    <label className="block">
                      <span className={labelCls}>Passed Batch validation session ID</span>
                      <input className={inputCls} value={approvalValidationId}
                        disabled={approvalWaive}
                        onChange={(e) => setApprovalValidationId(e.target.value.trim())}
                        placeholder="Paste the passed session ID" />
                    </label>
                    <label className="flex gap-3 items-start p-3 rounded-lg border cursor-pointer">
                      <input type="checkbox" className="mt-0.5" checked={approvalWaive}
                        onChange={(e) => setApprovalWaive(e.target.checked)} />
                      <span className="text-sm text-gray-700">
                        Waive optional physical validation for this release approval.
                      </span>
                    </label>
                    {approvalWaive && (
                      <label className="block">
                        <span className={labelCls}>Waiver reason (minimum 20 characters)</span>
                        <textarea className={inputCls} value={approvalWaiverReason}
                          onChange={(e) => setApprovalWaiverReason(e.target.value)}
                          placeholder="Explain why OTA-only evidence is sufficient for this approval." />
                      </label>
                    )}
                    <label className="block">
                      <span className={labelCls}>
                        Type APPROVE {guideSite || '&lt;SITE&gt;'} {otaReadiness?.release.target_firmware_version || '&lt;VERSION&gt;'}
                      </span>
                      <input className={inputCls} value={approvalConfirmation}
                        onChange={(e) => setApprovalConfirmation(e.target.value)} />
                    </label>
                    <button
                      onClick={handleApproveRelease}
                      disabled={approvalBusy
                        || (!approvalWaive && !approvalValidationId)
                        || (approvalWaive && approvalWaiverReason.trim().length < 20)
                        || approvalConfirmation !== `APPROVE ${guideSite} ${otaReadiness?.release.target_firmware_version}`}
                      className="w-full px-4 py-3 rounded-lg bg-green-700 text-white text-sm font-semibold disabled:opacity-40"
                    >
                      {approvalBusy ? 'Recording approval…' : 'Approve this immutable release'}
                    </button>
                  </>
                )}
                {approvalSuccess && (
                  <div className="p-3 rounded-lg border border-green-300 bg-green-50 text-sm text-green-900">
                    {approvalSuccess}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      ) : mode === 'guide' ? (
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
                ) : otaReadiness?.candidate_ready && otaReadiness?.release.canary_only ? (
                  <div className="p-4 rounded-lg border border-amber-300 bg-amber-50 text-sm text-amber-950">
                    <div className="font-semibold">Candidate ready — first canary required</div>
                    <div className="mt-1">
                      Signed firmware <b>{otaReadiness.release.target_firmware_version}</b> passed artifact,
                      signer, and anti-rollback checks. Continue with exactly one gateway; the station will
                      record it as the authorized test unit. Batch rollout remains locked.
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
                    disabled={!otaReadiness?.ready && !otaReadiness?.candidate_ready}
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
                    ['stationSignedIn', 'The station is signed in and its OTA card says ready or candidate ready.'],
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
                      {otaReadiness?.release.canary_only
                        ? 'Run the recommended Batch validation, then have Engineering/superadmin approve this immutable release in the OTA canary tab before any larger batch.'
                        : 'For a larger batch, return to Step 4 and increase gradually only after the canary remains healthy.'}
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
                    <td className="px-4 py-2 font-mono text-gray-900">
                      {r.thing_name}
                      {r.is_test ? <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px]">TEST</span> : null}
                    </td>
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
