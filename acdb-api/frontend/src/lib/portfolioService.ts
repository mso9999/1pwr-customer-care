/**
 * Fetches the organization / portfolio list from the CC backend, which
 * proxies the PR system's Firestore (single source of truth).
 */

export interface Portfolio {
  id: string;
  name: string;
  code?: string;
  country?: string;
  baseCurrency: string;
  allowedCurrencies: string[];
}

export interface CountryEntry {
  code: string;
  name: string;
  flag: string;
  baseCurrency: string;
  portfolios: Portfolio[];
}

const COUNTRY_META: Record<string, { code: string; flag: string }> = {
  'Lesotho':  { code: 'LS', flag: '\u{1F1F1}\u{1F1F8}' },
  'Benin':    { code: 'BN', flag: '\u{1F1E7}\u{1F1EF}' },
  'Zambia':   { code: 'ZM', flag: '\u{1F1FF}\u{1F1F2}' },
  'Tanzania': { code: 'TZ', flag: '\u{1F1F9}\u{1F1FF}' },
  'Nigeria':  { code: 'NG', flag: '\u{1F1F3}\u{1F1EC}' },
};

let _cache: { countries: CountryEntry[]; portfolios: Portfolio[] } | null = null;

export async function fetchPortfolios(): Promise<{
  countries: CountryEntry[];
  portfolios: Portfolio[];
}> {
  if (_cache) return _cache;

  const res = await fetch('/api/portfolios');
  if (!res.ok) throw new Error(`portfolios fetch failed: ${res.status}`);

  const portfolios: Portfolio[] = await res.json();

  const byCountry = new Map<string, Portfolio[]>();
  for (const p of portfolios) {
    const key = p.country || 'Unknown';
    const arr = byCountry.get(key) || [];
    arr.push(p);
    byCountry.set(key, arr);
  }

  const countries: CountryEntry[] = [];
  for (const [name, portfs] of byCountry) {
    const meta = COUNTRY_META[name] ?? { code: name.substring(0, 2).toUpperCase(), flag: '' };
    countries.push({
      code: meta.code,
      name,
      flag: meta.flag,
      baseCurrency: portfs[0]?.baseCurrency ?? 'USD',
      portfolios: portfs,
    });
  }

  countries.sort((a, b) => a.name.localeCompare(b.name));

  _cache = { countries, portfolios };
  return _cache;
}

export function clearPortfolioCache() {
  _cache = null;
}
