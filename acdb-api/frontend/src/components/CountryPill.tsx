import { useTranslation } from 'react-i18next';

export interface SiteEntry {
  /** Country ISO code that owns this site (e.g. ``"LS"``, ``"BN"``).
   *  May be ``null`` for legacy/unmapped community values. */
  country?: string | null;
}

interface Props<TSite extends SiteEntry> {
  sites: TSite[];
  /** Active country code, or ``''`` for "all countries". */
  value: string;
  onChange: (country: string) => void;
  className?: string;
}

/**
 * Country filter pill — toggles which country's sites are visible in the
 * companion site dropdown and which country's customers are returned by
 * the consolidated 1PDB.
 *
 * Renders nothing when the registry only has one country (no choice to make);
 * 1PWR CC stays single-country-clean for ops on a single-country database.
 */
export default function CountryPill<TSite extends SiteEntry>({
  sites,
  value,
  onChange,
  className = '',
}: Props<TSite>) {
  const { t } = useTranslation('common');

  const countries = Array.from(
    new Set(
      sites
        .map(s => (s.country || '').toUpperCase())
        .filter(Boolean),
    ),
  ).sort();

  if (countries.length <= 1) return null;

  const baseBtn =
    'px-3 py-1.5 text-sm font-medium rounded-lg border transition whitespace-nowrap';
  const active = 'bg-blue-600 border-blue-600 text-white';
  const inactive = 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50';

  return (
    <div className={`inline-flex gap-1.5 ${className}`} role="group" aria-label={t('country')}>
      <button
        type="button"
        onClick={() => onChange('')}
        className={`${baseBtn} ${value === '' ? active : inactive}`}
      >
        {t('allCountries')}
      </button>
      {countries.map(c => (
        <button
          type="button"
          key={c}
          onClick={() => onChange(c)}
          className={`${baseBtn} ${value === c ? active : inactive}`}
        >
          {c}
        </button>
      ))}
    </div>
  );
}
