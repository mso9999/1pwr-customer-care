import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { getOnboardingPipeline, type PipelineStage } from '../lib/api';

const STAGE_LABELS: Record<string, string> = {
  registered: 'Registered',
  connection_fee_paid: 'Connection Fee Paid',
  readyboard_fee_paid: 'Readyboard Fee Paid',
  readyboard_tested: 'Readyboard Tested',
  readyboard_installed: 'Readyboard Installed',
  airdac_connected: 'Airdac Connected',
  meter_installed: 'Meter Installed',
  customer_commissioned: 'Commissioned',
};

const STAGE_I18N_KEYS: Record<string, string> = {
  registered: 'pipeline:stages.registered',
  connection_fee_paid: 'pipeline:stages.connectionFeePaid',
  readyboard_fee_paid: 'pipeline:stages.readyboardFeePaid',
  readyboard_tested: 'pipeline:stages.readyboardTested',
  readyboard_installed: 'pipeline:stages.readyboardInstalled',
  airdac_connected: 'pipeline:stages.airdacConnected',
  meter_installed: 'pipeline:stages.meterInstalled',
  customer_commissioned: 'pipeline:stages.commissioned',
};

const STAGE_COLORS = [
  'bg-gray-400',
  'bg-blue-400',
  'bg-cyan-400',
  'bg-teal-400',
  'bg-green-400',
  'bg-emerald-400',
  'bg-lime-500',
  'bg-green-600',
];

export default function PipelinePage() {
  const { t } = useTranslation(['pipeline', 'common']);
  const [funnel, setFunnel] = useState<PipelineStage[]>([]);
  const [sites, setSites] = useState<string[]>([]);
  const [site, setSite] = useState('');
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const res = await getOnboardingPipeline(site || undefined);
      setFunnel(res.funnel);
      setSites(res.sites);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [site]);

  const maxCount = funnel.length > 0 ? Math.max(...funnel.map(s => s.count), 1) : 1;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-800">{t('pipeline:title')}</h1>
        <select
          value={site}
          onChange={e => setSite(e.target.value)}
          className="text-sm border rounded-lg px-3 py-2"
        >
          <option value="">{t('pipeline:allSites')}</option>
          {sites.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="space-y-6">
          {/* Funnel visualization */}
          <div className="bg-white rounded-xl border p-6">
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">{t('pipeline:commissioningFunnel')}</h2>
            <div className="space-y-3">
              {funnel.map((stage, i) => {
                const pct = (stage.count / maxCount) * 100;
                const dropoff = i > 0 && funnel[i - 1].count > 0
                  ? ((funnel[i - 1].count - stage.count) / funnel[i - 1].count * 100).toFixed(0)
                  : null;

                return (
                  <div key={stage.stage} className="flex items-center gap-4">
                    <div className="w-48 text-right text-sm font-medium text-gray-700 shrink-0">
                      {t(STAGE_I18N_KEYS[stage.stage] ?? stage.stage, { defaultValue: STAGE_LABELS[stage.stage] ?? stage.stage })}
                    </div>
                    <div className="flex-1 relative">
                      <div className="w-full bg-gray-100 rounded-full h-8 overflow-hidden">
                        <div
                          className={`h-8 rounded-full ${STAGE_COLORS[i] ?? 'bg-blue-400'} transition-all duration-500 flex items-center pl-3`}
                          style={{ width: `${Math.max(pct, 2)}%` }}
                        >
                          <span className="text-white text-sm font-bold drop-shadow">{stage.count}</span>
                        </div>
                      </div>
                    </div>
                    <div className="w-16 text-right text-xs text-gray-400 shrink-0">
                      {dropoff !== null && Number(dropoff) > 0 && (
                        <span className="text-red-400">-{dropoff}%</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Summary cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {funnel.length > 0 && (
              <>
                <SummaryCard label={t('pipeline:stats.totalRegistered')} value={funnel[0].count} color="blue" />
                <SummaryCard
                  label={t('pipeline:stats.fullyCommissioned')}
                  value={funnel[funnel.length - 1].count}
                  color="green"
                />
                <SummaryCard
                  label={t('pipeline:stats.conversionRate')}
                  value={`${funnel[0].count > 0 ? ((funnel[funnel.length - 1].count / funnel[0].count) * 100).toFixed(1) : 0}%`}
                  color="amber"
                />
                <SummaryCard
                  label={t('pipeline:stats.inProgress')}
                  value={funnel[0].count - funnel[funnel.length - 1].count}
                  color="purple"
                />
              </>
            )}
          </div>

          {/* Per-stage table */}
          <div className="bg-white rounded-xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-gray-500 text-xs">
                  <th className="px-4 py-3">{t('pipeline:table.stage')}</th>
                  <th className="px-4 py-3 text-right">{t('pipeline:table.count')}</th>
                  <th className="px-4 py-3 text-right">{t('pipeline:table.pctRegistered')}</th>
                  <th className="px-4 py-3 text-right">{t('pipeline:table.dropOff')}</th>
                </tr>
              </thead>
              <tbody>
                {funnel.map((stage, i) => {
                  const regCount = funnel[0]?.count ?? 1;
                  const pctOfReg = regCount > 0 ? ((stage.count / regCount) * 100).toFixed(1) : '—';
                  const prev = i > 0 ? funnel[i - 1].count : stage.count;
                  const dropoff = prev > 0 ? prev - stage.count : 0;
                  return (
                    <tr key={stage.stage} className="border-t border-gray-100">
                      <td className="px-4 py-3 font-medium">{t(STAGE_I18N_KEYS[stage.stage] ?? stage.stage, { defaultValue: STAGE_LABELS[stage.stage] ?? stage.stage })}</td>
                      <td className="px-4 py-3 text-right font-bold">{stage.count}</td>
                      <td className="px-4 py-3 text-right text-gray-600">{pctOfReg}%</td>
                      <td className="px-4 py-3 text-right text-red-500">{dropoff > 0 ? `-${dropoff}` : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  const textColor: Record<string, string> = {
    blue: 'text-blue-700', green: 'text-green-700', amber: 'text-amber-700', purple: 'text-purple-700',
  };
  const ringColor: Record<string, string> = {
    blue: 'ring-blue-100', green: 'ring-green-100', amber: 'ring-amber-100', purple: 'ring-purple-100',
  };
  return (
    <div className={`bg-white rounded-xl border p-4 ring-1 ${ringColor[color] ?? ''}`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${textColor[color] ?? ''}`}>{value}</p>
    </div>
  );
}
