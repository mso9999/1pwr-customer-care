import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  bulkProgramMembership,
  createProgram,
  downloadProgramConnections,
  issueProgramToken,
  listProgramMemberships,
  listProgramTokens,
  listPrograms,
  previewProgramDataset,
  revokeProgramToken,
  type Program,
  type ProgramMembership,
  type ProgramTokenIssued,
  type ProgramTokenSummary,
} from '../lib/api';

// ---------------------------------------------------------------------------
// New-program form
// ---------------------------------------------------------------------------

function NewProgramForm({ onCreated }: { onCreated: (p: Program) => void }) {
  const { t } = useTranslation(['programs', 'common']);
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [funder, setFunder] = useState('');
  const [country, setCountry] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || !name.trim()) return;
    setBusy(true);
    setErr('');
    try {
      const p = await createProgram({
        code: code.trim().toUpperCase(),
        name: name.trim(),
        funder: funder.trim() || undefined,
        country_code: country.trim().toUpperCase() || undefined,
        description: description.trim() || undefined,
      });
      setCode('');
      setName('');
      setFunder('');
      setCountry('');
      setDescription('');
      onCreated(p);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="bg-white rounded-lg shadow p-4 space-y-3">
      <h2 className="text-sm font-semibold text-gray-700">{t('programs:newProgram')}</h2>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:code')}</label>
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder={t('programs:codePlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm" required />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:name')}</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className="w-full px-3 py-2 border rounded-lg text-sm" required />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:funder')}</label>
          <input value={funder} onChange={(e) => setFunder(e.target.value)} className="w-full px-3 py-2 border rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:country')}</label>
          <input value={country} onChange={(e) => setCountry(e.target.value)} maxLength={2} placeholder="ZM" className="w-full px-3 py-2 border rounded-lg text-sm uppercase" />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-500 mb-1">{t('programs:description')}</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className="w-full px-3 py-2 border rounded-lg text-sm" />
        </div>
      </div>
      <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
        {busy ? t('programs:creating') : t('programs:create')}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Bulk-tag form
// ---------------------------------------------------------------------------

function BulkTagForm({ programCode, onApplied }: { programCode: string; onApplied: () => void }) {
  const { t } = useTranslation(['programs']);
  const [action, setAction] = useState<'add' | 'remove'>('add');
  const [countries, setCountries] = useState('');
  const [sites, setSites] = useState('');
  const [accts, setAccts] = useState('');
  const [milestone, setMilestone] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState<{ affected: number; skipped: number } | null>(null);

  const splitList = (s: string) =>
    s
      .split(/[,\s]+/)
      .map((x) => x.trim())
      .filter(Boolean);

  const apply = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    setResult(null);
    try {
      const r = await bulkProgramMembership(programCode, {
        action,
        country_codes: splitList(countries.toUpperCase()),
        site_codes: splitList(sites.toUpperCase()),
        account_numbers: splitList(accts),
        claim_milestone: milestone.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      setResult({ affected: r.affected_count, skipped: r.skipped_unknown.length });
      setCountries('');
      setSites('');
      setAccts('');
      onApplied();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={apply} className="bg-white rounded-lg shadow p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-gray-700">{t('programs:bulkTagTitle')}</h3>
        <p className="text-xs text-gray-500">{t('programs:bulkTagDesc')}</p>
      </div>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}
      {result && (
        <p className="text-emerald-700 text-sm bg-emerald-50 p-2 rounded">
          {t('programs:bulkResult', { affected: result.affected, skipped: result.skipped })}
        </p>
      )}
      <div className="flex gap-2">
        <button type="button" onClick={() => setAction('add')} className={`px-3 py-1.5 rounded-md text-sm ${action === 'add' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600'}`}>
          {t('programs:actionAdd')}
        </button>
        <button type="button" onClick={() => setAction('remove')} className={`px-3 py-1.5 rounded-md text-sm ${action === 'remove' ? 'bg-red-600 text-white' : 'bg-gray-100 text-gray-600'}`}>
          {t('programs:actionRemove')}
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:countries')}</label>
          <input value={countries} onChange={(e) => setCountries(e.target.value)} placeholder={t('programs:countriesPlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm uppercase" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:sites')}</label>
          <input value={sites} onChange={(e) => setSites(e.target.value)} placeholder={t('programs:sitesPlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm uppercase" />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-500 mb-1">{t('programs:accounts')}</label>
          <textarea value={accts} onChange={(e) => setAccts(e.target.value)} rows={2} placeholder={t('programs:accountsPlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm font-mono" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:milestone')}</label>
          <input value={milestone} onChange={(e) => setMilestone(e.target.value)} placeholder={t('programs:milestonePlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:notes')}</label>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} className="w-full px-3 py-2 border rounded-lg text-sm" />
        </div>
      </div>
      <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
        {busy ? t('programs:applying') : t('programs:applyTag')}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Memberships table
// ---------------------------------------------------------------------------

function MembershipsTable({ programCode, refreshKey }: { programCode: string; refreshKey: number }) {
  const { t } = useTranslation(['programs']);
  const [rows, setRows] = useState<ProgramMembership[]>([]);
  const [search, setSearch] = useState('');
  const [site, setSite] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    setLoading(true);
    listProgramMemberships(programCode, {
      search: search.trim() || undefined,
      site: site.trim().toUpperCase() || undefined,
      page,
      page_size: pageSize,
    })
      .then((r) => {
        setRows(r.items);
        setTotal(r.total);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [programCode, search, site, page, pageSize, refreshKey]);

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-700">{t('programs:membershipsTitle')}</h3>
          <p className="text-xs text-gray-500">{t('programs:membershipsDesc')}</p>
        </div>
        <div className="flex gap-2">
          <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder={t('programs:searchPlaceholder')} className="px-3 py-1.5 border rounded-lg text-sm" />
          <input value={site} onChange={(e) => { setSite(e.target.value); setPage(1); }} placeholder={t('programs:filterSite')} maxLength={6} className="w-24 px-3 py-1.5 border rounded-lg text-sm uppercase" />
        </div>
      </div>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}
      {loading ? (
        <div className="text-center py-6 text-gray-400">{t('programs:loading')}</div>
      ) : rows.length === 0 ? (
        <div className="text-center py-6 text-gray-400">{t('programs:empty')}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colAccount')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colCustomer')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colSite')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colMilestone')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colJoinedAt')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colNotes')}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((m) => (
                <tr key={m.account_number} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 font-mono">{m.account_number}</td>
                  <td className="px-3 py-1.5">{m.customer_name || <span className="text-gray-300">--</span>}</td>
                  <td className="px-3 py-1.5">{m.site_id || ''}</td>
                  <td className="px-3 py-1.5">{m.claim_milestone || ''}</td>
                  <td className="px-3 py-1.5 text-gray-500 text-xs">{m.joined_at?.slice(0, 10) || ''}</td>
                  <td className="px-3 py-1.5 text-gray-500">{m.notes || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > pageSize && (
        <div className="flex justify-between items-center text-xs text-gray-500">
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="px-3 py-1 border rounded disabled:opacity-50">Prev</button>
          <span>{`${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)} / ${total}`}</span>
          <button onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= total} className="px-3 py-1 border rounded disabled:opacity-50">Next</button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Token panel
// ---------------------------------------------------------------------------

function TokensPanel({ programCode }: { programCode: string }) {
  const { t } = useTranslation(['programs', 'common']);
  const [tokens, setTokens] = useState<ProgramTokenSummary[]>([]);
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [label, setLabel] = useState('');
  const [lifetime, setLifetime] = useState('90');
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<ProgramTokenIssued | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = () => {
    setLoading(true);
    listProgramTokens(programCode, includeRevoked)
      .then(setTokens)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode, includeRevoked]);

  const issue = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!label.trim()) return;
    setBusy(true);
    setErr('');
    try {
      const r = await issueProgramToken(programCode, {
        label: label.trim(),
        lifetime_days: lifetime ? Number(lifetime) : null,
      });
      setIssued(r);
      setCopied(false);
      setLabel('');
      refresh();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: number, lbl: string) => {
    if (!confirm(t('programs:revokeConfirm', { label: lbl }))) return;
    try {
      await revokeProgramToken(programCode, id);
      refresh();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const tokenStatus = (tok: ProgramTokenSummary) => {
    if (tok.revoked_at) return t('programs:tokenStatusRevoked');
    if (tok.expires_at && new Date(tok.expires_at).getTime() < Date.now()) return t('programs:tokenStatusExpired');
    return t('programs:tokenStatusActive');
  };

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-gray-700">{t('programs:tokensTitle')}</h3>
        <p className="text-xs text-gray-500">{t('programs:tokensDesc')}</p>
      </div>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}

      <form onSubmit={issue} className="flex flex-wrap gap-2 items-end">
        <div className="flex-1 min-w-[180px]">
          <label className="block text-xs text-gray-500 mb-1">{t('programs:tokenLabel')}</label>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t('programs:tokenLabelPlaceholder')} className="w-full px-3 py-2 border rounded-lg text-sm" required />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:lifetimeDays')}</label>
          <input value={lifetime} onChange={(e) => setLifetime(e.target.value)} type="number" min="1" max="3650" className="w-24 px-3 py-2 border rounded-lg text-sm" />
        </div>
        <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          {busy ? t('programs:issuing') : t('programs:issueToken')}
        </button>
      </form>
      <p className="text-xs text-gray-400">{t('programs:lifetimeNote')}</p>

      {issued && (
        <div className="border-2 border-amber-300 bg-amber-50 rounded-lg p-4 space-y-3">
          <div>
            <h4 className="font-semibold text-amber-900">{t('programs:newTokenTitle')}</h4>
            <p className="text-xs text-amber-800">{t('programs:newTokenWarning')}</p>
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-white border border-amber-200 rounded px-3 py-2 text-xs break-all">{issued.token}</code>
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(issued.token);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                } catch {
                  /* clipboard not available */
                }
              }}
              className="px-3 py-2 bg-amber-600 text-white rounded text-xs hover:bg-amber-700 whitespace-nowrap"
            >
              {copied ? t('programs:copied') : t('programs:copy')}
            </button>
          </div>
          <button
            type="button"
            onClick={() => setIssued(null)}
            className="px-3 py-1.5 bg-amber-200 text-amber-900 rounded text-xs hover:bg-amber-300"
          >
            {t('programs:iSavedIt')}
          </button>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs text-gray-500">
        <input type="checkbox" checked={includeRevoked} onChange={(e) => setIncludeRevoked(e.target.checked)} />
        {t('programs:showRevoked')}
      </label>

      {loading ? (
        <div className="text-center py-4 text-gray-400 text-sm">{t('programs:loading')}</div>
      ) : tokens.length === 0 ? (
        <div className="text-center py-4 text-gray-400 text-sm">--</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colTokenLabel')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colPrefix')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colIssued')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colExpires')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colLastUsed')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">{t('programs:colTokenStatus')}</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {tokens.map((tk) => {
                const status = tokenStatus(tk);
                const isActive = !tk.revoked_at && (!tk.expires_at || new Date(tk.expires_at).getTime() > Date.now());
                return (
                  <tr key={tk.id} className="hover:bg-gray-50">
                    <td className="px-3 py-1.5">{tk.label}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{tk.token_prefix}…</td>
                    <td className="px-3 py-1.5 text-gray-500 text-xs">{tk.issued_at?.slice(0, 10) || ''}</td>
                    <td className="px-3 py-1.5 text-gray-500 text-xs">{tk.expires_at?.slice(0, 10) || '--'}</td>
                    <td className="px-3 py-1.5 text-gray-500 text-xs">{tk.last_used_at?.slice(0, 10) || '--'}</td>
                    <td className="px-3 py-1.5">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                        status === t('programs:tokenStatusActive') ? 'bg-emerald-100 text-emerald-800'
                          : status === t('programs:tokenStatusExpired') ? 'bg-amber-100 text-amber-800'
                          : 'bg-gray-100 text-gray-700'
                      }`}>
                        {status}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      {isActive && (
                        <button onClick={() => revoke(tk.id, tk.label)} className="text-red-600 hover:text-red-800 text-xs">
                          {t('programs:revoke')}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview panel
// ---------------------------------------------------------------------------

function PreviewPanel({ programCode }: { programCode: string }) {
  const { t } = useTranslation(['programs']);
  const [dataset, setDataset] = useState<'electricity-payment' | 'meter-metrics'>('electricity-payment');
  const today = useMemo(() => new Date(), []);
  const yesterday = useMemo(() => new Date(today.getTime() - 24 * 60 * 60 * 1000), [today]);
  const [from, setFrom] = useState(yesterday.toISOString().slice(0, 10));
  const [to, setTo] = useState(today.toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [preview, setPreview] = useState<{ total: number; count: number; data: Record<string, unknown>[] } | null>(null);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    setPreview(null);
    try {
      const r = await previewProgramDataset(programCode, {
        dataset,
        from: `${from}T00:00:00Z`,
        to: `${to}T00:00:00Z`,
        page_size: 50,
      });
      setPreview({ total: r.total, count: r.count, data: r.data });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-gray-700">{t('programs:previewTitle')}</h3>
        <p className="text-xs text-gray-500">{t('programs:previewDesc')}</p>
      </div>
      <form onSubmit={run} className="flex flex-wrap gap-2 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:datasetPayments')} / {t('programs:datasetMetrics')}</label>
          <select value={dataset} onChange={(e) => setDataset(e.target.value as typeof dataset)} className="px-3 py-2 border rounded-lg text-sm bg-white">
            <option value="electricity-payment">{t('programs:datasetPayments')}</option>
            <option value="meter-metrics">{t('programs:datasetMetrics')}</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:from')}</label>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="px-3 py-2 border rounded-lg text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:to')}</label>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="px-3 py-2 border rounded-lg text-sm" />
        </div>
        <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          {busy ? t('programs:previewing') : t('programs:runPreview')}
        </button>
      </form>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}
      {preview && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">{t('programs:previewCount', { count: preview.count, total: preview.total })}</p>
          <pre className="bg-gray-900 text-emerald-200 text-xs rounded p-3 overflow-auto max-h-96">{JSON.stringify(preview.data, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connections claim export panel (Phase 3)
// ---------------------------------------------------------------------------

function ConnectionsExportPanel({ programCode }: { programCode: string }) {
  const { t } = useTranslation(['programs']);
  const [milestone, setMilestone] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const exportNow = async () => {
    setBusy(true);
    setErr('');
    try {
      await downloadProgramConnections(programCode, milestone.trim() || undefined);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-gray-700">{t('programs:exportTitle')}</h3>
        <p className="text-xs text-gray-500">{t('programs:exportDesc')}</p>
      </div>
      {err && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}
      <div className="flex flex-wrap gap-2 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('programs:milestone')}</label>
          <input value={milestone} onChange={(e) => setMilestone(e.target.value)} placeholder={t('programs:milestonePlaceholder')} className="px-3 py-2 border rounded-lg text-sm" />
        </div>
        <button onClick={exportNow} disabled={busy} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50">
          {busy ? t('programs:exporting') : t('programs:exportButton')}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page root
// ---------------------------------------------------------------------------

export default function ProgramsPage() {
  const { t } = useTranslation(['programs']);
  const [programs, setPrograms] = useState<Program[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [selectedCode, setSelectedCode] = useState<string>('');
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = () => {
    setLoading(true);
    listPrograms()
      .then((rows) => {
        setPrograms(rows);
        if (!selectedCode && rows.length > 0) {
          setSelectedCode(rows[0].code);
        }
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onCreated = (p: Program) => {
    setPrograms((prev) => [p, ...prev]);
    setSelectedCode(p.code);
  };

  const selected = programs.find((p) => p.code === selectedCode) ?? null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('programs:title')}</h1>
        <p className="text-sm text-gray-500">{t('programs:subtitle')}</p>
      </div>

      {err && <p className="text-red-600 text-sm bg-red-50 p-3 rounded">{err}</p>}

      <NewProgramForm onCreated={onCreated} />

      {loading ? (
        <div className="text-center py-6 text-gray-400">{t('programs:loading')}</div>
      ) : programs.length === 0 ? (
        <div className="text-center py-6 text-gray-400 bg-white rounded-lg shadow">{t('programs:empty')}</div>
      ) : (
        <>
          <div className="bg-white rounded-lg shadow p-4">
            <label className="block text-xs text-gray-500 mb-1">{t('programs:selectProgram')}</label>
            <div className="flex flex-wrap gap-2">
              {programs.map((p) => (
                <button
                  key={p.code}
                  onClick={() => setSelectedCode(p.code)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium border transition ${
                    selectedCode === p.code
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <span className="font-semibold">{p.code}</span>
                  <span className="ml-2 text-[10px] opacity-80">
                    {p.country_code || '--'} · {p.member_count} {t('programs:members')} · {p.active_token_count} {t('programs:tokens')}
                  </span>
                </button>
              ))}
            </div>
            {selected && (
              <div className="mt-3 text-xs text-gray-500">
                <p><strong className="text-gray-700">{selected.name}</strong>{selected.funder ? ` · ${selected.funder}` : ''}</p>
                {selected.description && <p className="mt-1">{selected.description}</p>}
              </div>
            )}
          </div>

          {selected && (
            <>
              <BulkTagForm programCode={selected.code} onApplied={() => { setRefreshKey((k) => k + 1); refresh(); }} />
              <MembershipsTable programCode={selected.code} refreshKey={refreshKey} />
              <TokensPanel programCode={selected.code} />
              <PreviewPanel programCode={selected.code} />
              <ConnectionsExportPanel programCode={selected.code} />
            </>
          )}
        </>
      )}
    </div>
  );
}
