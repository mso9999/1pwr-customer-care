import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getSiteReporting, type SiteReportingFlag } from '../lib/api';
import { formatLastSeen } from '../lib/datetime';

/**
 * Sites where the 1Meters have gone quiet. A live SparkMeter or site PCS means
 * the village is up and the 1Meters are the problem. Every source silent means
 * a site-wide outage. The two are drawn differently so they are not read as
 * the same event.
 */
export default function SiteReportingFlags() {
  const { i18n } = useTranslation();
  const fr = i18n.language?.startsWith('fr');
  const L = (en: string, frText: string) => (fr ? frText : en);
  const [sites, setSites] = useState<SiteReportingFlag[]>([]);

  useEffect(() => {
    let cancel = false;
    const load = () => {
      getSiteReporting()
        .then((r) => { if (!cancel) setSites(r.sites || []); })
        .catch(() => { if (!cancel) setSites([]); });
    };
    load();
    const timer = window.setInterval(load, 60_000);
    return () => { cancel = true; window.clearInterval(timer); };
  }, []);

  if (!sites.length) return null;

  const faultText = (s: SiteReportingFlag) => {
    const n = s.onemeter_known;
    const tail = L(
      `but none of the ${n} 1Meters has reported in the last 20 minutes. This is not a site-wide outage.`,
      `mais aucun des ${n} 1Meters n’a rapporté depuis 20 minutes. Ce n’est pas une coupure de site.`,
    );
    if (s.spark_live && s.pcs_live) {
      return L(`SparkMeters and the site PCS are reporting now, ${tail}`, `Les SparkMeters et le PCS du site rapportent en temps réel, ${tail}`);
    }
    if (s.spark_live) {
      return L(`SparkMeters are reporting now, ${tail}`, `Les SparkMeters rapportent en temps réel, ${tail}`);
    }
    return L(`The site PCS is reporting now, ${tail}`, `Le PCS du site rapporte en temps réel, ${tail}`);
  };

  const silentWitnesses = (s: SiteReportingFlag) => {
    const parts = ['1Meters'];
    if (s.has_spark) parts.push('SparkMeters');
    if (s.has_pcs) parts.push(L('the site PCS', 'le PCS du site'));
    if (parts.length === 1) return parts[0];
    const last = parts.pop();
    return L(`${parts.join(', ')} and ${last}`, `${parts.join(', ')} et ${last}`);
  };

  const when = (iso?: string | null) => (iso ? formatLastSeen(iso) : L('no recent reading', 'aucune lecture récente'));

  return (
    <div className="mb-4 space-y-2">
      {sites.map((s) => {
        const fault = s.condition === 'meter_fault';
        return (
          <div
            key={s.site}
            className={`rounded-xl border p-4 ${fault ? 'border-red-300 bg-red-50' : 'border-slate-300 bg-slate-100'}`}
          >
            <div className={`text-sm font-semibold ${fault ? 'text-red-950' : 'text-slate-900'}`}>
              {s.site}
              {' · '}
              {fault
                ? L('1Meter problem — the site is up', 'Problème 1Meter — le site est alimenté')
                : L('Site-wide outage — everything is silent', 'Coupure de site — tout est silencieux')}
            </div>
            <p className={`mt-1 text-sm ${fault ? 'text-red-900' : 'text-slate-700'}`}>
              {fault
                ? faultText(s)
                : L(
                  `${silentWitnesses(s)} are all silent. This matches a site-wide outage, not a fault limited to the 1Meters.`,
                  `${silentWitnesses(s)} sont tous silencieux. Cela correspond à une coupure de site, pas à une panne limitée aux 1Meters.`,
                )}
            </p>
            <p className={`mt-1 text-xs ${fault ? 'text-red-800' : 'text-slate-600'}`}>
              {L('1Meters last seen', '1Meters vus')}
              {': '}
              {when(s.onemeter_last_seen)}
              {s.has_spark && <> · SparkMeters: {s.spark_live ? L('reporting now', 'en temps réel') : when(s.spark_last_seen)}</>}
              {s.has_pcs && <> · {L('Site PCS', 'PCS du site')}: {s.pcs_live ? L('reporting now', 'en temps réel') : when(s.pcs_last_seen)}</>}
            </p>
          </div>
        );
      })}
    </div>
  );
}
