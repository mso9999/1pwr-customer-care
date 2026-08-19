import { useCallback, useEffect, useState } from 'react';
import {
  getCountrySites,
  createCountrySite,
  updateCountrySite,
  type CountrySite,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

const CODE_RE = /^[A-Z]{3}$/;

export default function SiteRegistryPage() {
  const { user } = useAuth();
  const roles = user?.roles || user?.cc_roles || (user?.role ? [user.role] : []);
  const isSuperadmin = roles.includes('superadmin');

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
      flash(`Site ${normalized} created (emergency local path). Prefer creating sites in PR so the full lifecycle is tracked.`);
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
          : `Site ${site.code} activated for commissioning.`
      );
      await reload();
    } catch (err: unknown) {
      // Activation without a canonical uGP design link requires an explicit
      // acknowledgement — offer it inline.
      const msg = err instanceof Error ? err.message : String(err);
      if (!site.active && msg.includes('confirm_missing_ugp_link')) {
        const confirmed = window.confirm(
          `${site.code} has no canonical uGP design linked.\n\n` +
            `Remediation: open the design in uGridPLAN and set its site association ` +
            `(or ask Engineering), then retry.\n\n` +
            `Activate ${site.code} WITHOUT the design link? This is audited.`
        );
        if (confirmed) {
          try {
            await updateCountrySite(site.code, { active: true, confirm_missing_ugp_link: true });
            flash(`Site ${site.code} activated without a uGP design link (audited).`);
            await reload();
          } catch (err2: unknown) {
            setError(err2 instanceof Error ? err2.message : String(err2));
          }
        }
      } else {
        setError(msg);
      }
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
          Sites are born in <strong>PR</strong> (pre-survey spend), get their canonical design in{' '}
          <strong>uGridPLAN</strong> after survey, and sync here automatically — staged inactive
          until someone activates them at commissioning. Activating a site makes it available to
          provisioning, customer onboarding, and account numbering. Codes are three uppercase
          letters, globally unique, and never reused: gateway identities and customer account
          numbers bind to them for life.
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

      {isSuperadmin ? (
        <form onSubmit={handleCreate} className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <h2 className="text-sm font-semibold text-gray-900 mb-1">Emergency local creation</h2>
          <p className="text-xs text-amber-800 mb-3">
            Superadmin break-glass only. The normal path is PR → Admin → Reference Data → Sites;
            sites created there sync here automatically.
          </p>
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
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Create site'}
            </button>
          </div>
        </form>
      ) : (
        <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
          New sites are created in <strong>PR</strong> (Admin → Reference Data → Sites) at the
          pre-survey stage and appear here automatically, staged inactive until commissioning.
        </div>
      )}

      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Code</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Name</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">District</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Source</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">uGP design</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-gray-600">Status</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-gray-400">
                  Loading…
                </td>
              </tr>
            ) : sites.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-gray-400">
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
                    ) : site.source === 'pr' ? (
                      <span className="rounded bg-purple-50 px-2 py-0.5 text-xs text-purple-700">
                        PR registry
                      </span>
                    ) : (
                      <span className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                        registry
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {site.canonical_ugp_project_id ? (
                      <span
                        className="rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700"
                        title={(site.ugp_project_ids || []).join(', ')}
                      >
                        {site.canonical_ugp_project_id}
                      </span>
                    ) : site.ugp_project_ids && site.ugp_project_ids.length > 0 ? (
                      <span
                        className="rounded bg-yellow-50 px-2 py-0.5 text-xs text-yellow-700"
                        title="Designs linked but none marked canonical"
                      >
                        linked, none canonical
                      </span>
                    ) : (
                      <span className="rounded bg-red-50 px-2 py-0.5 text-xs text-red-600">
                        missing
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {site.active ? (
                      <span className="rounded bg-green-50 px-2 py-0.5 text-xs text-green-700">active</span>
                    ) : site.source === 'pr' && !site.retired_by ? (
                      <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
                        staged
                      </span>
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
                        {site.active ? 'Retire' : site.source === 'pr' ? 'Activate' : 'Reactivate'}
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
