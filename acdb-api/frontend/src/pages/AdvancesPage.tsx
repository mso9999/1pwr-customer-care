import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  listAdvances,
  getAdvance,
  createAdvance,
  patchAdvance,
  replaceAdvanceContract,
  writeoffAdvance,
  getContractCreditAvailable,
  convertContractCreditToElectricity,
  refundContractCredit,
  openAdvanceContract,
  type Advance,
  type AdvanceStatus,
  type AdvanceType,
} from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

const ADMIN_ROLES = new Set(['superadmin', 'onm_team', 'finance_team']);

function formatCurrency(amount: number, currency: string): string {
  if (amount == null) return '—';
  return `${amount.toFixed(2)} ${currency}`;
}

function formatPct(pct: number): string {
  return `${(pct * 100).toFixed(2)} %`;
}

function shortHash(h: string | undefined | null): string {
  if (!h) return '';
  return h.slice(0, 8);
}

function isAdvanceAdmin(role: string | undefined | null): boolean {
  return !!role && ADMIN_ROLES.has(role);
}

// ---------------------------------------------------------------------------
// Create-advance modal (with required contract upload)
// ---------------------------------------------------------------------------

function CreateAdvanceModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { t } = useTranslation(['advances', 'common']);
  const [form, setForm] = useState({
    account_number: '',
    advance_type: 'connection' as AdvanceType,
    original_amount: 0,
    monthly_fee_pct: 0.015,
    repayment_fraction: 0.5,
    note: '',
  });
  const [contract, setContract] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!contract) {
      setError(t('advances:errors.contractRequired'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      await createAdvance({
        ...form,
        note: form.note.trim() || undefined,
        contract,
      });
      onCreated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <h3 className="text-lg font-bold">{t('advances:createTitle')}</h3>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.account')}</span>
          <input
            type="text"
            required
            placeholder="0001MAK"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
            value={form.account_number}
            onChange={(e) => setForm({ ...form, account_number: e.target.value.toUpperCase() })}
          />
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.type')}</span>
          <select
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
            value={form.advance_type}
            onChange={(e) => setForm({ ...form, advance_type: e.target.value as AdvanceType })}
          >
            <option value="connection">{t('advances:types.connection')}</option>
            <option value="readyboard">{t('advances:types.readyboard')}</option>
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm text-gray-600">{t('advances:fields.amount')}</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              required
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
              value={form.original_amount}
              onChange={(e) => setForm({ ...form, original_amount: Number(e.target.value) })}
            />
          </label>
          <label className="block">
            <span className="text-sm text-gray-600">{t('advances:fields.repaymentFraction')}</span>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              required
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
              value={form.repayment_fraction}
              onChange={(e) => setForm({ ...form, repayment_fraction: Number(e.target.value) })}
            />
          </label>
        </div>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.monthlyFeePct')}</span>
          <input
            type="number"
            step="0.0001"
            min="0"
            max="0.5"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
            value={form.monthly_fee_pct}
            onChange={(e) => setForm({ ...form, monthly_fee_pct: Number(e.target.value) })}
          />
          <span className="text-xs text-gray-500 mt-1 block">{t('advances:fields.monthlyFeePctHint')}</span>
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.note')}</span>
          <textarea
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
            rows={2}
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.contract')} <span className="text-red-500">*</span></span>
          <input
            ref={fileInputRef}
            type="file"
            required
            accept="application/pdf,image/png,image/jpeg"
            className="mt-1 block w-full text-sm file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
            onChange={(e) => setContract(e.target.files?.[0] ?? null)}
          />
          {contract && (
            <p className="text-xs text-gray-500 mt-1">
              {contract.name} — {(contract.size / 1024).toFixed(1)} KB
            </p>
          )}
          <p className="text-xs text-gray-500 mt-1">{t('advances:fields.contractHint')}</p>
        </label>

        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
            {t('common:cancel', 'Cancel')}
          </button>
          <button
            type="submit"
            disabled={saving || !contract}
            className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? t('advances:saving') : t('advances:createSubmit')}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit-fee modal
// ---------------------------------------------------------------------------

function EditFeeModal({ advance, onClose, onSaved }: { advance: Advance; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation(['advances', 'common']);
  const [feePct, setFeePct] = useState(advance.monthly_fee_pct);
  const [fraction, setFraction] = useState(advance.repayment_fraction);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await patchAdvance(advance.id, {
        monthly_fee_pct: feePct,
        repayment_fraction: fraction,
        note: note.trim() || undefined,
      });
      onSaved();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
        <h3 className="text-lg font-bold">{t('advances:editTitle', { id: advance.id })}</h3>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <div className="text-sm text-gray-500">
          {advance.account_number} — {advance.advance_type} — {formatCurrency(advance.outstanding, advance.currency)}
        </div>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.monthlyFeePct')}</span>
          <input
            type="number"
            step="0.0001"
            min="0"
            max="0.5"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={feePct}
            onChange={(e) => setFeePct(Number(e.target.value))}
          />
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.repaymentFraction')}</span>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={fraction}
            onChange={(e) => setFraction(Number(e.target.value))}
          />
        </label>

        <label className="block">
          <span className="text-sm text-gray-600">{t('advances:fields.note')}</span>
          <input
            type="text"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>

        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600">{t('common:cancel', 'Cancel')}</button>
          <button type="submit" disabled={saving} className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {saving ? t('advances:saving') : t('advances:save')}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Replace-contract modal
// ---------------------------------------------------------------------------

function ReplaceContractModal({ advance, onClose, onSaved }: { advance: Advance; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation(['advances', 'common']);
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError(t('advances:errors.contractRequired'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      await replaceAdvanceContract(advance.id, file);
      onSaved();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
        <h3 className="text-lg font-bold">{t('advances:replaceContractTitle', { id: advance.id })}</h3>
        <p className="text-sm text-gray-500">
          {t('advances:replaceContractHint', { filename: advance.contract_filename })}
        </p>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <input
          type="file"
          required
          accept="application/pdf,image/png,image/jpeg"
          className="block w-full text-sm file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />

        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600">{t('common:cancel', 'Cancel')}</button>
          <button type="submit" disabled={saving || !file} className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {saving ? t('advances:saving') : t('advances:replaceSubmit')}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ledger detail panel
// ---------------------------------------------------------------------------

function LedgerModal({ advance, onClose }: { advance: Advance; onClose: () => void }) {
  const { t } = useTranslation(['advances']);
  const [detail, setDetail] = useState<Advance | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAdvance(advance.id)
      .then((d) => setDetail(d))
      .finally(() => setLoading(false));
  }, [advance.id]);

  const ledger = detail?.ledger ?? [];

  const colors: Record<string, string> = {
    grant: 'text-blue-700 bg-blue-50',
    repayment: 'text-green-700 bg-green-50',
    monthly_fee: 'text-amber-700 bg-amber-50',
    adjustment: 'text-gray-700 bg-gray-100',
    writeoff: 'text-red-700 bg-red-50',
    contract_replaced: 'text-purple-700 bg-purple-50',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-3xl p-6 max-h-[85vh] overflow-y-auto">
        <div className="flex justify-between items-start mb-4">
          <div>
            <h3 className="text-lg font-bold">{t('advances:ledgerTitle', { id: advance.id })}</h3>
            <p className="text-sm text-gray-500">
              {advance.account_number} — {advance.advance_type} — {formatCurrency(advance.outstanding, advance.currency)} {t('advances:outstanding')}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>

        {loading ? (
          <p className="text-sm text-gray-500">{t('advances:loading')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-gray-500 uppercase tracking-wide border-b">
              <tr>
                <th className="text-left py-2">{t('advances:ledger.date')}</th>
                <th className="text-left py-2">{t('advances:ledger.type')}</th>
                <th className="text-right py-2">{t('advances:ledger.amount')}</th>
                <th className="text-right py-2">{t('advances:ledger.balanceAfter')}</th>
                <th className="text-left py-2">{t('advances:ledger.note')}</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry) => (
                <tr key={entry.id} className="border-b last:border-b-0">
                  <td className="py-2 text-gray-600 whitespace-nowrap">{new Date(entry.created_at).toLocaleString()}</td>
                  <td className="py-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[entry.entry_type] || 'bg-gray-100 text-gray-700'}`}>
                      {entry.entry_type.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="py-2 text-right tabular-nums">{formatCurrency(entry.amount, advance.currency)}</td>
                  <td className="py-2 text-right tabular-nums">{formatCurrency(entry.balance_after, advance.currency)}</td>
                  <td className="py-2 text-gray-600">{entry.note ?? '—'}</td>
                </tr>
              ))}
              {ledger.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-gray-400">{t('advances:ledger.empty')}</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AdvancesPage() {
  const { t } = useTranslation(['advances', 'common']);
  const { user } = useAuth();
  const canAdmin = isAdvanceAdmin(user?.role as string | undefined);

  const [rows, setRows] = useState<Advance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState<AdvanceStatus | ''>('active');
  const [typeFilter, setTypeFilter] = useState<AdvanceType | ''>('');
  const [accountFilter, setAccountFilter] = useState('');

  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Advance | null>(null);
  const [replacing, setReplacing] = useState<Advance | null>(null);
  const [viewing, setViewing] = useState<Advance | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await listAdvances({
        status: statusFilter || undefined,
        advance_type: typeFilter || undefined,
        account_number: accountFilter.trim() || undefined,
      });
      setRows(res.advances);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, typeFilter]);

  const totalOutstanding = useMemo(() => {
    return rows.reduce((sum, r) => sum + (r.status === 'active' ? r.outstanding : 0), 0);
  }, [rows]);

  const currency = rows[0]?.currency ?? '';

  const handleWriteoff = async (advance: Advance) => {
    const note = window.prompt(t('advances:writeoffPrompt'));
    if (note === null) return;
    try {
      await writeoffAdvance(advance.id, note);
      fetchData();
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : String(err));
    }
  };

  const handleConvertContractCredit = async () => {
    const account = window.prompt(t('advances:convert.promptAccount'));
    if (!account) return;
    const normalizedAccount = account.toUpperCase().trim();
    try {
      const available = await getContractCreditAvailable(normalizedAccount);
      if (available.total_available <= 0) {
        window.alert(t('advances:convert.noneAvailable', { account: normalizedAccount }));
        return;
      }
      window.alert(
        t('advances:convert.availableHint', {
          amount: available.total_available.toFixed(2),
          account: normalizedAccount,
        }),
      );
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : String(err));
      return;
    }

    const amountRaw = window.prompt(t('advances:convert.promptAmount'));
    if (!amountRaw) return;
    const amount = Number(amountRaw);
    if (!Number.isFinite(amount) || amount <= 0) {
      window.alert(t('advances:convert.invalidAmount'));
      return;
    }
    const note = window.prompt(t('advances:convert.promptNote')) || undefined;
    try {
      const res = await convertContractCreditToElectricity({
        account_number: normalizedAccount,
        amount,
        note,
      });
      window.alert(
        t('advances:convert.success', {
          account: res.account_number,
          amount: res.converted_amount.toFixed(2),
          kwh: res.converted_kwh.toFixed(4),
        }),
      );
      fetchData();
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : String(err));
    }
  };

  const handleRefundContractCredit = async () => {
    const account = window.prompt(t('advances:refund.promptAccount'));
    if (!account) return;
    const normalizedAccount = account.toUpperCase().trim();
    try {
      const available = await getContractCreditAvailable(normalizedAccount);
      if (available.total_available <= 0) {
        window.alert(t('advances:refund.noneAvailable', { account: normalizedAccount }));
        return;
      }
      window.alert(
        t('advances:refund.availableHint', {
          amount: available.total_available.toFixed(2),
          account: normalizedAccount,
        }),
      );
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : String(err));
      return;
    }
    const amountRaw = window.prompt(t('advances:refund.promptAmount'));
    if (!amountRaw) return;
    const amount = Number(amountRaw);
    if (!Number.isFinite(amount) || amount <= 0) {
      window.alert(t('advances:refund.invalidAmount'));
      return;
    }
    const note = window.prompt(t('advances:refund.promptNote')) || undefined;
    try {
      const res = await refundContractCredit({
        account_number: normalizedAccount,
        amount,
        note,
      });
      window.alert(
        t('advances:refund.success', {
          account: res.account_number,
          amount: res.refunded_amount.toFixed(2),
        }),
      );
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">{t('advances:title')}</h2>
        {canAdmin && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleConvertContractCredit}
              className="px-4 py-2 bg-amber-600 text-white text-sm rounded-lg hover:bg-amber-700"
            >
              {t('advances:convert.button')}
            </button>
            <button
              onClick={handleRefundContractCredit}
              className="px-4 py-2 bg-rose-600 text-white text-sm rounded-lg hover:bg-rose-700"
            >
              {t('advances:refund.button')}
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700"
            >
              {t('advances:newAdvance')}
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3 bg-white border border-gray-200 rounded-xl p-3">
        <label className="block">
          <span className="text-xs text-gray-500 block">{t('advances:filters.status')}</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as AdvanceStatus | '')}
            className="text-sm border rounded-lg px-3 py-2"
          >
            <option value="">{t('advances:filters.all')}</option>
            <option value="active">{t('advances:status.active')}</option>
            <option value="paid_off">{t('advances:status.paid_off')}</option>
            <option value="written_off">{t('advances:status.written_off')}</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-gray-500 block">{t('advances:filters.type')}</span>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as AdvanceType | '')}
            className="text-sm border rounded-lg px-3 py-2"
          >
            <option value="">{t('advances:filters.all')}</option>
            <option value="connection">{t('advances:types.connection')}</option>
            <option value="readyboard">{t('advances:types.readyboard')}</option>
          </select>
        </label>
        <label className="block flex-1 min-w-[200px]">
          <span className="text-xs text-gray-500 block">{t('advances:filters.account')}</span>
          <div className="flex gap-2">
            <input
              type="text"
              value={accountFilter}
              onChange={(e) => setAccountFilter(e.target.value.toUpperCase())}
              placeholder="0001MAK"
              className="text-sm border rounded-lg px-3 py-2 w-full"
              onKeyDown={(e) => { if (e.key === 'Enter') fetchData(); }}
            />
            <button
              type="button"
              onClick={fetchData}
              className="px-3 py-2 text-sm bg-gray-100 hover:bg-gray-200 rounded-lg"
            >
              {t('advances:filters.search')}
            </button>
          </div>
        </label>
        <div className="ml-auto text-sm text-gray-600">
          {t('advances:totals.activeOutstanding')}:{' '}
          <span className="font-semibold tabular-nums">{formatCurrency(totalOutstanding, currency)}</span>
        </div>
      </div>

      {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3">{error}</div>}

      <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 uppercase tracking-wide bg-gray-50">
            <tr>
              <th className="text-left px-4 py-2">{t('advances:cols.account')}</th>
              <th className="text-left px-4 py-2">{t('advances:cols.customer')}</th>
              <th className="text-left px-4 py-2">{t('advances:cols.type')}</th>
              <th className="text-right px-4 py-2">{t('advances:cols.original')}</th>
              <th className="text-right px-4 py-2">{t('advances:cols.outstanding')}</th>
              <th className="text-right px-4 py-2">{t('advances:cols.feePct')}</th>
              <th className="text-right px-4 py-2">{t('advances:cols.repayPct')}</th>
              <th className="text-left px-4 py-2">{t('advances:cols.status')}</th>
              <th className="text-left px-4 py-2">{t('advances:cols.contract')}</th>
              <th className="text-left px-4 py-2">{t('advances:cols.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={10} className="px-4 py-6 text-center text-gray-400">{t('advances:loading')}</td></tr>
            )}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={10} className="px-4 py-6 text-center text-gray-400">{t('advances:emptyList')}</td></tr>
            )}
            {!loading && rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-gray-50">
                <td className="px-4 py-2 font-mono text-xs">{r.account_number}</td>
                <td className="px-4 py-2">{[r.first_name, r.last_name].filter(Boolean).join(' ') || '—'}</td>
                <td className="px-4 py-2">{t(`advances:types.${r.advance_type}`)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatCurrency(r.original_amount, r.currency)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatCurrency(r.outstanding, r.currency)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatPct(r.monthly_fee_pct)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatPct(r.repayment_fraction)}</td>
                <td className="px-4 py-2">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    r.status === 'active' ? 'bg-blue-50 text-blue-700' :
                    r.status === 'paid_off' ? 'bg-green-50 text-green-700' :
                    'bg-red-50 text-red-700'
                  }`}>
                    {t(`advances:status.${r.status}`)}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <button
                    onClick={() => openAdvanceContract(r.id)}
                    className="text-blue-600 hover:underline text-xs"
                    title={`sha256:${shortHash(r.contract_sha256)}`}
                  >
                    {r.contract_filename}
                  </button>
                </td>
                <td className="px-4 py-2 text-xs space-x-2 whitespace-nowrap">
                  <button onClick={() => setViewing(r)} className="text-blue-600 hover:underline">
                    {t('advances:actions.ledger')}
                  </button>
                  {canAdmin && r.status === 'active' && (
                    <>
                      <button onClick={() => setEditing(r)} className="text-blue-600 hover:underline">
                        {t('advances:actions.edit')}
                      </button>
                      <button onClick={() => setReplacing(r)} className="text-blue-600 hover:underline">
                        {t('advances:actions.replaceContract')}
                      </button>
                      <button onClick={() => handleWriteoff(r)} className="text-red-600 hover:underline">
                        {t('advances:actions.writeoff')}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateAdvanceModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); fetchData(); }}
        />
      )}
      {editing && (
        <EditFeeModal
          advance={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); fetchData(); }}
        />
      )}
      {replacing && (
        <ReplaceContractModal
          advance={replacing}
          onClose={() => setReplacing(null)}
          onSaved={() => { setReplacing(null); fetchData(); }}
        />
      )}
      {viewing && (
        <LedgerModal advance={viewing} onClose={() => setViewing(null)} />
      )}
    </div>
  );
}
