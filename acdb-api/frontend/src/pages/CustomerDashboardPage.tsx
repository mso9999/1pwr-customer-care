import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart, Bar, LineChart, Line, Legend,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { getMyDashboard, getMyProfile, type CustomerDashboard, type MeterInfo, type HourlyPoint } from '../lib/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format seconds as DD:HH:MM */
function formatCountdown(totalSeconds: number): string {
  if (totalSeconds <= 0) return '00:00:00';
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  return `${String(days).padStart(2, '0')}:${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

/** Format a date string nicely */
function fmtDate(d: string | null): string {
  if (!d) return '--';
  try {
    return new Date(d + 'T00:00:00').toLocaleDateString('en-ZA', {
      day: 'numeric', month: 'short', year: 'numeric',
    });
  } catch {
    return d;
  }
}

/** Short day label from YYYY-MM-DD */
function shortDay(d: string): string {
  try {
    return new Date(d + 'T00:00:00').toLocaleDateString('en-ZA', { weekday: 'short' });
  } catch {
    return d.slice(8);
  }
}

/** Short day + date label for 30-day chart */
function shortDayDate(d: string): string {
  try {
    const dt = new Date(d + 'T00:00:00');
    return dt.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' });
  } catch {
    return d.slice(5);
  }
}

/** Month label from YYYY-MM */
function shortMonth(m: string): string {
  try {
    const [y, mo] = m.split('-');
    return new Date(Number(y), Number(mo) - 1).toLocaleDateString('en-ZA', {
      month: 'short', year: '2-digit',
    });
  } catch {
    return m;
  }
}

// ---------------------------------------------------------------------------
// Smart Meter Display
// ---------------------------------------------------------------------------

function SmartMeterFace({ kwh }: { kwh: number }) {
  // Seven-segment digital display style
  const display = kwh.toFixed(1);

  return (
    <div className="relative mx-auto w-full max-w-sm">
      {/* Meter housing */}
      <div className="bg-gradient-to-b from-gray-800 to-gray-900 rounded-2xl p-1 shadow-2xl">
        {/* Inner bezel */}
        <div className="bg-gradient-to-b from-gray-700 to-gray-800 rounded-xl p-4">
          {/* Brand label */}
          <div className="text-center mb-2">
            <span className="text-[10px] tracking-[0.3em] uppercase text-gray-400 font-medium">
              1PWR Smart Meter
            </span>
          </div>

          {/* LCD screen */}
          <div className="bg-gradient-to-b from-[#1a2e1a] to-[#0f1f0f] rounded-lg p-4 border border-gray-600 shadow-inner">
            {/* Units remaining label */}
            <div className="text-center mb-1">
              <span className="text-[10px] uppercase tracking-wider text-green-700 font-medium">
                Units Remaining
              </span>
            </div>

            {/* Digital readout */}
            <div className="text-center py-2">
              <span
                className="font-mono text-5xl sm:text-6xl font-bold tracking-wider"
                style={{
                  color: '#39ff14',
                  textShadow: '0 0 10px #39ff14, 0 0 20px #39ff1466, 0 0 40px #39ff1433',
                }}
              >
                {display}
              </span>
            </div>

            {/* Unit label */}
            <div className="text-center mt-1">
              <span
                className="text-lg font-mono font-semibold tracking-widest"
                style={{ color: '#39ff14aa' }}
              >
                kWh
              </span>
            </div>
          </div>

          {/* Status LED row */}
          <div className="flex justify-between items-center mt-3 px-2">
            <div className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_4px_#22c55e]" />
              <span className="text-[9px] text-gray-400 uppercase">Active</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full bg-blue-400 shadow-[0_0_4px_#60a5fa]" />
              <span className="text-[9px] text-gray-400 uppercase">Connected</span>
            </div>
            <div className="flex items-center gap-1.5">
              {kwh < 10 ? (
                <div className="w-2 h-2 rounded-full bg-red-500 shadow-[0_0_4px_#ef4444] animate-pulse" />
              ) : (
                <div className="w-2 h-2 rounded-full bg-gray-600" />
              )}
              <span className="text-[9px] text-gray-400 uppercase">Low</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat Cards
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub, warn }: {
  label: string; value: string; sub?: string; warn?: boolean;
}) {
  return (
    <div className={`rounded-xl p-4 shadow-sm border ${warn ? 'bg-amber-50 border-amber-200' : 'bg-white border-gray-100'}`}>
      <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</dt>
      <dd className={`text-2xl font-bold mt-1 ${warn ? 'text-amber-700' : 'text-gray-900'}`}>
        {value}
      </dd>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart wrappers
// ---------------------------------------------------------------------------

const CHART_BLUE = '#3b82f6';
const CHART_GREEN = '#10b981';
const CHART_AMBER = '#f59e0b';
const CHART_PURPLE = '#8b5cf6';


function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Meter Info
// ---------------------------------------------------------------------------

const PLATFORM_LABELS: Record<string, string> = {
  sparkmeter: 'SparkMeter',
  prototype: '1Meter Prototype',
  legacy: 'Legacy',
};
const ROLE_BADGES: Record<string, { label: string; color: string }> = {
  primary: { label: 'Primary', color: 'bg-green-100 text-green-700' },
  check:   { label: 'Check', color: 'bg-purple-100 text-purple-700' },
  backup:  { label: 'Backup', color: 'bg-gray-100 text-gray-600' },
};

function MeterInfoCard({ meters }: { meters: MeterInfo[] }) {
  if (!meters.length) return null;
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Meters on This Account
      </h3>
      <div className="space-y-2">
        {meters.map((m) => {
          const badge = ROLE_BADGES[m.role] ?? ROLE_BADGES.primary;
          return (
            <div
              key={m.meter_id}
              className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2"
            >
              <div>
                <span className="font-mono text-sm font-medium text-gray-800">
                  {m.meter_id}
                </span>
                <span className="ml-2 text-xs text-gray-400">
                  {PLATFORM_LABELS[m.platform] ?? m.platform}
                </span>
              </div>
              <span className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full ${badge.color}`}>
                {badge.label}
              </span>
            </div>
          );
        })}
      </div>
      {meters.some((m) => m.role === 'check') && (
        <p className="text-[11px] text-gray-400 mt-2">
          Check meters are installed for verification alongside the primary meter.
          Dashboard totals use only the primary meter.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function CustomerDashboardPage() {
  const [data, setData] = useState<CustomerDashboard | null>(null);
  const [acct, setAcct] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([getMyDashboard(), getMyProfile()])
      .then(([dash, prof]) => {
        setData(dash);
        const c = prof.customer;
        setAcct(String(c.account_number || c.customer_id || ''));
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <p className="text-red-500 text-lg">{error}</p>
        <p className="text-gray-400 text-sm mt-2">Unable to load your dashboard. Please try again later.</p>
      </div>
    );
  }

  if (!data) return null;

  const countdown = formatCountdown(data.estimated_recharge_seconds);
  const lowBalance = data.balance_kwh < 10;

  return (
    <div className="max-w-2xl mx-auto space-y-6 pb-8">
      {/* Greeting */}
      <div className="text-center pt-2">
        <h1 className="text-lg font-semibold text-gray-700">Welcome back</h1>
        {acct && (
          <Link
            to="/my/profile"
            className="inline-block mt-1 text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline"
          >
            Account {acct}
          </Link>
        )}
      </div>

      {/* Smart Meter Face */}
      <SmartMeterFace kwh={data.balance_kwh} />

      {/* Last Payment */}
      {data.last_payment ? (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Last Payment</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">
            {data.currency_code || 'LSL'} {data.last_payment.amount.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}
          </p>
          <p className="text-sm text-gray-500">
            received {fmtDate(data.last_payment.date)}
            {data.last_payment.kwh_purchased > 0 && (
              <span className="text-green-600 ml-1">
                (+{data.last_payment.kwh_purchased.toFixed(1)} kWh)
              </span>
            )}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 text-center">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Last Payment</p>
          <p className="text-gray-400 mt-1">No payment records found</p>
        </div>
      )}

      {/* Meter Info */}
      {data.meters && data.meters.length > 0 && (
        <MeterInfoCard meters={data.meters} />
      )}

      {/* Key Stats Grid */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          label="Avg. Daily Usage"
          value={`${data.avg_kwh_per_day.toFixed(1)} kWh`}
          sub="past 30 days"
        />
        <StatCard
          label="Est. Time to Recharge"
          value={countdown}
          sub="DD:HH:MM"
          warn={lowBalance}
        />
        <StatCard
          label="Total Consumption"
          value={`${data.total_kwh_all_time.toLocaleString('en-ZA', { maximumFractionDigits: 0 })} kWh`}
          sub="all time"
        />
        <StatCard
          label="Total Purchases"
          value={`${data.currency_code || 'LSL'} ${data.total_lsl_all_time.toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          sub="all time"
        />
      </div>

      {/* 24-Hour Bar Chart */}
      {data.hourly_24h && data.hourly_24h.length > 0 && (() => {
        const pts: HourlyPoint[] = data.hourly_24h;
        const sources = Object.keys(pts[0] || {}).filter(k => k !== 'hour' && k !== 'kwh');
        const isMulti = sources.length > 1;
        const srcColors: Record<string, string> = { 'SparkMeter': CHART_BLUE, '1Meter Prototype': CHART_AMBER };
        return (
          <ChartCard title={isMulti ? 'Meter Comparison — Last 24 Hours (kWh / hour)' : 'Last 24 Hours (kWh / hour)'}>
            {isMulti && (
              <p className="text-xs text-gray-400 -mt-2 mb-3">Both meters measure the same load. Close agreement confirms accuracy.</p>
            )}
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={pts} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                <XAxis
                  dataKey="hour"
                  tickFormatter={v => v.slice(11, 16)}
                  tick={{ fontSize: 10, fill: '#9ca3af' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: '#9ca3af' }}
                  axisLine={false}
                  tickLine={false}
                  width={40}
                />
                <Tooltip
                  labelFormatter={(label: any) => String(label).slice(5)}
                  formatter={(v: any, name: any) => [`${Number(v).toFixed(3)} kWh`, name]}
                  contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 }}
                />
                {isMulti ? (
                  <>
                    <Legend />
                    {sources.map(src => (
                      <Bar key={src} dataKey={src} fill={srcColors[src] ?? '#6b7280'} radius={[3, 3, 0, 0]} maxBarSize={20} />
                    ))}
                  </>
                ) : (
                  <Bar dataKey={sources[0] ?? 'kwh'} fill={CHART_BLUE} radius={[4, 4, 0, 0]} maxBarSize={36} />
                )}
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        );
      })()}

      {/* 7-Day Bar Chart */}
      <ChartCard title="Last 7 Days (kWh / day)">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={data.daily_7d} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
            <XAxis
              dataKey="date"
              tickFormatter={shortDay}
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip
              formatter={(v: any) => [`${Number(v).toFixed(2)} kWh`, 'Consumption']}
              labelFormatter={(label: any) => fmtDate(String(label))}
              contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 }}
            />
            <Bar dataKey="kwh" fill={CHART_BLUE} radius={[4, 4, 0, 0]} maxBarSize={36} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* Meter Comparison (shown when check meter is present) */}
      {data.meter_comparison && data.meter_comparison.length > 0 && (
        <ChartCard title="Meter Comparison — Last 7 Days (kWh / day)">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data.meter_comparison} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
              <XAxis
                dataKey="date"
                tickFormatter={shortDay}
                tick={{ fontSize: 11, fill: '#9ca3af' }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#9ca3af' }}
                axisLine={false}
                tickLine={false}
                width={40}
              />
              <Tooltip
                formatter={(v: any, name: any) => [`${Number(v).toFixed(3)} kWh`, name]}
                labelFormatter={(label: any) => fmtDate(String(label))}
                contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 }}
              />
              <Legend
                wrapperStyle={{ fontSize: 11 }}
                iconType="circle"
                iconSize={8}
              />
              <Bar dataKey="SparkMeter" fill={CHART_BLUE} radius={[4, 4, 0, 0]} maxBarSize={28} />
              <Bar dataKey="1Meter Prototype" fill={CHART_PURPLE} radius={[4, 4, 0, 0]} maxBarSize={28} />
            </BarChart>
          </ResponsiveContainer>
          <p className="text-[11px] text-gray-400 mt-2 text-center">
            Both meters measure the same load. Close agreement confirms accuracy.
          </p>
        </ChartCard>
      )}

      {/* 30-Day Line Chart */}
      <ChartCard title="Last 30 Days (kWh / day)">
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data.daily_30d} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
            <XAxis
              dataKey="date"
              tickFormatter={shortDayDate}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
              interval={4}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip
              formatter={(v: any) => [`${Number(v).toFixed(2)} kWh`, 'Consumption']}
              labelFormatter={(label: any) => fmtDate(String(label))}
              contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 }}
            />
            <Line
              type="monotone"
              dataKey="kwh"
              stroke={CHART_GREEN}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: CHART_GREEN }}
            />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 12-Month Bar Chart */}
      <ChartCard title="Last 12 Months (kWh / month)">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data.monthly_12m} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
            <XAxis
              dataKey="month"
              tickFormatter={shortMonth}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#9ca3af' }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <Tooltip
              formatter={(v: any) => [`${Number(v).toFixed(1)} kWh`, 'Consumption']}
              labelFormatter={(label: any) => shortMonth(String(label))}
              contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: 12 }}
            />
            <Bar dataKey="kwh" fill={CHART_AMBER} radius={[4, 4, 0, 0]} maxBarSize={32} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}
