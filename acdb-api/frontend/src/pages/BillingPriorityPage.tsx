import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useCountry } from '../contexts/CountryContext';
import {
  getAccountBillingPriority,
  getBillingPrioritySummary,
  getRelayAutoTrigger,
  getSiteBillingHolds,
  setAccountBillingPriority,
  setFleetBillingPriority,
  setRelayAutoTrigger,
  startSiteBillingHold,
  clearSiteBillingHold,
  setMeterElectricityBilling,
  type BillingPriority,
  type BillingPriorityForAccount,
  type BillingPrioritySummary,
  type RelayAutoTrigger,
  type SiteBillingHolds,
} from '../lib/api';

/**
 * Admin UI for the 1Meter billing migration "primacy" toggle.
 *
 * Two controls:
 *   1. Fleet-wide default (superadmin-only) — flips the default for every
 *      account that doesn't have a per-account override. This is the
 *      Phase 1 -> 2 transition.
 *   2. Per-account override — flips a single account (e.g. one MAK check
 *      meter customer). Used during the staged rollout *within* a phase.
 *
 * Backed by `acdb-api/billing_priority.py`. Every change is audited in
 * `cc_mutations`; protocol notes live in
 * `docs/ops/1meter-billing-migration-protocol.md`.
 */

const PRIORITY_LABEL: Record<BillingPriority, string> = {
  sm: 'SparkMeter (SM)',
  '1m': '1Meter (1M)',
};

const PRIORITY_PILL: Record<BillingPriority, string> = {
  sm: 'bg-blue-100 text-blue-800 border-blue-200',
  '1m': 'bg-purple-100 text-purple-800 border-purple-200',
};

function AutoCutoffCard() {
  const { t } = useTranslation('billingPriority');
  const { country } = useCountry();
  const [state, setState] = useState<RelayAutoTrigger | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (country === 'ALL') {
      setState(null);
      setError('');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    setMessage('');
    getRelayAutoTrigger()
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [country]);

  const save = async (enabled: boolean) => {
    const confirmed = window.confirm(enabled ? t('autoCutoff.confirmOn') : t('autoCutoff.confirmOff'));
    if (!confirmed) return;
    setSaving(true);
    setMessage('');
    try {
      const result = await setRelayAutoTrigger(enabled);
      setState(result);
      setMessage(result.status === 'noop' ? t('autoCutoff.noop') : t('autoCutoff.saved'));
    } catch (err) {
      setMessage(t('autoCutoff.failed', { message: (err as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="bg-white rounded-lg shadow border border-gray-200 p-5">
      <h2 className="text-lg font-semibold text-gray-900">{t('autoCutoff.title')}</h2>
      <p className="mt-1 text-sm text-gray-600">{t('autoCutoff.intro')}</p>
      <p className="mt-2 text-sm text-gray-600">{t('autoCutoff.who')}</p>
      <p className="mt-2 text-sm text-gray-600">{t('autoCutoff.firmware')}</p>

      {country === 'ALL' && (
        <p className="mt-4 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
          {t('autoCutoff.pickCountry')}
        </p>
      )}

      {country !== 'ALL' && loading && (
        <p className="mt-4 text-sm text-gray-500">{t('autoCutoff.loading')}</p>
      )}

      {error && (
        <p className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>
      )}

      {state && !loading && (
        <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-gray-900">
              {state.enabled
                ? t('autoCutoff.stateOn', { country: state.country })
                : t('autoCutoff.stateOff', { country: state.country })}
            </p>
            {state.force_off && (
              <p className="mt-1 text-sm text-amber-800">{t('autoCutoff.locked')}</p>
            )}
            {!state.force_off && !state.can_edit && (
              <p className="mt-1 text-sm text-gray-500 italic">{t('autoCutoff.readOnly')}</p>
            )}
          </div>
          {state.can_edit && (
            <button
              type="button"
              disabled={saving}
              onClick={() => save(!state.enabled)}
              className="shrink-0 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {state.enabled ? t('autoCutoff.turnOff') : t('autoCutoff.turnOn')}
            </button>
          )}
        </div>
      )}

      {message && <p className="mt-3 text-sm text-gray-700">{message}</p>}
    </section>
  );
}

function SiteBillingHoldCard() {
  const { t } = useTranslation('billingPriority');
  const { country } = useCountry();
  const [state, setState] = useState<SiteBillingHolds | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState('');
  const [meterId, setMeterId] = useState('');
  const [meterMode, setMeterMode] = useState<'bill' | 'inherit'>('bill');

  const load = () => {
    if (country === 'ALL') {
      setState(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    getSiteBillingHolds()
      .then(setState)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setMessage('');
    setReason('');
    setConfirm('');
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country]);

  const runSite = async (code: string, held: boolean) => {
    setBusy(code);
    setMessage('');
    try {
      if (held) {
        const result = await clearSiteBillingHold(code, confirm.trim(), reason.trim());
        setMessage(t('billingPriority:siteHold.billed', { code, count: result.released_payments, kwh: result.released_kwh }));
      } else {
        await startSiteBillingHold(code, confirm.trim(), reason.trim());
        setMessage(t('billingPriority:siteHold.held', { code }));
      }
      setReason('');
      setConfirm('');
      load();
    } catch (err) {
      setMessage(t('billingPriority:siteHold.failed', { message: (err as Error).message }));
    } finally {
      setBusy('');
    }
  };

  const runMeter = async () => {
    const id = meterId.trim();
    if (!id) return;
    setBusy('meter');
    setMessage('');
    try {
      await setMeterElectricityBilling(id, meterMode, confirm.trim(), reason.trim());
      setMessage(t(meterMode === 'bill' ? 'billingPriority:siteHold.meterBilled' : 'billingPriority:siteHold.meterFollows', { meter: id }));
      setMeterId('');
      setReason('');
      setConfirm('');
      load();
    } catch (err) {
      setMessage(t('billingPriority:siteHold.failed', { message: (err as Error).message }));
    } finally {
      setBusy('');
    }
  };

  if (country === 'ALL') {
    return (
      <section className="bg-white rounded-lg shadow border border-gray-200 p-5">
        <h2 className="text-lg font-semibold text-gray-900">{t('billingPriority:siteHold.title')}</h2>
        <p className="mt-2 text-sm text-gray-600">{t('billingPriority:autoCutoff.pickCountry')}</p>
      </section>
    );
  }

  return (
    <section className="bg-white rounded-lg shadow border border-gray-200 p-5 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">{t('billingPriority:siteHold.title')}</h2>
        <p className="mt-1 text-sm text-gray-600">{t('billingPriority:siteHold.intro')}</p>
      </div>
      {loading && <p className="text-sm text-gray-500">{t('billingPriority:autoCutoff.loading')}</p>}
      {error && <p className="text-sm text-red-700">{error}</p>}
      {state && (
        <div className="space-y-3">
          {state.sites.map((site) => {
            const phrase = site.held ? `BILL ${site.code}` : `HOLD ${site.code}`;
            return (
              <div key={site.code} className="border border-gray-200 rounded-lg p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm font-semibold text-gray-900">{site.code} · {site.name}</p>
                  <p className="text-sm text-gray-700">
                    {site.held
                      ? t('billingPriority:siteHold.stateHeld', { date: (site.started_at || '').slice(0, 10) })
                      : t('billingPriority:siteHold.stateBilling')}
                  </p>
                </div>
                {site.billing_meters.length > 0 && (
                  <p className="mt-1 text-xs text-gray-600">
                    {t('billingPriority:siteHold.metersBilling', {
                      meters: site.billing_meters.map((m) => m.meter_id).join(', '),
                    })}
                  </p>
                )}
                {state.can_edit && (
                  <button
                    type="button"
                    disabled={busy === site.code || reason.trim().length < 8 || confirm.trim().toUpperCase() !== phrase}
                    onClick={() => runSite(site.code, site.held)}
                    className="mt-2 px-3 py-1.5 rounded-md text-sm font-semibold bg-gray-900 text-white disabled:bg-gray-300"
                  >
                    {site.held ? t('billingPriority:siteHold.billSite', { phrase }) : t('billingPriority:siteHold.holdSite', { phrase })}
                  </button>
                )}
              </div>
            );
          })}
          {state.can_edit && (
            <div className="border-t border-gray-200 pt-3 space-y-2">
              <p className="text-sm text-gray-800">{t('billingPriority:siteHold.meterIntro')}</p>
              <div className="flex flex-wrap gap-2">
                <input
                  value={meterId}
                  onChange={(e) => setMeterId(e.target.value)}
                  placeholder={t('billingPriority:siteHold.meterPlaceholder')}
                  className="rounded-md border border-gray-300 px-3 py-2 text-sm"
                />
                <button type="button" onClick={() => setMeterMode('bill')} className={`px-3 py-2 rounded-md text-sm border ${meterMode === 'bill' ? 'bg-gray-900 text-white' : 'bg-white'}`}>
                  {t('billingPriority:siteHold.modeBill')}
                </button>
                <button type="button" onClick={() => setMeterMode('inherit')} className={`px-3 py-2 rounded-md text-sm border ${meterMode === 'inherit' ? 'bg-gray-900 text-white' : 'bg-white'}`}>
                  {t('billingPriority:siteHold.modeFollow')}
                </button>
              </div>
              <button
                type="button"
                disabled={
                  busy === 'meter'
                  || reason.trim().length < 8
                  || confirm.trim().toUpperCase() !== `${meterMode === 'bill' ? 'BILL' : 'FOLLOW'} ${meterId.trim().toUpperCase()}`
                }
                onClick={runMeter}
                className="px-3 py-1.5 rounded-md text-sm font-semibold bg-gray-900 text-white disabled:bg-gray-300"
              >
                {t('billingPriority:siteHold.saveMeter')}
              </button>
            </div>
          )}
          {state.can_edit && (
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-gray-600">
                {t('billingPriority:siteHold.reason')}
                <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm" />
              </label>
              <label className="text-xs text-gray-600">
                {t('billingPriority:siteHold.confirm')}
                <input value={confirm} onChange={(e) => setConfirm(e.target.value)} className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono" autoComplete="off" />
              </label>
            </div>
          )}
          {!state.can_edit && <p className="text-sm text-gray-600">{t('billingPriority:siteHold.readOnly')}</p>}
        </div>
      )}
      {message && <p className="text-sm text-gray-800">{message}</p>}
    </section>
  );
}


function PriorityPill({ value }: { value: BillingPriority }) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase border ${PRIORITY_PILL[value]}`}
    >
      {value}
    </span>
  );
}

export default function BillingPriorityPage() {
  const { isSuperadmin } = useAuth();

  // ── Fleet section state ──────────────────────────────────────────────
  const [summary, setSummary] = useState<BillingPrioritySummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState('');

  const [fleetTarget, setFleetTarget] = useState<BillingPriority>('sm');
  const [fleetNote, setFleetNote] = useState('');
  const [fleetSubmitting, setFleetSubmitting] = useState(false);
  const [fleetMessage, setFleetMessage] = useState('');

  const refreshSummary = () => {
    setSummaryLoading(true);
    setSummaryError('');
    getBillingPrioritySummary()
      .then((s) => {
        setSummary(s);
        setFleetTarget(s.fleet_default);
      })
      .catch((e: Error) => setSummaryError(e.message))
      .finally(() => setSummaryLoading(false));
  };

  useEffect(() => {
    refreshSummary();
  }, []);

  const handleFleetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!summary) return;
    if (fleetTarget === summary.fleet_default) {
      setFleetMessage(`Fleet default is already ${fleetTarget.toUpperCase()}.`);
      return;
    }
    const confirmed = window.confirm(
      `This will switch the fleet-wide billing primacy from ` +
        `${summary.fleet_default.toUpperCase()} to ${fleetTarget.toUpperCase()} for every account ` +
        `that does not have an explicit override. Audited in cc_mutations. Continue?`,
    );
    if (!confirmed) return;

    setFleetSubmitting(true);
    setFleetMessage('');
    try {
      const result = await setFleetBillingPriority(fleetTarget, fleetNote.trim() || undefined);
      if (result.status === 'noop') {
        setFleetMessage(`No change: fleet default is already ${fleetTarget.toUpperCase()}.`);
      } else {
        setFleetMessage(
          `Fleet default changed: ${result.previous_default?.toUpperCase()} → ${result.fleet_default?.toUpperCase()}.`,
        );
        setFleetNote('');
        refreshSummary();
      }
    } catch (err) {
      setFleetMessage(`Failed: ${(err as Error).message}`);
    } finally {
      setFleetSubmitting(false);
    }
  };

  // ── Account section state ────────────────────────────────────────────
  const [accountInput, setAccountInput] = useState('');
  const [accountState, setAccountState] = useState<BillingPriorityForAccount | null>(null);
  const [accountLookupLoading, setAccountLookupLoading] = useState(false);
  const [accountLookupError, setAccountLookupError] = useState('');

  const [acctOverrideTarget, setAcctOverrideTarget] = useState<'sm' | '1m' | 'inherit'>('inherit');
  const [acctNote, setAcctNote] = useState('');
  const [acctSubmitting, setAcctSubmitting] = useState(false);
  const [acctMessage, setAcctMessage] = useState('');

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    const acct = accountInput.trim();
    if (!acct) return;
    setAccountLookupLoading(true);
    setAccountLookupError('');
    setAccountState(null);
    setAcctMessage('');
    try {
      const result = await getAccountBillingPriority(acct);
      setAccountState(result);
      setAcctOverrideTarget(result.override ?? 'inherit');
    } catch (err) {
      setAccountLookupError((err as Error).message);
    } finally {
      setAccountLookupLoading(false);
    }
  };

  const handleAcctSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accountState) return;
    const newOverride: BillingPriority | null =
      acctOverrideTarget === 'inherit' ? null : acctOverrideTarget;
    if (newOverride === accountState.override) {
      setAcctMessage('No change: override is already that value.');
      return;
    }

    const fromLabel = accountState.override ?? 'inherit fleet';
    const toLabel = newOverride ?? 'inherit fleet';
    const confirmed = window.confirm(
      `Change billing primacy override for ${accountState.account_number} ` +
        `from "${fromLabel}" to "${toLabel}"? Audited in cc_mutations.`,
    );
    if (!confirmed) return;

    setAcctSubmitting(true);
    setAcctMessage('');
    try {
      const result = await setAccountBillingPriority(
        accountState.account_number,
        newOverride,
        acctNote.trim() || undefined,
      );
      if (result.status === 'noop') {
        setAcctMessage('No change applied.');
      } else {
        setAcctMessage(
          `Override changed. Effective primacy is now ${result.effective_priority?.toUpperCase()}.`,
        );
        setAcctNote('');
        // Refresh both views.
        const refreshed = await getAccountBillingPriority(accountState.account_number);
        setAccountState(refreshed);
        setAcctOverrideTarget(refreshed.override ?? 'inherit');
        refreshSummary();
      }
    } catch (err) {
      setAcctMessage(`Failed: ${(err as Error).message}`);
    } finally {
      setAcctSubmitting(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Billing-source primacy</h1>
        <p className="mt-1 text-sm text-gray-600">
          Controls which meter source — SparkMeter (SM) or 1Meter (1M) — is authoritative for
          customer kWh balance. Used during the 1Meter migration test (see{' '}
          <a
            href="/docs/ops/1meter-billing-migration-protocol.md"
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            migration protocol
          </a>
          ). Every change is audited in <code className="px-1 bg-gray-100 rounded text-[12px]">cc_mutations</code>.
        </p>
      </div>

      <AutoCutoffCard />

      <SiteBillingHoldCard />

      {/* ────── Fleet default ────── */}
      <section className="bg-white rounded-lg shadow border border-gray-200 p-5">
        <header className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Fleet default</h2>
            <p className="mt-1 text-sm text-gray-600">
              Applies to every account that does not have an explicit per-account override below.
              {!isSuperadmin && (
                <span className="ml-1 text-gray-500 italic">Read-only — superadmin only.</span>
              )}
            </p>
          </div>
          {summary && <PriorityPill value={summary.fleet_default} />}
        </header>

        {summaryError && (
          <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {summaryError}
          </div>
        )}

        {summaryLoading && (
          <div className="text-sm text-gray-500">Loading…</div>
        )}

        {summary && !summaryLoading && (
          <>
            <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-5">
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Current default</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900">
                  {PRIORITY_LABEL[summary.fleet_default]}
                </dd>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Per-account on SM</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900">
                  {summary.per_account_overrides.sm ?? 0}
                </dd>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Per-account on 1M</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900">
                  {summary.per_account_overrides['1m'] ?? 0}
                </dd>
              </div>
            </dl>

            <form
              onSubmit={handleFleetSubmit}
              className="border-t border-gray-200 pt-4 space-y-3"
            >
              <fieldset disabled={!isSuperadmin || fleetSubmitting} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-700 uppercase tracking-wide mb-1">
                    Set fleet default to
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {(['sm', '1m'] as BillingPriority[]).map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => setFleetTarget(p)}
                        className={`px-4 py-2 rounded-md text-sm font-medium border ${
                          fleetTarget === p
                            ? 'bg-blue-600 border-blue-600 text-white'
                            : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        {PRIORITY_LABEL[p]}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 uppercase tracking-wide mb-1">
                    Operator note (optional, max 500 chars)
                  </label>
                  <textarea
                    value={fleetNote}
                    onChange={(e) => setFleetNote(e.target.value)}
                    maxLength={500}
                    rows={2}
                    placeholder="e.g. Phase 1 -> 2 transition; ticket OPS-XYZ"
                    className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 focus:ring-1 focus:ring-blue-400 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
                  />
                </div>

                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    className="px-4 py-2 rounded-md text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
                  >
                    {fleetSubmitting ? 'Applying…' : 'Apply fleet default'}
                  </button>
                  {fleetMessage && (
                    <span className="text-sm text-gray-600">{fleetMessage}</span>
                  )}
                </div>
              </fieldset>
            </form>
          </>
        )}
      </section>

      {/* ────── Per-account override ────── */}
      <section className="bg-white rounded-lg shadow border border-gray-200 p-5">
        <header className="mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Per-account override</h2>
          <p className="mt-1 text-sm text-gray-600">
            Look up an account (e.g. <code className="px-1 bg-gray-100 rounded text-[12px]">0045MAK</code>),
            then set its primacy override or clear it (account inherits the fleet default).
            Used for the check-meter scenario — staged rollout to one customer at a time.
          </p>
        </header>

        <form onSubmit={handleLookup} className="flex flex-wrap items-end gap-2 mb-4">
          <div className="flex-1 min-w-[240px]">
            <label className="block text-xs font-medium text-gray-700 uppercase tracking-wide mb-1">
              Account number
            </label>
            <input
              type="text"
              value={accountInput}
              onChange={(e) => setAccountInput(e.target.value.toUpperCase())}
              placeholder="0045MAK"
              className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 focus:ring-1 focus:ring-blue-400 focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={accountLookupLoading || !accountInput.trim()}
            className="px-4 py-2 rounded-md text-sm font-semibold bg-gray-700 text-white hover:bg-gray-800 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {accountLookupLoading ? 'Looking up…' : 'Look up'}
          </button>
        </form>

        {accountLookupError && (
          <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {accountLookupError}
          </div>
        )}

        {accountState && (
          <>
            <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-5">
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Account</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900">{accountState.account_number}</dd>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Effective primacy</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900 flex items-center gap-2">
                  {PRIORITY_LABEL[accountState.effective_priority]}
                  <PriorityPill value={accountState.effective_priority} />
                </dd>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Override</dt>
                <dd className="mt-1 text-sm font-semibold text-gray-900">
                  {accountState.override
                    ? PRIORITY_LABEL[accountState.override]
                    : <span className="italic text-gray-500">none (inherits fleet)</span>}
                </dd>
              </div>
            </dl>

            <form
              onSubmit={handleAcctSubmit}
              className="border-t border-gray-200 pt-4 space-y-3"
            >
              <fieldset disabled={acctSubmitting} className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-700 uppercase tracking-wide mb-1">
                    Set override to
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {([
                      { val: 'inherit', label: `Inherit fleet (${accountState.fleet_default.toUpperCase()})` },
                      { val: 'sm', label: PRIORITY_LABEL.sm },
                      { val: '1m', label: PRIORITY_LABEL['1m'] },
                    ] as { val: 'inherit' | 'sm' | '1m'; label: string }[]).map((opt) => (
                      <button
                        key={opt.val}
                        type="button"
                        onClick={() => setAcctOverrideTarget(opt.val)}
                        className={`px-4 py-2 rounded-md text-sm font-medium border ${
                          acctOverrideTarget === opt.val
                            ? 'bg-blue-600 border-blue-600 text-white'
                            : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 uppercase tracking-wide mb-1">
                    Operator note (optional)
                  </label>
                  <textarea
                    value={acctNote}
                    onChange={(e) => setAcctNote(e.target.value)}
                    maxLength={500}
                    rows={2}
                    placeholder="e.g. customer 0045MAK opted into 1M primacy for migration test"
                    className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 focus:ring-1 focus:ring-blue-400 focus:outline-none"
                  />
                </div>

                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    className="px-4 py-2 rounded-md text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
                  >
                    {acctSubmitting ? 'Applying…' : 'Apply override'}
                  </button>
                  {acctMessage && (
                    <span className="text-sm text-gray-600">{acctMessage}</span>
                  )}
                </div>
              </fieldset>
            </form>
          </>
        )}
      </section>

      <p className="text-xs text-gray-500">
        Backed by <code className="px-1 bg-gray-100 rounded">PATCH /api/billing-priority</code> and{' '}
        <code className="px-1 bg-gray-100 rounded">PATCH /api/billing-priority/&#123;account&#125;</code>.
        Currently scoped to Lesotho only.
      </p>
    </div>
  );
}
