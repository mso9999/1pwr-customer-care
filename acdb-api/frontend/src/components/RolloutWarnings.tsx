import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getInstallationStatus, type RolloutWarning, type SiteInstallationStatus } from '../lib/api';

/**
 * 1Meter rollout gaps for the selected country: gateways in the field ahead of
 * the site walkthrough, gateways online without a pole record, and meters that
 * report telemetry but are not assigned (so they are absent from this page).
 * Collapsed by default; each warning carries a stable id and the date it opened.
 * Hidden for users without provisioning access (the endpoint returns 403).
 */
const MAX_ITEMS = 8;

export default function RolloutWarnings() {
  const { i18n } = useTranslation();
  const fr = i18n.language?.startsWith('fr');
  const L = (en: string, frText: string) => (fr ? frText : en);
  const [sites, setSites] = useState<SiteInstallationStatus[]>([]);

  useEffect(() => {
    getInstallationStatus()
      .then((r) => setSites(r.sites))
      .catch(() => setSites([]));
  }, []);

  const flagged = sites.filter((s) => (s.warnings?.length ?? 0) > 0);
  if (!flagged.length) return null;

  const total = flagged.reduce((n, s) => n + (s.warnings?.length ?? 0), 0);
  const fmtDate = (iso?: string | null) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(fr ? 'fr-FR' : 'en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };
  const linkCls = 'px-2.5 py-1 rounded-lg bg-white border border-amber-300 text-xs font-semibold text-amber-900 hover:bg-amber-100';

  const describe = (w: RolloutWarning) => {
    const n = w.items.length;
    if (w.kind === 'walkthrough') {
      return w.reason === 'release_not_approved'
        ? L(`${n} gateway(s) have been online, but the site walkthrough is not complete (release not approved).`,
          `${n} passerelle(s) ont été en ligne, mais le parcours du site n’est pas terminé (version non approuvée).`)
        : L(`${n} gateway(s) have been online, but the site walkthrough is not complete (site commissioning not confirmed).`,
          `${n} passerelle(s) ont été en ligne, mais le parcours du site n’est pas terminé (mise en service du site non confirmée).`);
    }
    if (w.kind === 'not_on_pole') {
      return L(`${n} gateway(s) online but not recorded on a pole.`, `${n} passerelle(s) en ligne mais non enregistrée(s) sur un poteau.`);
    }
    return L(`${n} meter(s) reporting but not assigned to a customer.`, `${n} compteur(s) actif(s) non attribué(s) à un client.`);
  };

  const action = (w: RolloutWarning, site: string) => {
    if (w.kind === 'walkthrough') {
      return <Link to={`/provisioning?tab=walkthrough&site=${site}`} className={linkCls}>{L('Open walkthrough', 'Ouvrir le parcours')}</Link>;
    }
    if (w.kind === 'not_on_pole') {
      return <Link to={`/provisioning?tab=field-install&site=${site}`} className={linkCls}>{L('Record on pole', 'Enregistrer le poteau')}</Link>;
    }
    return <Link to={`/assign-meter?platform=prototype&site=${site}`} className={linkCls}>{L('Assign meters', 'Attribuer les compteurs')}</Link>;
  };

  return (
    <details className="group mb-4 rounded-xl border border-amber-300 bg-amber-50">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 p-4 [&::-webkit-details-marker]:hidden">
        <span className="text-amber-700 transition-transform group-open:rotate-90">▶</span>
        <span className="font-semibold text-amber-950">
          {L('1Meter installs not yet complete in CC', 'Installations 1Meter pas encore complètes dans CC')}
        </span>
        <span className="text-xs text-amber-900">
          {L(`${total} warning(s) · ${flagged.map((s) => s.site).join(', ')}`,
            `${total} alerte(s) · ${flagged.map((s) => s.site).join(', ')}`)}
        </span>
      </summary>
      <div className="px-4 pb-4">
        <p className="text-xs text-amber-900">
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
                <ul className="mt-1 space-y-2 text-sm text-gray-800">
                  {(s.warnings ?? []).map((w) => (
                    <li key={w.id} className="space-y-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span>
                          <span className="mr-2 rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[11px] text-amber-900">{w.id}</span>
                          {describe(w)}{' '}
                          <span className="text-xs text-gray-500">{L('Since', 'Depuis le')} {fmtDate(w.since)}</span>
                        </span>
                        {action(w, site)}
                      </div>
                      <ul className="ml-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-600">
                        {w.items.slice(0, MAX_ITEMS).map((it) => (
                          <li key={it.id}>
                            <span className="font-mono text-gray-800">{it.id}</span>{' '}
                            {w.kind === 'unassigned_meters'
                              ? L(`last read ${fmtDate(it.last_seen)}`, `dernière lecture ${fmtDate(it.last_seen)}`)
                              : L(`since ${fmtDate(it.since)}`, `depuis le ${fmtDate(it.since)}`)}
                          </li>
                        ))}
                        {w.items.length > MAX_ITEMS && (
                          <li>{L(`and ${w.items.length - MAX_ITEMS} more`, `et ${w.items.length - MAX_ITEMS} autres`)}</li>
                        )}
                      </ul>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </div>
    </details>
  );
}
