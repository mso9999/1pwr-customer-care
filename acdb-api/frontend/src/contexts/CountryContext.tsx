import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

export interface CountryConfig {
  country_code: string;
  country_name: string;
  currency: string;
  currency_symbol: string;
  dial_code: string;
  sites: Record<string, string>;
}

interface CountryContextType {
  country: string;
  setCountry: (code: string) => void;
  config: CountryConfig | null;
  apiBase: string;
  loading: boolean;
}

const COUNTRY_ROUTES: Record<string, string> = {
  LS: '/api',
  BJ: '/api/bj',
};

const COUNTRY_LABELS: Record<string, string> = {
  LS: 'Lesotho',
  BJ: 'Benin',
};

const CountryContext = createContext<CountryContextType | null>(null);

export function CountryProvider({ children }: { children: ReactNode }) {
  const [country, setCountryState] = useState(
    () => localStorage.getItem('cc_country') || 'LS'
  );
  const [config, setConfig] = useState<CountryConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const apiBase = COUNTRY_ROUTES[country] || '/api';

  const setCountry = (code: string) => {
    localStorage.setItem('cc_country', code);
    setCountryState(code);
  };

  useEffect(() => {
    setLoading(true);
    fetch(`${apiBase}/config`)
      .then((r) => r.json())
      .then((data: CountryConfig) => {
        setConfig(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [apiBase]);

  return (
    <CountryContext.Provider value={{ country, setCountry, config, apiBase, loading }}>
      {children}
    </CountryContext.Provider>
  );
}

export function useCountry() {
  const ctx = useContext(CountryContext);
  if (!ctx) throw new Error('useCountry must be used within CountryProvider');
  return ctx;
}

export { COUNTRY_LABELS, COUNTRY_ROUTES };
