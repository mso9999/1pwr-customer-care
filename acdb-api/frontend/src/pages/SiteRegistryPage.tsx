import { useCallback, useEffect, useState } from 'react';
import {
  getCountrySites,
  createCountrySite,
  updateCountrySite,
  type CountrySite,
} from '../lib/api';

const CODE_RE = /^[A-Z]{3}$/;

export default function SiteRegistryPage() {
  const [countryCode, setCountryCode] = useState('');
  const [sites, setSites] = useState<CountrySite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [district, setDistrict] = useState('');
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getCountrySites();
      setCountryCode(data.country_code);
      setSites(data.sites);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const flash = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(''), 5000);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    const normalized = code.trim().toUpperCase();
    if (!CODE_RE.test(normalized)) {
      setError('Site code must be exactly three uppercase letters (e.g. CHI).');
      return;
    }
    if (name.trim().length < 2) {
      setError('Enter the official display name for the site.');
      return;
    }
    setBusy(true);
    try {
      await createCountrySite({
        code: normalized,
        name: name.trim(),
        district: district.trim() || undefined,
      });
      flash(`Site ${normalized} created. It is immediately available for provisioning and customer onboarding.`);
      setCode('');
      setName('');
      setDistrict('');
      await reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleToggleActive = async (site: CountrySite) => {
    setError('');
    setBusy(true);
    try {
      await updateCountrySite(site.code, { active: !site.active });
      flash(
        site.active
          ? `Site ${site.code} retired. It no longer appears for new provisioning or onboarding; existing accounts and gateways are unaffected.`
          : `Site ${site.code} reactivated.`
      );
      await reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const inputCls =
    'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500';

  return (
    <div className="max-w-4xl mx-auto p-4 sm:p-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-gray-900">Site Registry — {countryCode}</h1>
        <p className="text-sm text-gray-500 mt-1">
          Canonical deployment site codes for this country. Creating a site here makes it
          immediately available to provisioning, customer onboarding, and account numbering — no
          code deploy required. Codes are three uppercase letters, are globally unique, and are
          never reused: gateway identities and customer account numbers bind to them for life.
          Retiring deactivates a code; it never deletes it.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 whitespace-pre-wrap">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          {success}
        </div>
      )}

      <form onSubmit={handleCreate} className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-900 mb-3">Add a deployment site</h2>
        <div className="grid grid-cols-1 sm:grid-cols-[120px_1fr_1fr_auto] gap-3 items-end">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Code</label>
            <input
              className={`${inputCls} uppercase font-mono`}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3))}
              placeholder="CHI"
              maxLength={3}
              required
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Official name</label>
            <input
              className={inputCls}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Chinsali"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">District / Province</label>
            <input
              className={inputCls}
              value={district}
              onChange={(e) => setDistrict(e.target.value)}
              placeholder="Muchinga"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Create site'}
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Confirm the code with the country lead before creating — it cannot be changed afterwards.
        </p>
      </form>

      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Code</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Name</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">District</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Source</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Status</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-gray-400">
                  Loading…
                </td>
              </tr>
            ) : sites.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-gray-400">
                  No sites registered for {countryCode} yet.
                </td>
              </tr>
            ) : (
              sites.map((site) => (
                <tr key={site.code} className={site.active ? '' : 'bg-gray-50 text-gray-400'}>
                  <td className="px-4 py-2 font-mono font-semibold">{site.code}</td>
                  <td className="px-4 py-2">{site.name}</td>
                  <td className="px-4 py-2">{site.district || '—'}</td>
                  <td className="px-4 py-2">
                    {site.source === 'config' ? (
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                        code-defined
                      </span>
                    ) : (
                      <span className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                        registry
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {site.active ? (
                      <span className="rounded bg-green-50 px-2 py-0.5 text-xs text-green-700">active</span>
                    ) : (
                      <span className="rounded bg-gray-200 px-2 py-0.5 text-xs text-gray-600">
                        retired{site.retired_by ? ` by ${site.retired_by}` : ''}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {site.source !== 'config' && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleToggleActive(site)}
                        className={`text-xs font-semibold ${
                          site.active
                            ? 'text-red-600 hover:text-red-800'
                            : 'text-blue-600 hover:text-blue-800'
                        } disabled:opacity-50`}
                      >
                        {site.active ? 'Retire' : 'Reactivate'}
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
