import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useCountry } from '../contexts/CountryContext';
import {
  getCountryFees,
  getFeeThresholdExempt,
  getSmsInboundLog,
  getSmsPhoneMeters,
  setFeeThresholdExempt,
  type CountryFees,
  type SmsInboundRow,
} from '../lib/api';

const FAILED = new Set(['error', 'no_account', 'untrusted_sender', 'contract_unallocated']);
const PROCESSED = new Set([
  'electricity', 'fee', 'contract_fee_advance', 'duplicate', 'replayed',
]);
const FEE_ADMIN = new Set(['superadmin', 'onm_team', 'finance_team']);

function bucket(row: SmsInboundRow): 'processed' | 'pending' | 'failed' {
  const outcome = row.outcome || '';
  if (FAILED.has(outcome) || (!outcome && row.parsed_ok === false)) return 'failed';
  if (PROCESSED.has(outcome)) return 'processed';
  return 'pending';
}

function csvCell(value: unknown): string {
  const text = value == null ? '' : String(value);
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export default function SmsInboxPage() {
  const { t } = useTranslation('smsInbox');
  const { country } = useCountry();
  const { user, isSuperadmin } = useAuth();
  const roles = user?.roles || user?.cc_roles || (user?.role ? [user.role] : []);
  const isEditor = isSuperadmin || roles.some((role) => role === 'superadmin' || role === 'onm_team');
  const isFeeAdmin = roles.some((role) => FEE_ADMIN.has(role));

  const [rows, setRows] = useState<SmsInboundRow[]>([]);
  const [fees, setFees] = useState<CountryFees | null>(null);
  const [account, setAccount] = useState('');
  const [outcome, setOutcome] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [phone, setPhone] = useState('');
  const [phoneAccounts, setPhoneAccounts] = useState<string[] | null>(null);
  const [exemptAccount, setExemptAccount] = useState('');
  const [exempt, setExempt] = useState<boolean | null>(null);
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    if (country === 'ALL') return;
    setLoading(true);
    setError('');
    try {
      const [log, countryFees] = await Promise.all([
        getSmsInboundLog({
          account: account.trim() || undefined,
          outcome: outcome || undefined,
          limit: 200,
        }),
        getCountryFees(),
      ]);
      setRows(log.rows || []);
      setFees(countryFees);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [account, country, outcome]);

  useEffect(() => { load(); }, [load]);

  const download = () => {
    const cols: (keyof SmsInboundRow)[] = [
      'id', 'received_at', 'sender', 'account_number', 'amount', 'receipt_key', 'outcome', 'content', 'error',
    ];
    const lines = [cols.join(',')].concat(rows.map((row) => cols.map((col) => csvCell(row[col])).join(',')));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sms-inbox-${country}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const lookupPhone = async () => {
    setPhoneAccounts(null);
    setError('');
    try {
      const res = await getSmsPhoneMeters(phone.trim());
      setPhoneAccounts(res.accounts || []);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const loadExempt = async () => {
    setExempt(null);
    setNotice('');
    try {
      const res = await getFeeThresholdExempt(exemptAccount.trim());
      setExempt(res.exempt);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const saveExempt = async (next: boolean) => {
    setNotice('');
    try {
      const res = await setFeeThresholdExempt(exemptAccount.trim(), next);
      setExempt(res.exempt);
      setNotice(t('exemptSaved'));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (country === 'ALL') {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <h1 className="text-xl font-semibold text-gray-800">{t('title')}</h1>
        <p className="mt-3 text-sm text-gray-600">{t('pickCountry')}</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-800">{t('title')}</h1>
          <p className="text-sm text-gray-500 mt-1">{t('subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <button onClick={download} disabled={!rows.length} className="px-3 py-2 text-sm rounded-lg border border-gray-300 disabled:opacity-40">{t('exportCsv')}</button>
          <button onClick={load} className="px-3 py-2 text-sm rounded-lg bg-blue-600 text-white">{t('refresh')}</button>
        </div>
      </div>

      <div className="bg-white border rounded-xl p-4 text-sm text-gray-600 space-y-1">
        <div className="font-medium text-gray-800">{t('threshold')}</div>
        {fees?.connection_fee_threshold == null
          ? <p>{t('thresholdUnset')}</p>
          : <p>{t('thresholdSet', { amount: fees.connection_fee_threshold.toFixed(2), currency: fees.currency })}</p>}
        <p>{t('lowBalanceOff')} <Link className="text-blue-700 underline" to="/tariffs">{t('editOnTariffs')}</Link></p>
      </div>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>}
      {notice && <div className="p-3 bg-green-50 border border-green-200 rounded-xl text-green-700 text-sm">{notice}</div>}

      <div className="flex flex-wrap gap-2">
        <input value={account} onChange={(e) => setAccount(e.target.value)} placeholder={t('account')} className="px-3 py-2 border rounded-lg text-sm" />
        <input value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder={t('allOutcomes')} className="px-3 py-2 border rounded-lg text-sm" />
      </div>

      <div className="bg-white border rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-gray-500">
              <th className="px-3 py-2">{t('received')}</th>
              <th className="px-3 py-2">{t('sender')}</th>
              <th className="px-3 py-2">{t('account')}</th>
              <th className="px-3 py-2">{t('amount')}</th>
              <th className="px-3 py-2">{t('content')}</th>
              <th className="px-3 py-2">{t('status')}</th>
              <th className="px-3 py-2">{t('action')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={7} className="px-3 py-6 text-gray-400">{t('loading')}</td></tr>}
            {!loading && rows.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-gray-400">{t('empty')}</td></tr>}
            {rows.map((row) => {
              const state = bucket(row);
              const label = state === 'processed' ? t('processed') : state === 'failed' ? t('failed') : t('pending');
              return (
                <tr key={row.id} className="border-t align-top">
                  <td className="px-3 py-2 whitespace-nowrap text-xs text-gray-500">{row.received_at || ''}</td>
                  <td className="px-3 py-2 font-mono text-xs">{row.sender || ''}</td>
                  <td className="px-3 py-2 font-mono text-xs">{row.account_number || '—'}</td>
                  <td className="px-3 py-2 tabular-nums">{row.amount ?? '—'}</td>
                  <td className="px-3 py-2 max-w-md text-xs text-gray-700">{row.content}</td>
                  <td className="px-3 py-2">{label}{row.outcome ? <span className="block text-xs text-gray-400">{row.outcome}</span> : null}</td>
                  <td className="px-3 py-2">
                    {state === 'failed' && (
                      isEditor
                        ? <Link className="text-blue-700 underline text-xs" to="/admin/sms-formats?tab=unprocessed">{t('openUnprocessed')}</Link>
                        : <Link className="text-blue-700 underline text-xs" to="/record-payment">{t('recordPayment')}</Link>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="bg-white border rounded-xl p-4 space-y-3">
        <h2 className="text-sm font-semibold text-gray-700">{t('phoneLookup')}</h2>
        <div className="flex gap-2">
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder={t('phonePlaceholder')} className="px-3 py-2 border rounded-lg text-sm" />
          <button onClick={lookupPhone} className="px-3 py-2 text-sm rounded-lg border">{t('lookup')}</button>
        </div>
        {phoneAccounts && (
          <p className="text-sm text-gray-600">
            {phoneAccounts.length === 0 ? t('phoneNone') : `${t('phoneAccounts')}: ${phoneAccounts.join(', ')}`}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-2 border-t">
          <input value={exemptAccount} onChange={(e) => setExemptAccount(e.target.value)} placeholder={t('account')} className="px-3 py-2 border rounded-lg text-sm" />
          <button onClick={loadExempt} className="px-3 py-2 text-sm rounded-lg border">{t('lookup')}</button>
          {exempt !== null && (
            <label className="text-sm text-gray-700 flex items-center gap-2">
              <input
                type="checkbox"
                checked={exempt}
                disabled={!isFeeAdmin}
                onChange={(e) => saveExempt(e.target.checked)}
              />
              {t('exempt')}
            </label>
          )}
        </div>
        <p className="text-xs text-gray-400">{t('exemptHelp')}</p>
      </div>
    </div>
  );
}
