import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  listUnmeteredService,
  getUnmeteredService,
  enrollUnmeteredService,
  endUnmeteredService,
  type UnmeteredServiceEnrollment,
  type UnmeteredServiceLedgerEntry,
  type UnmeteredServiceListResponse,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

const ADMIN_ROLES = new Set(['superadmin', 'onm_team', 'finance_team']);

function fmtMoney(amount: number | null | undefined, symbol: string): string {
  if (amount == null) return '—';
  return `${symbol}${amount.toFixed(2)}`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

// ---------------------------------------------------------------------------
// Enroll modal
// ---------------------------------------------------------------------------

function EnrollModal({ onClose, onEnrolled }: { onClose: () => void; onEnrolled: () => void }) {
  const { t } = useTranslation(['unmeteredService', 'common']);
  const [form, setForm] = useState({
    account_number: '',
    monthly_fee: '',
    repayment_fraction: '',
    opening_outstanding: '',
    note: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.account_number.trim()) {
      setError(t('unmeteredService:errors.accountRequired'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      await enrollUnmeteredService({
        account_number: form.account_number.trim(),
        monthly_fee: form.monthly_fee ? parseFloat(form.monthly_fee) : undefined,
        repayment_fraction: form.repayment_fraction ? parseFloat(form.repayment_fraction) : undefined,
        opening_outstanding: form.opening_outstanding ? parseFloat(form.opening_outstanding) : undefined,
        note: form.note.trim() || undefined,
      });
      onEnrolled();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 space-y-4">
        <h3 className="text-lg font-bold">{t('unmeteredService:enrollTitle')}</h3>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <label className="block">
          <span className="text-sm text-gray-600">{t('unmeteredService:fields.account')}</span>
          <input
            type="text"
            value={form.account_number}
            onChange={(e) => setForm({ ...form, account_number: e.target.value })}
            className="mt-1 w-full px-3 py-2 border rounded-lg text-sm font-mono"
            placeholder="0068MAK"
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm text-gray-600">{t('unmeteredService:fields.monthlyFee')}</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={form.monthly_fee}
              onChange={(e) => setForm({ ...form, monthly_fee: e.target.value })}
              className="mt-1 w-full px-3 py-2 border rounded-lg text-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm text-gray-600">{t('unmeteredService:fields.repaymentFraction')}</span>
            <input
              type="number"
              min="0"
              max="0.99"
              step="0.05"
              value={form.repayment_fraction}
              onChange={(e) => setForm({ ...form, repayment_fraction: e.target.value })}
              className="mt-1 w-full px-3 py-2 border rounded-lg text-sm"
            />
          </label>
        </div>

        <label className="block">
          <span className="text-sm text-gray-600">{t('unmeteredService:fields.openingOutstanding')}</span>
          <input
            type="number"
            min="0"
            step="0.01"
            value={form.opening_outstanding}
            onChange={(e) => setForm({ ...form, opening_outstanding: e.target.value })}
            className="mt-1 w-full px-3 py-2 border rounded-lg text-sm"
          />
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('unmeteredService:fields.note')}</span>
          <textarea
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
            className="mt-1 w-full px-3 py-2 border rounded-lg text-sm"
            rows={2}
          />
        </label>

        <div className="flex gap-2 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="flex-1 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? t('common:saving') : t('unmeteredService:enrollSubmit')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-200"
          >
            {t('common:cancel')}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail modal (enrollment + ledger)
// ---------------------------------------------------------------------------

function DetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { t } = useTranslation(['unmeteredService', 'common']);
  const [data, setData] = useState<{
    enrollment: UnmeteredServiceEnrollment;
    ledger: UnmeteredServiceLedgerEntry[];
  } | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    getUnmeteredService(id).then(setData).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  const symbol = data?.enrollment.currency === 'LSL' ? 'M' : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl p-6 max-h-[85vh] overflow-y-auto">
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {!data ? (
          <p className="text-gray-400 text-sm">{t('unmeteredService:loading')}</p>
        ) : (
          <>
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-lg font-bold font-mono">{data.enrollment.account_number}</h3>
                <p className="text-sm text-gray-500">
                  {[data.enrollment.first_name, data.enrollment.last_name].filter(Boolean).join(' ')}
                  {data.enrollment.community ? ` · ${data.enrollment.community}` : ''}
                </p>
              </div>
              <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                data.enrollment.status === 'active' ? 'bg-amber-100 text-amber-800' : 'bg-gray-100 text-gray-600'
              }`}>
                {t(`unmeteredService:status.${data.enrollment.status}`)}
              </span>
            </div>

            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 mt-4 text-sm">
              <div className="flex justify-between"><dt className="text-gray-500">{t('unmeteredService:table.monthlyFee')}</dt><dd className="font-medium">{fmtMoney(data.enrollment.monthly_fee, symbol)}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">{t('unmeteredService:table.outstanding')}</dt><dd className="font-semibold">{fmtMoney(data.enrollment.outstanding, symbol)}</dd></div>
              <div className="flex justify-between"><dt className="text-gray-500">{t('unmeteredService:table.started')}</dt><dd>{fmtDate(data.enrollment.started_at)}</dd></div>
              {data.enrollment.ended_at && (
                <div className="flex justify-between"><dt className="text-gray-500">{t('unmeteredService:table.ended')}</dt><dd>{fmtDate(data.enrollment.ended_at)} ({t(`unmeteredService:endReasons.${data.enrollment.end_reason || 'manual'}`)})</dd></div>
              )}
            </dl>

            <h4 className="text-sm font-semibold mt-6 mb-2">{t('unmeteredService:detail.ledgerTitle')}</h4>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b">
                  <th className="py-1.5 pr-2">{t('unmeteredService:detail.date')}</th>
                  <th className="py-1.5 pr-2">{t('unmeteredService:detail.type')}</th>
                  <th className="py-1.5 pr-2">{t('unmeteredService:detail.period')}</th>
                  <th className="py-1.5 pr-2 text-right">{t('unmeteredService:detail.amount')}</th>
                  <th className="py-1.5 pr-2 text-right">{t('unmeteredService:detail.balanceAfter')}</th>
                  <th className="py-1.5">{t('unmeteredService:detail.note')}</th>
                </tr>
              </thead>
              <tbody>
                {data.ledger.map((l) => (
                  <tr key={l.id} className="border-b border-gray-50">
                    <td className="py-1.5 pr-2 text-gray-500">{fmtDate(l.created_at)}</td>
                    <td className="py-1.5 pr-2">{t(`unmeteredService:detail.${l.entry_type}`)}</td>
                    <td className="py-1.5 pr-2 text-gray-500">{l.accrual_period || '—'}</td>
                    <td className="py-1.5 pr-2 text-right">{fmtMoney(l.amount, symbol)}</td>
                    <td className="py-1.5 pr-2 text-right font-medium">{fmtMoney(l.balance_after, symbol)}</td>
                    <td className="py-1.5 text-gray-500 text-xs">{l.note || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <div className="flex justify-end mt-4">
          <button onClick={onClose} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-xl text-sm font-semibold hover:bg-gray-200">
            {t('common:close')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function UnmeteredServicePage() {
  const { t } = useTranslation(['unmeteredService', 'common']);
  const { user } = useAuth();
  const canAdmin = (user?.roles || user?.cc_roles || (user?.role ? [user.role] : [])).some((r) => ADMIN_ROLES.has(r));

  const [data, setData] = useState<UnmeteredServiceListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState<'active' | 'ended' | 'all'>('active');
  const [showEnroll, setShowEnroll] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    listUnmeteredService({ status: statusFilter })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  const handleEnd = async (row: UnmeteredServiceEnrollment) => {
    if (!window.confirm(t('unmeteredService:endPrompt'))) return;
    try {
      await endUnmeteredService(row.id, { reason: 'manual' });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const symbol = data?.currency_symbol || '';

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('unmeteredService:title')}</h1>
          <p className="text-sm text-gray-500 mt-1">{t('unmeteredService:subtitle')}</p>
        </div>
        {canAdmin && (
          <button
            onClick={() => setShowEnroll(true)}
            className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700"
          >
            {t('unmeteredService:enroll')}
          </button>
        )}
      </div>

      {data && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase">{t('unmeteredService:summary.active')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{data.active_count}</p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase">{t('unmeteredService:summary.outstanding')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{fmtMoney(data.total_outstanding, symbol)}</p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase">{t('unmeteredService:summary.monthlyFee')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{fmtMoney(data.monthly_fee_configured, symbol)}</p>
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as 'active' | 'ended' | 'all')}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
        >
          <option value="active">{t('unmeteredService:status.active')}</option>
          <option value="ended">{t('unmeteredService:status.ended')}</option>
          <option value="all">{t('common:all')}</option>
        </select>
      </div>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div>}

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading ? (
          <p className="p-6 text-gray-400 text-sm">{t('unmeteredService:loading')}</p>
        ) : !data || data.enrollments.length === 0 ? (
          <p className="p-6 text-gray-400 text-sm">
            {statusFilter === 'active' ? t('unmeteredService:emptyActive') : t('unmeteredService:emptyList')}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b bg-gray-50">
                  <th className="px-4 py-3">{t('unmeteredService:table.account')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.customer')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.site')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.started')}</th>
                  <th className="px-4 py-3 text-right">{t('unmeteredService:table.monthlyFee')}</th>
                  <th className="px-4 py-3 text-right">{t('unmeteredService:table.outstanding')}</th>
                  <th className="px-4 py-3 text-right">{t('unmeteredService:table.monthsAccrued')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.lastAccrual')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.status')}</th>
                  <th className="px-4 py-3">{t('unmeteredService:table.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {data.enrollments.map((row) => (
                  <tr key={row.id} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono font-medium">{row.account_number}</td>
                    <td className="px-4 py-3">{[row.first_name, row.last_name].filter(Boolean).join(' ') || '—'}</td>
                    <td className="px-4 py-3">{row.community || '—'}</td>
                    <td className="px-4 py-3 text-gray-500">{fmtDate(row.started_at)}</td>
                    <td className="px-4 py-3 text-right">{fmtMoney(row.monthly_fee, symbol)}</td>
                    <td className="px-4 py-3 text-right font-semibold">{fmtMoney(row.outstanding, symbol)}</td>
                    <td className="px-4 py-3 text-right">{row.months_accrued ?? 0}</td>
                    <td className="px-4 py-3 text-gray-500">{row.last_accrual_period || '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                        row.status === 'active' ? 'bg-amber-100 text-amber-800' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {row.status === 'ended' && row.end_reason
                          ? `${t('unmeteredService:status.ended')} · ${t(`unmeteredService:endReasons.${row.end_reason}`)}`
                          : t(`unmeteredService:status.${row.status}`)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        <button
                          onClick={() => setDetailId(row.id)}
                          className="text-blue-600 hover:text-blue-800 text-xs font-semibold"
                        >
                          {t('common:view')}
                        </button>
                        {canAdmin && row.status === 'active' && (
                          <button
                            onClick={() => handleEnd(row)}
                            className="text-red-600 hover:text-red-800 text-xs font-semibold"
                          >
                            {t('unmeteredService:endSubmit')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showEnroll && (
        <EnrollModal
          onClose={() => setShowEnroll(false)}
          onEnrolled={() => { setShowEnroll(false); load(); }}
        />
      )}
      {detailId != null && <DetailModal id={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
