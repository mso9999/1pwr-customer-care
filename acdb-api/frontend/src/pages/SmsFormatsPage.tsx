import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import {
  createSmsFormat,
  deleteSmsFormat,
  getSmsFormats,
  getSmsObservedSenders,
  getSmsUnprocessed,
  previewSmsFormat,
  replaySmsUnprocessed,
  testSmsFormat,
  updateSmsFormat,
  updateSmsFormatSettings,
  type SmsFormat,
  type SmsFormatInput,
  type SmsFormatPreview,
  type SmsFormatSettings,
  type SmsFormatsResponse,
  type SmsFormatTestResult,
  type SmsObservedSender,
  type SmsSummary,
  type SmsUnprocessedRow,
} from '../lib/api';

type Tab = 'formats' | 'unprocessed' | 'settings';

const PROVIDER_SUGGESTIONS: Record<string, string[]> = {
  LS: ['mpesa', 'ecocash'],
  BN: ['mtn_momo_bj', 'moov_money_bj', 'celtiis_cash_bj'],
};

const PLACEHOLDERS = ['{amount}', '{account}', '{phone}', '{txn_id}', '{remark}', '{*}'];

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function emptyDraft(decimal: '.' | ','): SmsFormatInput {
  return {
    kind: 'payment',
    provider: '',
    name: '',
    pattern_type: 'template',
    pattern: '',
    sender_pattern: '',
    decimal_separator: decimal,
    phone_prefix: '',
    priority: 100,
    enabled: false,
    samples: [{ text: '', sender: '' }],
    notes: '',
  };
}

function toInput(f: SmsFormat): SmsFormatInput {
  return {
    kind: f.kind,
    provider: f.provider,
    name: f.name,
    pattern_type: f.pattern_type,
    pattern: f.pattern,
    sender_pattern: f.sender_pattern || '',
    decimal_separator: f.decimal_separator,
    phone_prefix: f.phone_prefix || '',
    priority: f.priority,
    enabled: f.enabled,
    samples: f.samples?.length ? f.samples : [{ text: '', sender: '' }],
    notes: f.notes || '',
  };
}

function cleanInput(d: SmsFormatInput): SmsFormatInput {
  return {
    ...d,
    sender_pattern: d.sender_pattern?.trim() || null,
    phone_prefix: d.phone_prefix?.trim() || null,
    notes: d.notes?.trim() || null,
    samples: d.samples
      .filter((s) => s.text.trim())
      .map((s) => ({
        ...s,
        expect_amount: s.expect_amount === undefined || s.expect_amount === null || Number.isNaN(s.expect_amount) ? null : s.expect_amount,
        expect_account: s.expect_account?.trim() || null,
        expect_txn_id: s.expect_txn_id?.trim() || null,
      })),
  };
}

function Summary({ s }: { s: SmsSummary | null | undefined }) {
  const { t } = useTranslation('smsFormats');
  if (!s || s.kind === 'unparsed') return <span className="text-gray-400">{t('unparsed')}</span>;
  if (s.kind === 'balance_request') return <span>{t('kind.balance_request')} {s.account_hint}</span>;
  return (
    <span className="font-mono text-xs">
      {s.amount} · {s.account_hint || '—'} · {s.txn_id || '—'} · {s.provider} <span className="text-gray-400">({s.format_name})</span>
    </span>
  );
}

export default function SmsFormatsPage() {
  const { t } = useTranslation('smsFormats');
  const { user, isSuperadmin } = useAuth();
  const roles = user?.roles || user?.cc_roles || (user?.role ? [user.role] : []);
  const allowed = isSuperadmin || roles.some((r) => r === 'superadmin' || r === 'onm_team');

  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(searchParams.get('tab') === 'unprocessed' ? 'unprocessed' : 'formats');
  const [data, setData] = useState<SmsFormatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<SmsFormatInput | null>(null);
  const [testText, setTestText] = useState('');
  const [testSender, setTestSender] = useState('');
  const [testResult, setTestResult] = useState<SmsFormatTestResult | null>(null);
  const [preview, setPreview] = useState<SmsFormatPreview | null>(null);

  const [unprocessed, setUnprocessed] = useState<SmsUnprocessedRow[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [allowDupes, setAllowDupes] = useState(false);

  const [settings, setSettings] = useState<SmsFormatSettings | null>(null);
  const [sendersText, setSendersText] = useState('');
  const [observed, setObserved] = useState<SmsObservedSender[] | null>(null);

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(''), 6000);
  };

  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const d = await getSmsFormats();
      setData(d);
      setSettings(d.settings);
      setSendersText(d.settings.trusted_senders.join('\n'));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (allowed) reload();
  }, [allowed, reload]);

  const loadUnprocessed = useCallback(async () => {
    setError('');
    try {
      const r = await getSmsUnprocessed(30);
      setUnprocessed(r.rows);
      setSelected(new Set());
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  const loadObserved = useCallback(async () => {
    try {
      const r = await getSmsObservedSenders(30);
      setObserved(r.senders);
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    if (!allowed) return;
    if (tab === 'unprocessed' && unprocessed === null) loadUnprocessed();
    if (tab === 'settings' && observed === null) loadObserved();
  }, [tab, allowed, unprocessed, observed, loadUnprocessed, loadObserved]);

  const providers = useMemo(() => {
    const base = PROVIDER_SUGGESTIONS[data?.country_code || 'LS'] || [];
    const used = (data?.formats || []).map((f) => f.provider);
    return Array.from(new Set([...base, ...used]));
  }, [data]);

  if (!allowed) {
    return (
      <div className="max-w-2xl mx-auto my-12 rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-800">
        {t('denied')}
      </div>
    );
  }

  const startNew = () => {
    setEditingId(null);
    setDraft(emptyDraft(data?.default_decimal_separator || '.'));
    setTestResult(null);
    setPreview(null);
    setTestText('');
    setTestSender('');
  };

  const startEdit = (f: SmsFormat) => {
    setEditingId(f.id);
    setDraft(toInput(f));
    setTestResult(null);
    setPreview(null);
    setTestText(f.samples?.[0]?.text || '');
    setTestSender(f.samples?.[0]?.sender || '');
  };

  const draftPayload = () => (draft ? { ...cleanInput(draft), id: editingId } : null);

  const runTest = async () => {
    if (!draft) return;
    setBusy(true);
    setError('');
    try {
      setTestResult(await testSmsFormat({ draft: draftPayload(), text: testText, sender: testSender }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const runPreview = async () => {
    if (!draft) return;
    setBusy(true);
    setError('');
    try {
      setPreview(await previewSmsFormat({ draft: draftPayload(), days: 30 }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError('');
    try {
      const body = cleanInput(draft);
      if (editingId) await updateSmsFormat(editingId, body);
      else await createSmsFormat(body);
      flash(t('saved'));
      setDraft(null);
      setEditingId(null);
      await reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (f: SmsFormat) => {
    setBusy(true);
    setError('');
    try {
      await updateSmsFormat(f.id, cleanInput({ ...toInput(f), enabled: !f.enabled }));
      await reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (f: SmsFormat) => {
    if (!window.confirm(t('confirmDelete', { name: f.name }))) return;
    setBusy(true);
    try {
      await deleteSmsFormat(f.id);
      await reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const replay = async () => {
    setBusy(true);
    setError('');
    try {
      const r = await replaySmsUnprocessed({ log_ids: Array.from(selected), allow_possible_duplicates: allowDupes });
      const done = r.results.filter((x) => x.status === 'replayed').length;
      const skipped = r.results.filter((x) => x.status !== 'replayed');
      flash(t('replayResult', { done, skipped: skipped.length })
        + (skipped.length ? ' — ' + skipped.map((s) => `#${s.log_id}: ${s.reason}`).join('; ') : ''));
      await loadUnprocessed();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async (patch: Partial<SmsFormatSettings>) => {
    setBusy(true);
    setError('');
    try {
      const s = await updateSmsFormatSettings(patch);
      setSettings(s);
      setSendersText(s.trusted_senders.join('\n'));
      flash(t('saved'));
      await loadObserved();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const setSample = (i: number, patch: Partial<SmsFormatInput['samples'][number]>) => {
    if (!draft) return;
    const samples = draft.samples.map((s, j) => (j === i ? { ...s, ...patch } : s));
    setDraft({ ...draft, samples });
  };

  const input = 'w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none';
  const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

  return (
    <div className="max-w-6xl mx-auto p-4 space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">{t('title')}</h1>
        <p className="text-sm text-gray-600 mt-1">{t('intro', { country: data?.country_code || '' })}</p>
      </div>

      {error && <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-800 whitespace-pre-wrap">{error}</div>}
      {notice && <div className="rounded-md bg-green-50 border border-green-200 p-3 text-sm text-green-800">{notice}</div>}

      <div className="flex gap-2 border-b border-gray-200">
        {(['formats', 'unprocessed', 'settings'] as Tab[]).map((k) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${tab === k ? 'border-blue-600 text-blue-700 font-medium' : 'border-transparent text-gray-600'}`}
          >
            {t(`tabs.${k}`)}
          </button>
        ))}
      </div>

      {loading && <div className="text-gray-400 text-sm">{t('loading')}</div>}

      {tab === 'formats' && data && (
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <p className="text-sm text-gray-600">{t('orderNote')}</p>
            <button onClick={startNew} className={`${btn} bg-blue-600 text-white`}>{t('newFormat')}</button>
          </div>

          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="px-3 py-2">{t('col.priority')}</th>
                  <th className="px-3 py-2">{t('col.kind')}</th>
                  <th className="px-3 py-2">{t('col.provider')}</th>
                  <th className="px-3 py-2">{t('col.name')}</th>
                  <th className="px-3 py-2">{t('col.samples')}</th>
                  <th className="px-3 py-2">{t('col.status')}</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {data.formats.map((f) => {
                  const bad = (f.sample_results || []).filter((r) => !r.ok).length;
                  return (
                    <tr key={f.id} className="border-t border-gray-100">
                      <td className="px-3 py-2">{f.priority}</td>
                      <td className="px-3 py-2">{t(`kind.${f.kind}`)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{f.provider}</td>
                      <td className="px-3 py-2">
                        {f.name}
                        {f.compile_error && <div className="text-xs text-red-600">{f.compile_error}</div>}
                      </td>
                      <td className="px-3 py-2">
                        {bad ? <span className="text-red-600">{t('samplesFailing', { count: bad })}</span>
                          : <span className="text-green-700">{t('samplesOk', { count: f.sample_results?.length || 0 })}</span>}
                      </td>
                      <td className="px-3 py-2">
                        <button onClick={() => toggleEnabled(f)} disabled={busy}
                          className={`rounded-full px-2 py-0.5 text-xs ${f.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                          {f.enabled ? t('enabled') : t('disabled')}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <button onClick={() => startEdit(f)} className="text-blue-700 text-sm mr-3">{t('edit')}</button>
                        <button onClick={() => remove(f)} className="text-red-600 text-sm">{t('delete')}</button>
                      </td>
                    </tr>
                  );
                })}
                {data.builtins.map((b) => (
                  <tr key={b.name} className="border-t border-gray-100 bg-gray-50/50 text-gray-500">
                    <td className="px-3 py-2">{t('last')}</td>
                    <td className="px-3 py-2">{t('kind.payment')}</td>
                    <td className="px-3 py-2 font-mono text-xs">{b.provider}</td>
                    <td className="px-3 py-2">{b.name}<div className="text-xs font-mono truncate max-w-md">{b.example}</div></td>
                    <td className="px-3 py-2" colSpan={3}>{t('builtin')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {draft && (
            <div className="rounded-lg border border-blue-200 bg-white p-4 space-y-4">
              <h2 className="font-semibold text-gray-900">{editingId ? t('editFormat') : t('newFormat')}</h2>
              <div className="grid gap-3 sm:grid-cols-3">
                <label className="text-sm">{t('field.kind')}
                  <select className={input} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as SmsFormatInput['kind'] })}>
                    <option value="payment">{t('kind.payment')}</option>
                    <option value="balance_request">{t('kind.balance_request')}</option>
                  </select>
                </label>
                <label className="text-sm">{t('field.provider')}
                  <input className={input} list="sms-providers" value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value })} />
                  <datalist id="sms-providers">{providers.map((p) => <option key={p} value={p} />)}</datalist>
                </label>
                <label className="text-sm">{t('field.name')}
                  <input className={input} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </label>
              </div>

              <div>
                <div className="flex items-center gap-4 text-sm mb-1">
                  <span className="font-medium">{t('field.pattern')}</span>
                  <label><input type="radio" checked={draft.pattern_type === 'template'} onChange={() => setDraft({ ...draft, pattern_type: 'template' })} /> {t('template')}</label>
                  <label><input type="radio" checked={draft.pattern_type === 'regex'} onChange={() => setDraft({ ...draft, pattern_type: 'regex' })} /> {t('regex')}</label>
                </div>
                <textarea className={`${input} font-mono`} rows={3} value={draft.pattern} onChange={(e) => setDraft({ ...draft, pattern: e.target.value })} />
                <p className="text-xs text-gray-500 mt-1">
                  {draft.pattern_type === 'template' ? t('templateHelp') : t('regexHelp')}{' '}
                  {draft.pattern_type === 'template' && PLACEHOLDERS.map((p) => (
                    <button key={p} type="button" className="font-mono text-blue-700 mr-2"
                      onClick={() => setDraft({ ...draft, pattern: draft.pattern + p })}>{p}</button>
                  ))}
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-4">
                <label className="text-sm">{t('field.sender')}
                  <input className={`${input} font-mono`} value={draft.sender_pattern || ''} placeholder="^MTN" onChange={(e) => setDraft({ ...draft, sender_pattern: e.target.value })} />
                </label>
                <label className="text-sm">{t('field.decimal')}
                  <select className={input} value={draft.decimal_separator || '.'} onChange={(e) => setDraft({ ...draft, decimal_separator: e.target.value as '.' | ',' })}>
                    <option value=".">{t('decimalDot')}</option>
                    <option value=",">{t('decimalComma')}</option>
                  </select>
                </label>
                <label className="text-sm">{t('field.phonePrefix')}
                  <input className={input} value={draft.phone_prefix || ''} placeholder={data.dial_code} onChange={(e) => setDraft({ ...draft, phone_prefix: e.target.value })} />
                </label>
                <label className="text-sm">{t('field.priority')}
                  <input type="number" className={input} value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) || 0 })} />
                </label>
              </div>

              <div className="space-y-2">
                <div className="flex justify-between items-center">
                  <span className="text-sm font-medium">{t('field.samples')}</span>
                  <button type="button" className="text-sm text-blue-700" onClick={() => setDraft({ ...draft, samples: [...draft.samples, { text: '', sender: '' }] })}>{t('addSample')}</button>
                </div>
                <p className="text-xs text-gray-500">{t('samplesHelp')}</p>
                {draft.samples.map((s, i) => (
                  <div key={i} className="grid gap-2 sm:grid-cols-12 items-start">
                    <textarea className={`${input} sm:col-span-6 font-mono text-xs`} rows={2} placeholder={t('sampleText')} value={s.text} onChange={(e) => setSample(i, { text: e.target.value })} />
                    <input className={`${input} sm:col-span-2`} placeholder={t('sampleSender')} value={s.sender || ''} onChange={(e) => setSample(i, { sender: e.target.value })} />
                    {draft.kind === 'payment' && (
                      <input className={`${input} sm:col-span-1`} placeholder={t('expectAmount')} value={s.expect_amount ?? ''} onChange={(e) => setSample(i, { expect_amount: e.target.value === '' ? null : Number(e.target.value) })} />
                    )}
                    <input className={`${input} sm:col-span-2`} placeholder={t('expectAccount')} value={s.expect_account || ''} onChange={(e) => setSample(i, { expect_account: e.target.value })} />
                    <button type="button" className="text-red-600 text-sm sm:col-span-1" onClick={() => setDraft({ ...draft, samples: draft.samples.filter((_, j) => j !== i) })}>✕</button>
                  </div>
                ))}
              </div>

              <label className="block text-sm">{t('field.notes')}
                <input className={input} value={draft.notes || ''} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
              </label>

              <div className="rounded-md bg-gray-50 p-3 space-y-2">
                <div className="text-sm font-medium">{t('testTitle')}</div>
                <div className="grid gap-2 sm:grid-cols-12">
                  <textarea className={`${input} sm:col-span-8 font-mono text-xs`} rows={2} placeholder={t('sampleText')} value={testText} onChange={(e) => setTestText(e.target.value)} />
                  <input className={`${input} sm:col-span-2`} placeholder={t('sampleSender')} value={testSender} onChange={(e) => setTestSender(e.target.value)} />
                  <button onClick={runTest} disabled={busy || !testText} className={`${btn} bg-gray-800 text-white sm:col-span-2`}>{t('test')}</button>
                </div>
                {testResult && (
                  <div className="text-sm space-y-1">
                    <div>{t('thisFormat')}: {testResult.draft_matched
                      ? <span className="text-green-700 font-mono text-xs">{JSON.stringify(testResult.draft_result)}</span>
                      : <span className="text-red-600">{t('noMatch')}</span>}</div>
                    <div>{t('pipeline')}: <b>{t(`kind.${testResult.pipeline.kind}`, { defaultValue: testResult.pipeline.kind })}</b>
                      {testResult.pipeline.parsed?.format_name && <> — {testResult.pipeline.parsed.format_name}</>}</div>
                    {testResult.resolution && (
                      <div>{t('account')}: {testResult.resolution.account
                        ? <b>{testResult.resolution.account}</b>
                        : <span className="text-red-600">{t('noAccount')}</span>} <span className="text-gray-500">({testResult.resolution.allocation}{testResult.resolution.reason ? `: ${testResult.resolution.reason}` : ''})</span></div>
                    )}
                    {!testResult.sender_trusted && testResult.sender_mode !== 'off' && (
                      <div className="text-amber-700">{t('untrustedSender', { mode: testResult.sender_mode })}</div>
                    )}
                    {testResult.compiled_regex && <details><summary className="text-xs text-gray-500 cursor-pointer">{t('showRegex')}</summary><code className="text-xs break-all">{testResult.compiled_regex}</code></details>}
                  </div>
                )}
              </div>

              <div className="rounded-md bg-gray-50 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{t('previewTitle')}</span>
                  <button onClick={runPreview} disabled={busy || !draft.pattern} className={`${btn} bg-gray-800 text-white`}>{t('preview')}</button>
                </div>
                {preview && (
                  <div className="text-sm space-y-2">
                    <div>{t('previewCounts', preview.counts)}</div>
                    {preview.counts.no_longer_parsed + preview.counts.changed > 0 && (
                      <div className="text-amber-700">{t('previewWarning')}</div>
                    )}
                    <div className="max-h-72 overflow-y-auto divide-y divide-gray-200">
                      {preview.examples.map((ex) => (
                        <div key={ex.log_id} className="py-1.5">
                          <div className="text-xs text-gray-500">#{ex.log_id} · {ex.received_at?.slice(0, 16)} · {ex.sender} · <b>{t(`change.${ex.change}`)}</b></div>
                          <div className="font-mono text-xs truncate">{ex.content}</div>
                          <div className="text-xs"><Summary s={ex.before} /> → <Summary s={ex.after} /></div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between">
                <label className="text-sm"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> {t('enableOnSave')}</label>
                <div className="space-x-2">
                  <button onClick={() => { setDraft(null); setEditingId(null); }} className={`${btn} bg-gray-100 text-gray-700`}>{t('cancel')}</button>
                  <button onClick={save} disabled={busy || !draft.pattern || !draft.provider || !draft.name} className={`${btn} bg-blue-600 text-white`}>{t('save')}</button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'unprocessed' && (
        <div className="space-y-3">
          <p className="text-sm text-gray-600">{t('unprocessedIntro')}</p>
          <div className="flex items-center gap-4">
            <button onClick={loadUnprocessed} className={`${btn} bg-gray-100 text-gray-700`}>{t('refresh')}</button>
            <label className="text-sm"><input type="checkbox" checked={allowDupes} onChange={(e) => setAllowDupes(e.target.checked)} /> {t('allowDupes')}</label>
            <button onClick={replay} disabled={busy || selected.size === 0} className={`${btn} bg-blue-600 text-white`}>{t('replaySelected', { count: selected.size })}</button>
          </div>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="px-3 py-2"></th>
                  <th className="px-3 py-2">{t('col.received')}</th>
                  <th className="px-3 py-2">{t('col.sender')}</th>
                  <th className="px-3 py-2">{t('col.message')}</th>
                  <th className="px-3 py-2">{t('col.now')}</th>
                  <th className="px-3 py-2">{t('col.warnings')}</th>
                </tr>
              </thead>
              <tbody>
                {(unprocessed || []).map((r) => {
                  const replayable = r.now.kind === 'payment' && !r.already_credited;
                  return (
                    <tr key={r.log_id} className="border-t border-gray-100 align-top">
                      <td className="px-3 py-2">
                        <input type="checkbox" disabled={!replayable} checked={selected.has(r.log_id)}
                          onChange={(e) => {
                            const next = new Set(selected);
                            if (e.target.checked) next.add(r.log_id); else next.delete(r.log_id);
                            setSelected(next);
                          }} />
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-xs">{r.received_at?.slice(0, 16).replace('T', ' ')}<div className="text-gray-400">{r.outcome}</div></td>
                      <td className="px-3 py-2 text-xs">{r.sender}</td>
                      <td className="px-3 py-2 font-mono text-xs max-w-md break-words">{r.content}</td>
                      <td className="px-3 py-2 text-xs"><Summary s={r.now} />{r.account && <div>→ <b>{r.account}</b></div>}</td>
                      <td className="px-3 py-2 text-xs">
                        {r.already_credited && <div className="text-green-700">{t('alreadyCredited')}</div>}
                        {r.possible_manual_duplicate && (
                          <div className="text-amber-700">{t('possibleDuplicate', {
                            amount: r.possible_manual_duplicate.transaction_amount,
                            date: r.possible_manual_duplicate.transaction_date?.slice(0, 10),
                            source: r.possible_manual_duplicate.source || '?',
                          })}</div>
                        )}
                        {r.now.kind === 'payment' && !r.account && <div className="text-red-600">{t('noAccount')}</div>}
                      </td>
                    </tr>
                  );
                })}
                {unprocessed && unprocessed.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">{t('nothingUnprocessed')}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'settings' && settings && (
        <div className="space-y-6">
          <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
            <h2 className="font-semibold">{t('trustedTitle')}</h2>
            <p className="text-sm text-gray-600">{t('trustedIntro')}</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <textarea className={`${input} font-mono`} rows={6} value={sendersText} onChange={(e) => setSendersText(e.target.value)} placeholder={'MTN MoMo\nMPESA\n199'} />
                <div className="flex items-center gap-2">
                  <select className={`${input} w-auto`} value={settings.trusted_senders_mode}
                    onChange={(e) => setSettings({ ...settings, trusted_senders_mode: e.target.value as SmsFormatSettings['trusted_senders_mode'] })}>
                    <option value="off">{t('mode.off')}</option>
                    <option value="warn">{t('mode.warn')}</option>
                    <option value="enforce">{t('mode.enforce')}</option>
                  </select>
                  <button disabled={busy} className={`${btn} bg-blue-600 text-white`}
                    onClick={() => saveSettings({
                      trusted_senders: sendersText.split('\n').map((s) => s.trim()).filter(Boolean),
                      trusted_senders_mode: settings.trusted_senders_mode,
                    })}>{t('save')}</button>
                </div>
              </div>
              <div>
                <div className="text-sm font-medium mb-1">{t('observedTitle')}</div>
                <div className="max-h-56 overflow-y-auto text-xs divide-y divide-gray-100 border border-gray-100 rounded">
                  {(observed || []).map((o) => (
                    <div key={o.sender} className="flex items-center justify-between px-2 py-1">
                      <span className="font-mono">{o.sender || '—'}</span>
                      <span className="text-gray-500">{t('observedCounts', { parsed: o.parsed, total: o.total })}</span>
                      {o.trusted ? <span className="text-green-700">{t('trusted')}</span> : (
                        <button className="text-blue-700" onClick={() => setSendersText((sendersText ? sendersText + '\n' : '') + o.sender)}>{t('add')}</button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
            <h2 className="font-semibold">{t('balanceTitle')}</h2>
            <p className="text-sm text-gray-600">{t('balanceIntro')}</p>
            <label className="text-sm block"><input type="checkbox" checked={settings.balance_replies_enabled}
              onChange={(e) => setSettings({ ...settings, balance_replies_enabled: e.target.checked })} /> {t('balanceEnabled')}</label>
            <label className="text-sm block">{t('balanceTemplate')}
              <input className={input} value={settings.balance_reply_template} onChange={(e) => setSettings({ ...settings, balance_reply_template: e.target.value })} />
            </label>
            <label className="text-sm block">{t('unregisteredTemplate')}
              <input className={input} value={settings.balance_unregistered_template} onChange={(e) => setSettings({ ...settings, balance_unregistered_template: e.target.value })} />
            </label>
            <button disabled={busy} className={`${btn} bg-blue-600 text-white`}
              onClick={() => saveSettings({
                balance_replies_enabled: settings.balance_replies_enabled,
                balance_reply_template: settings.balance_reply_template,
                balance_unregistered_template: settings.balance_unregistered_template,
              })}>{t('save')}</button>
          </section>
        </div>
      )}
    </div>
  );
}
