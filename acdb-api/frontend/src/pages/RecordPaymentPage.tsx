import { useState } from 'react';
import { recordManualPayment, type RecordPaymentResult } from '../lib/api';

export default function RecordPaymentPage() {
  const [form, setForm] = useState({ account_number: '', amount: '', meter_id: '', note: '' });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<RecordPaymentResult | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.account_number || !form.amount) { setError('Account number and amount are required'); return; }
    setSubmitting(true);
    setError('');
    setResult(null);
    try {
      const res = await recordManualPayment({
        account_number: form.account_number.trim(),
        amount: Number(form.amount),
        meter_id: form.meter_id.trim() || undefined,
        note: form.note.trim() || undefined,
      });
      setResult(res);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const onesDigit = form.amount ? Math.floor(Number(form.amount)) % 10 : null;
  const isDedicated = onesDigit === 1 || onesDigit === 9;

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-800 mb-6">Record Payment</h1>

      <form onSubmit={handleSubmit} className="bg-white rounded-xl border p-6 space-y-4">
        {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3">{error}</div>}

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Account Number</span>
          <input
            type="text"
            value={form.account_number}
            onChange={e => setForm({ ...form, account_number: e.target.value })}
            placeholder="e.g. 0045MAK"
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Amount (M)</span>
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={form.amount}
            onChange={e => setForm({ ...form, amount: e.target.value })}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
          />
          {form.amount && isDedicated && (
            <p className="text-xs text-amber-600 mt-1">
              Amount ends in {onesDigit} — will be treated as a dedicated debt payment if the customer has active financing.
            </p>
          )}
        </label>

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Meter ID <span className="text-gray-400">(optional)</span></span>
          <input
            type="text"
            value={form.meter_id}
            onChange={e => setForm({ ...form, meter_id: e.target.value })}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-gray-700">Note <span className="text-gray-400">(optional)</span></span>
          <textarea
            value={form.note}
            onChange={e => setForm({ ...form, note: e.target.value })}
            rows={2}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-400 focus:outline-none"
          />
        </label>

        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition"
        >
          {submitting ? 'Recording...' : 'Record Payment'}
        </button>
      </form>

      {result && (
        <div className="mt-6 bg-green-50 border border-green-200 rounded-xl p-5">
          <h3 className="font-bold text-green-800 mb-3">Payment Recorded</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span className="text-gray-500">Transaction ID</span>
              <p className="font-medium">#{result.transaction_id}</p>
            </div>
            <div>
              <span className="text-gray-500">Amount</span>
              <p className="font-medium">M {result.amount?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-500">kWh Vended</span>
              <p className="font-medium">{result.kwh?.toFixed(4)} kWh</p>
            </div>
            <div>
              <span className="text-gray-500">New Balance</span>
              <p className="font-medium">{result.balance_kwh?.toFixed(4)} kWh</p>
            </div>
            {result.financing && (
              <>
                <div>
                  <span className="text-gray-500">Electricity Portion</span>
                  <p className="font-medium text-blue-700">M {result.financing.electricity_portion?.toFixed(2)}</p>
                </div>
                <div>
                  <span className="text-gray-500">Debt Portion</span>
                  <p className="font-medium text-amber-700">M {result.financing.debt_portion?.toFixed(2)}</p>
                </div>
              </>
            )}
          </div>
          {result.sm_credit && (
            <div className="mt-3 text-xs text-gray-500">
              SM Credit: {result.sm_credit.success ? 'OK' : 'Failed'} ({result.sm_credit.platform})
            </div>
          )}
        </div>
      )}
    </div>
  );
}
