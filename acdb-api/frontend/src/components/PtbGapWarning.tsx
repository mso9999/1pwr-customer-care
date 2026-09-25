import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { assignPtb, getInstallationStatus, getPtbGaps, type PtbGap } from '../lib/api';

/**
 * 1Meters serving a customer with recent transactions or consumption that are
 * in no uGridPLAN PTB. A meter in service is almost certainly inside a PTB,
 * so offer to record it. Hidden when there are no gaps or no access.
 */
export default function PtbGapWarning({ site, account }: { site: string; account?: string }) {
  const { i18n } = useTranslation();
  const L = (en: string, fr: string) => (i18n.language?.startsWith('fr') ? fr : en);
  const [gaps, setGaps] = useState<PtbGap[]>([]);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    if (!site) return;
    getPtbGaps(site, account)
      .then((r) => setGaps(r.meters))
      .catch(() => setGaps([]));
  }, [site, account]);

  useEffect(load, [load]);

  const createPtb = async (gap: PtbGap) => {
    if (!gap.pole_id && !gap.survey_id) {
      setMessage(L(
        `No pole is known for ${gap.meter_id}. Open the meter on the Meters page and pick its pole on the map.`,
        `Aucun poteau connu pour ${gap.meter_id}. Ouvrez le compteur dans la page Compteurs et choisissez son poteau sur la carte.`,
      ));
      return;
    }
    if (!window.confirm(L(
      `Create the PTB for meter ${gap.meter_id} (${gap.account_number}) on pole ${gap.pole_id || 'from the customer connection'} in uGridPlan?`,
      `Créer le PTB du compteur ${gap.meter_id} (${gap.account_number}) sur le poteau ${gap.pole_id || 'du raccordement client'} dans uGridPlan ?`,
    ))) return;
    setBusy(gap.meter_id);
    setMessage('');
    try {
      const res = await assignPtb({
        site,
        account_number: gap.account_number,
        meter_serial: gap.meter_id,
        pole_id: gap.pole_id || undefined,
        survey_id: gap.survey_id || undefined,
        create_ptb: true,
      });
      setMessage(L(
        `PTB ${res.ptb_id} ${res.ptb_created ? 'created' : 'updated'} on pole ${res.pole_id}.`,
        `PTB ${res.ptb_id} ${res.ptb_created ? 'créé' : 'mis à jour'} sur le poteau ${res.pole_id}.`,
      ));
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  if (!gaps.length && !message) return null;

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
      {gaps.length > 0 && (
        <>
          <div className="font-semibold text-amber-950">
            {L(`${site}: meter(s) in service with no PTB in uGridPlan`, `${site} : compteur(s) en service sans PTB dans uGridPlan`)}
          </div>
          <p className="text-xs text-amber-900 mt-1">
            {L(
              'These 1Meters serve customers with recent transactions or consumption, so they are almost certainly installed inside a pole box. Create the PTB unless this is a bench test.',
              'Ces 1Meter servent des clients avec des transactions ou une consommation récentes : ils sont presque certainement installés dans un boîtier. Créez le PTB sauf s’il s’agit d’un test sur banc.',
            )}
          </p>
          <ul className="mt-2 space-y-1.5 text-sm">
            {gaps.map((g) => (
              <li key={g.meter_id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="font-mono">{g.meter_id}</span> · {g.account_number}
                  {g.pole_id ? ` · ${L('pole', 'poteau')} ${g.pole_id}` : ''}
                </span>
                <button
                  type="button"
                  disabled={busy === g.meter_id}
                  onClick={() => createPtb(g)}
                  className="px-2.5 py-1 rounded-lg bg-white border border-amber-300 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-50"
                >
                  {busy === g.meter_id ? L('Creating…', 'Création…') : L('Create PTB', 'Créer le PTB')}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {message && <div className="text-xs text-gray-800 mt-2">{message}</div>}
    </div>
  );
}

/** One PTB-gap warning per site that has 1Meters bound to customers. */
export function SitePtbGapWarnings() {
  const [sites, setSites] = useState<string[]>([]);
  useEffect(() => {
    getInstallationStatus()
      .then((r) => setSites(r.sites.filter((s) => s.assigned_meters > 0).map((s) => s.site)))
      .catch(() => setSites([]));
  }, []);
  if (!sites.length) return null;
  return (
    <div className="space-y-3">
      {sites.map((site) => <PtbGapWarning key={site} site={site} />)}
    </div>
  );
}
