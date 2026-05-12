import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  getOnboardingCustomerStatus,
  patchOnboardingCustomerStatus,
  type OnboardingCustomerStatus,
} from '../lib/api';

const STEP_ORDER = [
  'connection_fee_paid',
  'readyboard_fee_paid',
  'readyboard_tested',
  'readyboard_installed',
  'airdac_connected',
  'meter_installed',
  'customer_commissioned',
] as const;

const STEP_I18N_KEYS: Record<string, string> = {
  connection_fee_paid: 'pipeline:stages.connectionFeePaid',
  readyboard_fee_paid: 'pipeline:stages.readyboardFeePaid',
  readyboard_tested: 'pipeline:stages.readyboardTested',
  readyboard_installed: 'pipeline:stages.readyboardInstalled',
  airdac_connected: 'pipeline:stages.airdacConnected',
  meter_installed: 'pipeline:stages.meterInstalled',
  customer_commissioned: 'pipeline:stages.commissioned',
};

export default function OnboardingStepsPanel({
  accountNumber,
  canWrite,
}: {
  accountNumber: string;
  canWrite: boolean;
}) {
  const { t } = useTranslation(['pipeline', 'common']);
  const [status, setStatus] = useState<OnboardingCustomerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getOnboardingCustomerStatus(accountNumber);
      setStatus(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load onboarding status');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (accountNumber) load();
  }, [accountNumber]);

  const updateStep = async (step: string, value: boolean, date?: string | null) => {
    if (!canWrite) return;
    setSaving(true);
    setError('');
    try {
      const data = await patchOnboardingCustomerStatus(accountNumber, {
        steps: [{ step, value, date: date ?? null }],
      });
      setStatus(data);
    } catch (err: any) {
      setError(err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  if (!accountNumber) return null;

  return (
    <div className="bg-white rounded-lg shadow p-4 sm:p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
          Onboarding
        </h2>
        {status?.onboarding_import_tag && (
          <span className="text-xs text-gray-400">{status.onboarding_import_tag}</span>
        )}
      </div>
      {loading && <p className="text-sm text-gray-400">{t('common:loading')}...</p>}
      {error && <p className="text-sm text-red-600 mb-2">{error}</p>}
      {status && (
        <div className="space-y-2">
          {STEP_ORDER.map(step => {
            const row = status.steps[step];
            const dateValue = row?.date ? String(row.date).slice(0, 10) : '';
            return (
              <div key={step} className="flex flex-wrap items-center gap-3 border-t border-gray-100 pt-2">
                <label className="flex items-center gap-2 min-w-[12rem] text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={!!row?.value}
                    disabled={!canWrite || saving}
                    onChange={e => updateStep(step, e.target.checked, dateValue || undefined)}
                  />
                  {t(STEP_I18N_KEYS[step] ?? step, { defaultValue: step })}
                </label>
                <input
                  type="date"
                  value={dateValue}
                  disabled={!canWrite || saving || !row?.value}
                  onChange={e => updateStep(step, true, e.target.value)}
                  className="text-sm border rounded px-2 py-1"
                />
              </div>
            );
          })}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-3 border-t text-sm text-gray-600">
            <div>Survey / plot: {status.survey_id || '—'}</div>
            <div>Meter serial: {status.meter_serial || '—'}</div>
          </div>
        </div>
      )}
    </div>
  );
}
