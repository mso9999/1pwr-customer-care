import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  getFleetLive,
  getProvisionedMeters,
  getProvisioningSiteCodes,
  installGateway,
  listGatewayInstallations,
  type GatewayInstallation,
  type InstallGatewayResult,
  type ProvisionedMeter,
  type ProvisioningSiteCode,
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
export default function FieldInstall({ initialSite = '' }: { initialSite?: string }) {
  const { i18n } = useTranslation();
  const L = (en: string, fr: string) => (i18n.language?.startsWith('fr') ? fr : en);
  const [sites, setSites] = useState<ProvisioningSiteCode[]>([]);
  const [site, setSite] = useState(initialSite);
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
    if (initialSite) setSite(initialSite);
  }, [initialSite]);

  useEffect(() => {
    getProvisioningSiteCodes()
      .then((list) => {
        setSites(list);
        setSite((current) => current || (list.length === 1 ? list[0].code : ''));
      })
      .catch(() => setSites([]));
  }, []);

  useEffect(() => {
    setLoadErr('');
    setGateway('');
    if (!site) {
      setProv([]);
      setInstalls([]);
      setSummary(null);
      return;
    }
    getProvisionedMeters(site)
      .then((r) => setProv(r.meters || []))
      .catch(() => setProv([]));
    refreshInstalls(site);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site]);

  // Keep polling while any install is awaiting first cloud contact.
  useEffect(() => {
    if (!site || !installs.some((i) => i.status === 'awaiting_contact')) return;
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

  // Gateway options: provisioned, non-test units; not-yet-installed first. A
  // gateway that already reports a meter serial or serves accounts still needs
  // its pole record — only an existing install makes it unavailable.
  const gwOptions = useMemo(() => {
    const installedThings = new Set(installs.map((i) => i.gateway_thing));
    const rows = prov
      .filter((m) => (m.thing_name || '').includes('-GW-') && !m.is_test)
      .map((m) => {
        const name = String(m.thing_name);
        const installed = installedThings.has(name);
        const label = installed
          ? `${name} — ${L('installed on', 'installée sur')} ${installs.find((i) => i.gateway_thing === name)?.pole_id || L('a pole', 'un poteau')}`
          : name;
        return { name, taken: installed, label };
      });
    rows.sort((a, b) => Number(a.taken) - Number(b.taken) || a.name.localeCompare(b.name));
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prov, installs, i18n.language]);

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
      return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">{L('verified online', 'vérifiée en ligne')}</span>;
    }
    return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">{L('awaiting contact', 'en attente de contact')}</span>;
  };

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800">{L('Install a gateway on a pole', 'Installer une passerelle sur un poteau')}</h3>
          <p className="text-xs text-gray-500 mt-1">
            {L(
              "Record this the same day the gateway goes up: bind the provisioned unit to the pole's PTB, then power it. CC verifies the gateway is live on the cloud. Next, assign each meter on the pole with Assign Meter.",
              'À enregistrer le jour même de la pose : liez l’unité provisionnée au boîtier (PTB) du poteau, puis mettez-la sous tension. CC vérifie que la passerelle est en ligne. Ensuite, attribuez chaque compteur du poteau avec Attribuer un compteur.',
            )}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Site</label>
            <select value={site} onChange={(e) => setSite(e.target.value)} className="w-full px-3 py-2.5 border rounded-lg text-sm bg-white">
              <option value="">{L('Select site…', 'Choisir le site…')}</option>
              {sites.map((s) => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{L('Gateway unit (provisioned)', 'Passerelle (provisionnée)')}</label>
            <select value={gateway} onChange={(e) => setGateway(e.target.value)} className="w-full px-3 py-2.5 border rounded-lg text-sm bg-white">
              <option value="">{L('Select gateway…', 'Choisir la passerelle…')}</option>
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
              <span className="font-medium text-gray-700">{L("Don't know which unit it is?", 'Vous ne savez pas quelle unité c’est ?')}</span>{' '}
              {L('Power it and watch — the one that just connected is yours.', 'Mettez-la sous tension et observez — celle qui vient de se connecter est la vôtre.')}
            </div>
            <button
              onClick={() => setWatching((w) => !w)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium ${watching ? 'bg-green-600 text-white' : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-100'}`}
            >
              {watching ? L('Watching… (tap to stop)', 'Observation… (toucher pour arrêter)') : L('Watch for a unit coming online', 'Détecter une unité qui se connecte')}
            </button>
          </div>
          {watching && (
            <div className="mt-2.5">
              {recentUnits.length === 0 ? (
                <div className="text-xs text-gray-500 py-1.5 flex items-center gap-2">
                  <span className="animate-spin inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full" />
                  {L('Listening… power the unit now. Nothing new in the last 3 min.', 'À l’écoute… mettez l’unité sous tension. Rien de nouveau depuis 3 min.')}
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
                        {L('Use this unit', 'Utiliser cette unité')}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{L('Pole (physical location)', 'Poteau (emplacement physique)')}</label>
          <div className="flex gap-2">
            <input
              value={poleId}
              onChange={(e) => setPoleId(e.target.value)}
              placeholder={site ? `${L('e.g.', 'ex.')} ${site}_01_DA12` : ''}
              className="flex-1 px-3 py-2.5 border rounded-lg text-sm bg-white font-mono"
            />
            <button
              onClick={() => setPickerOpen(true)}
              className="px-3 py-2.5 bg-gray-100 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-200"
            >
              {L('Pick on map', 'Choisir sur la carte')}
            </button>
          </div>
        </div>

        <button
          onClick={submit}
          disabled={busy || !site || !gateway || !poleId.trim()}
          className="w-full py-3 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? L('Installing…', 'Installation…') : L('Install & verify', 'Installer et vérifier')}
        </button>

        {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm whitespace-pre-wrap">{error}</div>}

        {result && (
          <div className={`p-4 rounded-lg border ${result.verified ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}`}>
            <div className={`text-sm font-semibold ${result.verified ? 'text-green-800' : 'text-amber-800'}`}>
              {result.verified
                ? L('Installed — gateway is live on the cloud', 'Installée — la passerelle est en ligne')
                : L('Installed — awaiting first cloud contact', 'Installée — en attente du premier contact cloud')}
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
                {L('Run guided troubleshooting', 'Lancer le dépannage guidé')}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold text-gray-800">Installations</h3>
          <button onClick={() => site && refreshInstalls(site)} className="px-3 py-1.5 bg-gray-100 border border-gray-300 rounded-lg text-xs font-medium hover:bg-gray-200">
            {L('Refresh', 'Actualiser')}
          </button>
        </div>
        {summary && (
          <div className="flex gap-4 text-xs text-gray-600 mb-3 flex-wrap">
            <span>{summary.total} {L('installed', 'installées')}</span>
            <span className="text-green-700 font-medium">{summary.verified} {L('verified', 'vérifiées')}</span>
            <span className="text-amber-700 font-medium">{summary.awaiting_contact} {L('awaiting contact', 'en attente de contact')}</span>
            <span className="text-gray-400">{summary.connected_now} {L('connected now', 'connectées maintenant')}</span>
          </div>
        )}
        {loadErr && <div className="p-2 text-xs text-red-600">{loadErr}</div>}
        <div className="divide-y divide-gray-100">
          {installs.length === 0 && (
            <div className="py-6 text-center text-sm text-gray-400">
              {site
                ? L(`No gateway installations recorded for ${site} yet.`, `Aucune installation de passerelle enregistrée pour ${site}.`)
                : L('Select a site to see its installations.', 'Choisissez un site pour voir ses installations.')}
            </div>
          )}
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
                    {L('Troubleshoot', 'Dépanner')}
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
