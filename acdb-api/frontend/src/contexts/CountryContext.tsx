import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { fetchPortfolios, type CountryEntry, type Portfolio } from '../lib/portfolioService';

export type { CountryEntry, Portfolio };

export interface CountryConfig {
  country_code: string;
  country_name: string;
  currency: string;
  currency_symbol: string;
  dial_code: string;
  sites: Record<string, string>;
}

interface CountryContextType {
  /* country */
  country: string;                      // CC country code, e.g. 'LS'
  setCountry: (code: string) => void;
  config: CountryConfig | null;
  apiBase: string;
  /* portfolio */
  portfolio: Portfolio | null;
  setPortfolio: (p: Portfolio | null) => void;
  /* reference lists from PR system */
  countries: CountryEntry[];
  portfolios: Portfolio[];
  loading: boolean;
}

const COUNTRY_ROUTES: Record<string, string> = {
  LS: '/api',
  BN: '/api/bn',
  ZM: '/api/zm',
};

const FALLBACK_COUNTRIES: CountryEntry[] = [
  { code: 'LS', name: 'Lesotho', flag: '\u{1F1F1}\u{1F1F8}', baseCurrency: 'LSL', portfolios: [] },
  { code: 'BN', name: 'Benin',   flag: '\u{1F1E7}\u{1F1EF}', baseCurrency: 'XOF', portfolios: [] },
];

const CountryContext = createContext<CountryContextType | null>(null);

export function CountryProvider({ children }: { children: ReactNode }) {
  const [country, setCountryState] = useState(
    () => localStorage.getItem('cc_country') || 'LS'
  );
  const [portfolioId, setPortfolioId] = useState(
    () => localStorage.getItem('cc_portfolio') || ''
  );
  const [config, setConfig] = useState<CountryConfig | null>(null);
  const [countries, setCountries] = useState<CountryEntry[]>(FALLBACK_COUNTRIES);
  const [allPortfolios, setAllPortfolios] = useState<Portfolio[]>([]);
  const [loading, setLoading] = useState(true);

  const apiBase = COUNTRY_ROUTES[country] || '/api';

  // ── Fetch organizations from PR system Firebase ──
  useEffect(() => {
    fetchPortfolios()
      .then(({ countries: c, portfolios: p }) => {
        if (c.length > 0) setCountries(c);
        setAllPortfolios(p);
      })
      .catch((err) => {
        console.warn('Failed to fetch portfolios from PR system, using fallback country list:', err);
      })
      .finally(() => setLoading(false));
  }, []);

  // ── Resolve selected portfolio object from stored id ──
  const portfolio = allPortfolios.find((p) => p.id === portfolioId) ?? null;

  const setCountry = (code: string) => {
    localStorage.setItem('cc_country', code);
    setCountryState(code);
  };

  const setPortfolio = (p: Portfolio | null) => {
    const id = p?.id ?? '';
    localStorage.setItem('cc_portfolio', id);
    setPortfolioId(id);
  };

  // ── Fetch country config from CC backend when country changes ──
  useEffect(() => {
    setLoading(true);
    fetch(`${apiBase}/config`)
      .then((r) => r.json())
      .then((data: CountryConfig) => {
        setConfig(data);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [apiBase]);

  return (
    <CountryContext.Provider
      value={{
        country,
        setCountry,
        config,
        apiBase,
        portfolio,
        setPortfolio,
        countries,
        portfolios: allPortfolios,
        loading,
      }}
    >
      {children}
    </CountryContext.Provider>
  );
}

export function useCountry() {
  const ctx = useContext(CountryContext);
  if (!ctx) throw new Error('useCountry must be used within CountryProvider');
  return ctx;
}

export { COUNTRY_ROUTES };
