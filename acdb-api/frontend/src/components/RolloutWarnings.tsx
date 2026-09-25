import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getInstallationStatus, type SiteInstallationStatus } from '../lib/api';

/**
 * 1Meter rollout gaps for the selected country: gateways in the field ahead of
 * the site walkthrough, gateways online without a pole record, and meters that
 * report telemetry but are not assigned (so they are absent from this page).
 * Hidden for users without provisioning access (the endpoint returns 403).
 */
export default function RolloutWarnings() {
  const { i18n } = useTranslation();
  const L = (en: string, fr: string) => (i18n.language?.startsWith('fr') ? fr : en);
  const [sites, setSites] = useState<SiteInstallationStatus[]>([]);

  useEffect(() => {
    getInstallationStatus()
      .then((r) => setSites(r.sites))
      .catch(() => setSites([]));
  }, []);

  const flagged = sites.filter((s) =>
    (s.gateways_in_field > 0 && !s.walkthrough_complete)
    || s.gateways_not_on_pole.length > 0
    || s.unassigned_meters.length > 0,
  );
  if (!flagged.length) return null;

  const linkCls = 'px-2.5 py-1 rounded-lg bg-white border border-amber-300 text-xs font-semibold text-amber-900 hover:bg-amber-100';

  return (
    <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4">
      <div className="font-semibold text-amber-950">
        {L('1Meter installs not yet complete in CC', 'Installations 1Meter pas encore complètes dans CC')}
      </div>
      <p className="text-xs text-amber-900 mt-1">
        {L(
          'A meter only appears in this list and on the map after it is assigned to a customer. These sites have equipment in the field that CC has not fully recorded.',
          'Un compteur n’apparaît dans cette liste et sur la carte qu’une fois attribué à un client. Ces sites ont du matériel sur le terrain que CC n’a pas encore entièrement enregistré.',
        )}
      </p>
      <div className="mt-3 space-y-2">
        {flagged.map((s) => {
          const site = encodeURIComponent(s.site);
          return (
            <div key={s.site} className="rounded-lg border border-amber-200 bg-white/70 p-3">
              <div className="text-sm font-semibold text-gray-900">{s.site} — {s.name}</div>
              <ul className="mt-1 space-y-1.5 text-sm text-gray-800">
                {s.gateways_in_field > 0 && !s.walkthrough_complete && (
                  <li className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {L(
                        `${s.gateways_in_field} gateway(s) have been online, but the site walkthrough is not complete (${!s.release_approved ? 'release not approved' : 'site commissioning not confirmed'}).`,
                        `${s.gateways_in_field} passerelle(s) ont été en ligne, mais le parcours du site n’est pas terminé (${!s.release_approved ? 'version non approuvée' : 'mise en service du site non confirmée'}).`,
                      )}
                    </span>
                    <Link to={`/provisioning?tab=walkthrough&site=${site}`} className={linkCls}>
                      {L('Open walkthrough', 'Ouvrir le parcours')}
                    </Link>
                  </li>
                )}
                {s.gateways_not_on_pole.length > 0 && (
                  <li className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {L('Online but not recorded on a pole:', 'En ligne mais non enregistrées sur un poteau :')}{' '}
                      <span className="font-mono text-xs">{s.gateways_not_on_pole.join(', ')}</span>
                    </span>
                    <Link to={`/provisioning?tab=field-install&site=${site}`} className={linkCls}>
                      {L('Record on pole', 'Enregistrer le poteau')}
                    </Link>
                  </li>
                )}
                {s.unassigned_meters.length > 0 && (
                  <li className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {L(
                        `${s.unassigned_meters.length} meter(s) reporting but not assigned to a customer:`,
                        `${s.unassigned_meters.length} compteur(s) actif(s) non attribué(s) à un client :`,
                      )}{' '}
                      <span className="font-mono text-xs">{s.unassigned_meters.map((m) => m.meter_id).join(', ')}</span>
                    </span>
                    <Link to={`/assign-meter?platform=prototype&site=${site}`} className={linkCls}>
                      {L('Assign meters', 'Attribuer les compteurs')}
                    </Link>
                  </li>
                )}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}
