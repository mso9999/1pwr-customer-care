import { useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts';
import { getCheckMeterComparison } from '../lib/api';
import type { CheckMeterComparisonResponse, CheckMeterPair } from '../lib/api';

const PAIR_COLORS = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed',
  '#0891b2', '#be185d', '#65a30d',
];

function formatHour(iso: string): string {
  try {
    const d = new Date(iso);
    const mon = d.toLocaleString('en', { month: 'short' });
    const day = d.getDate();
    const hh = d.getHours().toString().padStart(2, '0');
    return `${mon} ${day} ${hh}:00`;
  } catch {
    return iso;
  }
}

function sign(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}` : n.toFixed(1);
}

function StatCard({ pair, color }: { pair: CheckMeterPair; color: string }) {
  const s = pair.stats;
  const totalDev = s.total_deviation_pct ?? ((s.total_1m_kwh - s.total_sm_kwh) / (s.total_sm_kwh || 1) * 100);
  const absTotal = Math.abs(totalDev);
  const qualityColor = absTotal < 5 ? '#16a34a' : absTotal < 15 ? '#d97706' : '#dc2626';
  return (
    <div
      className="rounded-xl border-2 bg-white shadow-sm p-4 min-w-[240px] flex-1"
      style={{ borderColor: color }}
    >
      <div className="flex items-center gap-2 mb-2">
        <span
          className="inline-block w-3 h-3 rounded-full shrink-0"
          style={{ backgroundColor: color }}
        />
        <span className="font-bold text-gray-800 text-sm">{pair.account}</span>
      </div>

      <div className="flex items-baseline gap-1.5 mb-3">
        <span className="text-2xl font-bold" style={{ color: qualityColor }}>
          {sign(totalDev)}%
        </span>
        <span className="text-xs text-gray-400">total deviation</span>
      </div>

      <div className="space-y-1 text-sm">
        <div className="flex justify-between">
          <span className="text-gray-500">Total SM</span>
          <span className="font-medium text-gray-700">{s.total_sm_kwh} kWh</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Total 1M</span>
          <span className="font-medium text-gray-700">{s.total_1m_kwh} kWh</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Mean SM / hr</span>
          <span className="font-medium text-gray-700">{s.mean_sm_kwh.toFixed(3)} kWh</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Mean 1M / hr</span>
          <span className="font-medium text-gray-700">{s.mean_1m_kwh.toFixed(3)} kWh</span>
        </div>
        <hr className="border-gray-100" />
        <div className="flex justify-between text-xs text-gray-400">
          <span>Hourly dev (mean ± sd)</span>
          <span>{sign(s.mean_deviation_pct)}% ± {s.stddev_deviation_pct.toFixed(0)}%</span>
        </div>
        <div className="flex justify-between text-xs text-gray-400">
          <span>Matched hours</span>
          <span>{s.n_matched_hours}</span>
        </div>
      </div>
    </div>
  );
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
  label?: string;
  pairs: CheckMeterPair[];
}

function CustomTooltip({ active, payload, label, pairs }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-xs max-w-xs">
      <p className="font-semibold text-gray-700 mb-1.5">{formatHour(label ?? '')}</p>
      {pairs.map((pair, i) => {
        const smEntry = payload.find((p: any) => p.dataKey === `${pair.account}_sm`);
        const m1Entry = payload.find((p: any) => p.dataKey === `${pair.account}_1m`);
        const smVal = smEntry?.value as number | null | undefined;
        const m1Val = m1Entry?.value as number | null | undefined;
        const color = PAIR_COLORS[i % PAIR_COLORS.length];
        return (
          <div key={pair.account} className="mb-1">
            <div className="flex items-center gap-1.5 font-medium" style={{ color }}>
              <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
              {pair.account}
            </div>
            <div className="pl-3.5 text-gray-600">
              SM: {smVal != null ? `${smVal.toFixed(3)} kWh` : '—'}
              {' · '}
              1M: {m1Val != null ? `${m1Val.toFixed(3)} kWh` : '—'}
              {smVal != null && m1Val != null && smVal > 0 && (
                <span className="ml-1 text-gray-400">
                  ({sign(((m1Val - smVal) / smVal) * 100)}%)
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function CheckMeterPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [data, setData] = useState<CheckMeterComparisonResponse | null>(null);
  const [days, setDays] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const resp = await getCheckMeterComparison(days);
        if (!cancelled) setData(resp);
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [days]);

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Check Meter Comparison</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            SparkMeter (primary) vs 1Meter (check) — hourly kWh readings
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600">Period:</label>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="text-sm border border-gray-200 rounded-md px-2.5 py-1.5 bg-white focus:ring-1 focus:ring-blue-400 focus:outline-none"
          >
            <option value={0}>Since firmware update</option>
            <option value={1}>Last 24 hours</option>
            <option value={3}>Last 3 days</option>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        </div>
      </div>

      {/* Loading / Error */}
      {loading && (
        <div className="flex items-center justify-center py-24">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600" />
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 mb-6">
          {error}
        </div>
      )}

      {!loading && !error && data && (
        <>
          {data.pairs.length === 0 ? (
            <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 rounded-lg p-4">
              No check meter pairs found. Ensure meters with role "check" exist alongside "primary" meters on the same accounts.
            </div>
          ) : (
            <>
              {/* Chart */}
              <div className="bg-white rounded-xl shadow-md border border-gray-100 p-4 sm:p-6 mb-6">
                <div className="mb-3">
                  <h2 className="text-base font-bold text-gray-800">
                    Hourly Consumption — SM (solid) vs 1M (dashed)
                  </h2>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {data.time_series.length} data points · Local time (SAST)
                  </p>
                </div>

                <div className="flex gap-3 flex-wrap mb-4">
                  {data.pairs.map((pair, i) => (
                    <div key={pair.account} className="flex items-center gap-2 text-xs text-gray-600">
                      <span className="inline-block w-3 h-3 rounded-full" style={{ backgroundColor: PAIR_COLORS[i % PAIR_COLORS.length] }} />
                      <span className="font-medium">{pair.account}</span>
                      <span className="text-gray-400">
                        SM {pair.primary_meter_id} / 1M {pair.check_meter_id}
                      </span>
                    </div>
                  ))}
                </div>

                <ResponsiveContainer width="100%" height={420}>
                  <LineChart
                    data={data.time_series}
                    margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="reading_hour"
                      tickFormatter={formatHour}
                      tick={{ fontSize: 10 }}
                      interval="preserveStartEnd"
                      minTickGap={60}
                    />
                    <YAxis
                      tick={{ fontSize: 11 }}
                      label={{
                        value: 'kWh',
                        angle: -90,
                        position: 'insideLeft',
                        style: { fontSize: 11, fill: '#9ca3af' },
                      }}
                    />
                    <Tooltip
                      content={<CustomTooltip pairs={data.pairs} />}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: 11 }}
                      formatter={(value: string) => {
                        const parts = value.split('_');
                        const acct = parts.slice(0, -1).join('_');
                        const type = parts[parts.length - 1] === 'sm' ? 'SM' : '1M';
                        return `${acct} ${type}`;
                      }}
                    />
                    {data.pairs.map((pair, i) => {
                      const color = PAIR_COLORS[i % PAIR_COLORS.length];
                      return [
                        <Line
                          key={`${pair.account}_sm`}
                          type="monotone"
                          dataKey={`${pair.account}_sm`}
                          stroke={color}
                          strokeWidth={2}
                          dot={false}
                          connectNulls
                          name={`${pair.account}_sm`}
                        />,
                        <Line
                          key={`${pair.account}_1m`}
                          type="monotone"
                          dataKey={`${pair.account}_1m`}
                          stroke={color}
                          strokeWidth={2}
                          strokeDasharray="6 3"
                          dot={false}
                          connectNulls
                          name={`${pair.account}_1m`}
                        />,
                      ];
                    })}
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Stat Cards */}
              <div className="flex flex-wrap gap-4">
                {data.pairs.map((pair, i) => (
                  <StatCard
                    key={pair.account}
                    pair={pair}
                    color={PAIR_COLORS[i % PAIR_COLORS.length]}
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
