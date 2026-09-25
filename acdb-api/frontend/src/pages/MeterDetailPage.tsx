import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  getMeterDetail,
  getMeterHistory,
  getMeterReadings,
  type MeterAssignment,
  type MeterDetail,
  type MeterReading,
} from '../lib/api';
import { formatLastSeen } from '../lib/datetime';
import FirmwareHistory from '../components/FirmwareHistory';

/** ``sample_time`` is the ingestion wall clock at UTC+2. */
function sampleIso(st: string | null | undefined): string | null {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(st || '');
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - 2 * 3600 * 1000).toISOString();
}

const fmtNum = (v: number | null | undefined, digits = 2) => (v == null ? '—' : v.toFixed(digits));
const fmtDate = (v: string | null | undefined) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-sm text-gray-900">{children}</div>
    </div>
  );
}

export default function MeterDetailPage() {
  const { id = '' } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<MeterDetail | null>(null);
  const [error, setError] = useState('');
  const [assignments, setAssignments] = useState<MeterAssignment[]>([]);
  const [readings, setReadings] = useState<MeterReading[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [now] = useState(() => Date.now());

  useEffect(() => {
    getMeterDetail(id).then(setDetail).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    getMeterHistory(id).then((r) => setAssignments(r.assignments || [])).catch(() => setAssignments([]));
    getMeterReadings(id)
      .then((r) => { setReadings(r.readings); setNextBefore(r.next_before); })
      .catch(() => setReadings([]));
  }, [id]);

  const loadOlder = () => {
    if (!nextBefore) return;
    setLoadingMore(true);
    getMeterReadings(id, nextBefore)
      .then((r) => { setReadings((prev) => [...prev, ...r.readings]); setNextBefore(r.next_before); })
      .finally(() => setLoadingMore(false));
  };

  if (error) {
    return <div className="p-4"><div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div></div>;
  }
  if (!detail) return <div className="p-6 text-gray-400 text-sm">Loading meter…</div>;

  const m = detail.meter;
  const live = detail.live;
  const cust = detail.customer;
  const lastSeen = live?.last_seen || sampleIso(live?.sample_time);
  const online = lastSeen ? now - new Date(lastSeen).getTime() < 24 * 3600 * 1000 : false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link to="/meters" className="text-xs text-blue-600 hover:underline">← Meters</Link>
          <h1 className="text-xl font-semibold text-gray-900 font-mono">Meter {detail.meter_id}</h1>
        </div>
        <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${online ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
          {online ? 'reporting' : lastSeen ? 'offline' : 'never reported'}
        </span>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
          <div className="text-sm font-semibold text-gray-800">Customer</div>
          <Fact label="Customer">
            {cust ? (
              <Link to={`/customers/${cust.customer_id_legacy ?? cust.account_number}`} className="text-blue-600 hover:underline">
                {cust.name || 'Customer'}{cust.customer_id_legacy ? ` · #${cust.customer_id_legacy}` : ''}
              </Link>
            ) : '—'}
          </Fact>
          <Fact label="Account">
            {m?.account_number ? (
              <Link to={`/customer-data?account=${encodeURIComponent(m.account_number)}`} className="text-blue-600 hover:underline font-mono">
                {m.account_number}
              </Link>
            ) : 'not assigned'}
          </Fact>
          <Fact label="Site / village">{[m?.community, m?.village_name].filter(Boolean).join(' · ') || '—'}</Fact>
          <Fact label="Platform / role / status">{[m?.platform, m?.role, m?.status].filter(Boolean).join(' · ') || '—'}</Fact>
          <Fact label="Connected">{fmtDate(m?.customer_connect_date || m?.date_installed)}</Fact>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
          <div className="text-sm font-semibold text-gray-800">Latest reading</div>
          <Fact label="Cumulative energy (meter register)">
            <span className="text-2xl font-semibold">{fmtNum(live?.energy_kwh ?? detail.state?.last_energy_kwh, 4)}</span> kWh
          </Fact>
          <div className="grid grid-cols-2 gap-3">
            <Fact label="Power">{fmtNum(live?.power_w, 1)} W</Fact>
            <Fact label="Voltage">{fmtNum(live?.voltage_v, 1)} V</Fact>
            <Fact label="Current">{fmtNum(live?.current_ma, 0)} mA</Fact>
            <Fact label="Relay">{live?.relay === '1' ? 'on' : live?.relay === '0' ? 'off' : '—'}</Fact>
          </div>
          <Fact label="Last seen">{lastSeen ? formatLastSeen(lastSeen) : '—'}</Fact>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
          <div className="text-sm font-semibold text-gray-800">1Meter gateway</div>
          <Fact label="Gateway">
            <span className="font-mono">{live?.thing_name || detail.gateway?.thing_name || '—'}</span>
          </Fact>
          <Fact label="Pole">{detail.gateway?.pole_id || '—'}</Fact>
          <Fact label="Firmware">{live?.fw_version || detail.state?.firmware_version || '—'}</Fact>
          <Fact label="RS485 (Modbus) address">
            {detail.modbus_id ?? <span className="text-gray-500">not reported (gateway firmware older than 1.1.72)</span>}
          </Fact>
          <FirmwareHistory meterId={detail.meter_id} />
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="text-sm font-semibold text-gray-800 mb-2">Assignment history</div>
        {assignments.length === 0 ? (
          <div className="text-sm text-gray-400">No assignments recorded.</div>
        ) : (
          <ul className="space-y-2 text-sm">
            {assignments.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <Link to={`/customer-data?account=${encodeURIComponent(a.account_number)}`} className="font-mono text-blue-600 hover:underline">{a.account_number}</Link>
                {a.customer_id_legacy != null && (
                  <Link to={`/customers/${a.customer_id_legacy}`} className="text-blue-600 hover:underline">
                    {a.customer_name || 'Customer'} · #{a.customer_id_legacy}
                  </Link>
                )}
                <span className="text-gray-500">{fmtDate(a.assigned_at)} → {a.removed_at ? fmtDate(a.removed_at) : 'now'}</span>
                {a.removal_reason && <span className="text-xs text-gray-500">({a.removal_reason})</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 text-sm font-semibold text-gray-800">Reported readings</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-500">
              <tr>
                <th className="text-left px-3 py-2">Time</th>
                <th className="text-right px-3 py-2">Cumulative kWh</th>
                <th className="text-right px-3 py-2">Power W</th>
                <th className="text-right px-3 py-2">Voltage V</th>
                <th className="text-right px-3 py-2">Current mA</th>
                <th className="text-center px-3 py-2">Relay</th>
                <th className="text-left px-3 py-2">Gateway</th>
                <th className="text-left px-3 py-2">FW</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {readings.map((r, i) => {
                const iso = sampleIso(r.sample_time);
                return (
                  <tr key={`${r.sample_time}-${i}`}>
                    <td className="px-3 py-1.5 whitespace-nowrap">{iso ? formatLastSeen(iso) : r.sample_time}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{fmtNum(r.energy_kwh, 4)}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{fmtNum(r.power_w, 1)}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{fmtNum(r.voltage_v, 1)}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{fmtNum(r.current_ma, 0)}</td>
                    <td className="px-3 py-1.5 text-center">{r.relay === '1' ? 'on' : r.relay === '0' ? 'off' : '—'}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{r.thing_name || '—'}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{r.fw_version || '—'}</td>
                  </tr>
                );
              })}
              {readings.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-400">No 1Meter readings.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {nextBefore && (
          <div className="px-4 py-3 border-t border-gray-100">
            <button onClick={loadOlder} disabled={loadingMore} className="px-3 py-1.5 border rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
              {loadingMore ? 'Loading…' : 'Load older readings'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
