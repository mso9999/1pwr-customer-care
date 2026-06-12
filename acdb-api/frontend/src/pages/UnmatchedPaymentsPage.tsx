import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  claimUnmatchedPayment,
  dismissUnmatchedPayment,
  getUnmatchedPayments,
  unmatchedPaymentsExportUrl,
  type MerchantUnmatchedPayment,
} from '../lib/api';

export default function UnmatchedPaymentsPage() {
  const { t } = useTranslation(['unmatchedPayments', 'common']);
  const [rows, setRows] = useState<MerchantUnmatchedPayment[]>([]);
  const [total, setTotal] = useState(0);
  const [openCount, setOpenCount] = useState(0);
  const [openTotal, setOpenTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('open');
  const [categoryFilter, setCategoryFilter] = useState('customer');
  const [search, setSearch] = useState('');
  const [searchDebounced, setSearchDebounced] = useState('');
  const [accountById, setAccountById] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const timer = setTimeout(() => setSearchDebounced(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const load = async () => {
    setLoading(true);
    setMessage('');
    try {
      const res = await getUnmatchedPayments({
        status: statusFilter,
        category: categoryFilter,
        search: searchDebounced || undefined,
      });
      setRows(res.payments);
      setTotal(res.total);
      setOpenCount(res.open_customer_count);
      setOpenTotal(res.open_customer_total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [statusFilter, categoryFilter, searchDebounced]);

  const handleLink = async (row: MerchantUnmatchedPayment) => {
    const account = (accountById[row.id] || '').trim().toUpperCase();
    if (!account) return;
    if (!window.confirm(t('unmatchedPayments:confirmLink', { account }))) return;
    setBusyId(row.id);
    try {
      const res = await claimUnmatchedPayment(row.id, account);
      setMessage(
        res.skipped
          ? t('unmatchedPayments:linkSkipped')
          : t('unmatchedPayments:linkSuccess', { account }),
      );
      await load();
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const handleDismiss = async (row: MerchantUnmatchedPayment) => {
    if (!window.confirm(t('unmatchedPayments:confirmDismiss'))) return;
    setBusyId(row.id);
    const account = (accountById[row.id] || '').trim().toUpperCase() || undefined;
    try {
      await dismissUnmatchedPayment(row.id, account);
      setMessage(t('unmatchedPayments:dismissSuccess'));
      await load();
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const setSuggestedAccount = (rowId: number, account: string) => {
    setAccountById(prev => ({ ...prev, [rowId]: account }));
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-800 mb-2">{t('unmatchedPayments:title')}</h1>
      <p className="text-sm text-gray-600 mb-2">{t('unmatchedPayments:subtitle')}</p>
      <p className="text-sm font-medium text-amber-800 mb-4">
        {t('unmatchedPayments:openSummary', {
          count: openCount,
          total: openTotal.toFixed(2),
        })}
      </p>
      <p className="text-xs text-gray-500 mb-6 max-w-3xl">{t('unmatchedPayments:help')}</p>

      {message && (
        <div className="mb-4 rounded-lg bg-blue-50 text-blue-800 text-sm px-4 py-3">{message}</div>
      )}

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="text-sm border rounded-lg px-3 py-2"
        >
          <option value="open">{t('unmatchedPayments:filters.open')}</option>
          <option value="resolved">{t('unmatchedPayments:filters.resolved')}</option>
          <option value="all">{t('unmatchedPayments:filters.all')}</option>
        </select>
        <select
          value={categoryFilter}
          onChange={e => setCategoryFilter(e.target.value)}
          className="text-sm border rounded-lg px-3 py-2"
        >
          <option value="customer">{t('unmatchedPayments:filters.customer')}</option>
          <option value="treasury">{t('unmatchedPayments:filters.treasury')}</option>
          <option value="all">{t('unmatchedPayments:filters.allCategories')}</option>
        </select>
        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t('unmatchedPayments:filters.searchPlaceholder')}
          className="text-sm border rounded-lg px-3 py-2 min-w-[220px]"
        />
        <span className="text-sm text-gray-500">{t('unmatchedPayments:recordCount', { count: total })}</span>
        <a
          href={unmatchedPaymentsExportUrl(statusFilter, categoryFilter)}
          className="ml-auto px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 no-underline"
          download
        >
          {t('unmatchedPayments:exportCsv')}
        </a>
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="bg-white rounded-xl border overflow-x-auto">
          <table className="w-full text-sm min-w-[960px]">
            <thead>
              <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                <th className="px-4 py-3">{t('unmatchedPayments:colPaid')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colReceipt')}</th>
                <th className="px-4 py-3 text-right">{t('unmatchedPayments:colAmount')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colReference')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colPhone')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colHints')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colStatus')}</th>
                <th className="px-4 py-3">{t('unmatchedPayments:colActions')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const isOpen = !row.resolved_at;
                const hints: string[] = [];
                if (row.reference_accounts?.length) {
                  hints.push(`${t('unmatchedPayments:hintRefs')}: ${row.reference_accounts.join(', ')}`);
                }
                if (row.phone_matches?.length) {
                  hints.push(
                    `${t('unmatchedPayments:hintPhone')}: ${row.phone_matches
                      .map(m => `${m.account_number}${m.name ? ` (${m.name})` : ''}`)
                      .join('; ')}`,
                  );
                }
                if (row.already_booked) {
                  hints.push(t('unmatchedPayments:statusBookedElsewhere'));
                }
                if (!hints.length) hints.push(t('unmatchedPayments:hintNone'));

                return (
                  <tr key={row.id} className="border-t border-gray-100 hover:bg-gray-50 align-top">
                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      {new Date(row.paid_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{row.receipt}</td>
                    <td className="px-4 py-3 text-right font-medium whitespace-nowrap">
                      M {Number(row.amount).toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600 max-w-[280px]">{row.reference_text}</td>
                    <td className="px-4 py-3 font-mono text-xs">{row.payer_phone || '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-600 max-w-[240px]">
                      <ul className="space-y-1">
                        {hints.map((h, i) => (
                          <li key={i}>{h}</li>
                        ))}
                      </ul>
                      {isOpen && row.phone_matches?.length === 1 && (
                        <button
                          type="button"
                          className="mt-1 text-blue-600 hover:underline text-xs"
                          onClick={() => setSuggestedAccount(row.id, row.phone_matches![0].account_number)}
                        >
                          → {row.phone_matches[0].account_number}
                        </button>
                      )}
                      {isOpen && row.existing_reference_accounts?.length === 1 && (
                        <button
                          type="button"
                          className="mt-1 text-blue-600 hover:underline text-xs block"
                          onClick={() => setSuggestedAccount(row.id, row.existing_reference_accounts![0])}
                        >
                          → {row.existing_reference_accounts[0]}
                        </button>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          isOpen ? 'bg-amber-50 text-amber-700' : 'bg-green-50 text-green-700'
                        }`}
                      >
                        {isOpen ? t('unmatchedPayments:statusOpen') : t('unmatchedPayments:statusResolved')}
                      </span>
                      {row.resolved_account && (
                        <div className="mt-1 font-mono text-xs">
                          <Link to={`/customers?search=${row.resolved_account}`} className="text-blue-600">
                            {row.resolved_account}
                          </Link>
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {isOpen && row.category === 'customer' ? (
                        <div className="flex flex-col gap-2 min-w-[160px]">
                          <input
                            type="text"
                            value={accountById[row.id] || ''}
                            onChange={e => setAccountById(prev => ({ ...prev, [row.id]: e.target.value.toUpperCase() }))}
                            placeholder={t('unmatchedPayments:linkAccount')}
                            className="text-xs font-mono border rounded px-2 py-1"
                          />
                          <div className="flex gap-2">
                            <button
                              type="button"
                              disabled={busyId === row.id || !(accountById[row.id] || '').trim()}
                              onClick={() => handleLink(row)}
                              className="px-2 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-50"
                            >
                              {t('unmatchedPayments:link')}
                            </button>
                            <button
                              type="button"
                              disabled={busyId === row.id}
                              onClick={() => handleDismiss(row)}
                              className="px-2 py-1 bg-gray-200 text-gray-800 text-xs rounded hover:bg-gray-300 disabled:opacity-50"
                            >
                              {t('unmatchedPayments:dismiss')}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-center py-8 text-gray-400">
                    {t('unmatchedPayments:empty')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
