import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  getGensiteSite,
  verifyGensiteCredential,
  type GensiteSiteDetail,
} from '../lib/api';

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  } catch {
    return ts;
  }
}

function num(v: number | null | undefined, digits = 2, unit = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}${unit ? ' ' + unit : ''}`;
}

export default function GenSitePage() {
  const { code = '' } = useParams<{ code: string }>();
  const [params] = useSearchParams();
  const returnTo = params.get('return_to');

  const [detail, setDetail] = useState<GensiteSiteDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyMsg, setVerifyMsg] = useState<string>('');

  async function reload() {
    try {
      setLoading(true);
      const d = await getGensiteSite(code);
      setDetail(d);
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { if (code) reload(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [code]);

  async function runVerify(vendor: string, backend: string) {
    const key = `${vendor}/${backend}`;
    setVerifying(key);
    setVerifyMsg('');
    try {
      const r = await verifyGensiteCredential(code, vendor, backend);
      setVerifyMsg(`${key}: ${r.ok ? '✅' : '❌'} ${r.message}`);
      await reload();
    } catch (e) {
      setVerifyMsg(`${key}: error — ${String(e)}`);
    } finally {
      setVerifying(null);
    }
  }

  if (loading && !detail) return <div className="p-6 text-sm text-gray-500">Loading…</div>;
  if (error) return <div className="p-6 text-sm text-red-600">{error}</div>;
  if (!detail) return null;

  const { site, equipment, credentials, latest_readings } = detail;
  const readingByEq = new Map(latest_readings.map(r => [r.equipment_id, r]));

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {site.country} · {site.kind}
          </div>
          <h1 className="text-2xl font-semibold">{site.display_name} <span className="text-gray-400 font-mono">({site.code})</span></h1>
          <p className="text-sm text-gray-500 mt-1">
            {site.district && <>District: {site.district} · </>}
            {site.commissioned_at && <>Commissioned {formatTs(site.commissioned_at)}</>}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/gensite" className="px-3 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm">All sites</Link>
          {returnTo && (
            <a href={returnTo} className="px-3 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg text-sm">
              ← Back to UGP
            </a>
          )}
        </div>
      </div>

      {verifyMsg && <div className="text-sm bg-gray-50 border rounded-lg px-3 py-2">{verifyMsg}</div>}

      {/* Equipment + live tiles */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Installed equipment</h2>
        {equipment.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-lg p-4 bg-gray-50">
            No equipment registered yet.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {equipment.map(eq => {
              const r = readingByEq.get(eq.id);
              return (
                <div key={eq.id} className="border rounded-xl p-4 bg-white shadow-sm">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="text-xs uppercase text-gray-500">{eq.kind}</div>
                      <div className="font-semibold">{eq.vendor}{eq.model ? ` · ${eq.model}` : ''}</div>
                      <div className="text-xs text-gray-500 font-mono">{eq.serial || '—'}</div>
                    </div>
                    <div className="text-right text-xs text-gray-500">
                      {eq.nameplate_kw && <div>{eq.nameplate_kw} kW</div>}
                      {eq.nameplate_kwh && <div>{eq.nameplate_kwh} kWh</div>}
                      {eq.firmware_version && <div>fw {eq.firmware_version}</div>}
                    </div>
                  </div>

                  <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
                    <Tile label="AC" value={num(r?.ac_kw ?? null, 2, 'kW')} />
                    <Tile label="PV"  value={num(r?.pv_kw ?? null, 2, 'kW')} />
                    <Tile label="SoC" value={num(r?.battery_soc_pct ?? null, 1, '%')} />
                    <Tile label="Batt" value={num(r?.battery_kw ?? null, 2, 'kW')} />
                    <Tile label="Grid" value={num(r?.grid_kw ?? null, 2, 'kW')} />
                    <Tile label="V"    value={num(r?.ac_v_avg ?? null, 1, 'V')} />
                  </div>
                  <div className="mt-2 text-xs text-gray-500">
                    {r ? `Updated ${formatTs(r.ts_utc)}` : 'No reading yet'}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Credentials */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Vendor credentials</h2>
        {credentials.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-lg p-4 bg-gray-50">
            No credentials stored. Commission the site to add them.
          </div>
        ) : (
          <div className="bg-white border rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Vendor</th>
                  <th className="px-4 py-2">Backend</th>
                  <th className="px-4 py-2">User</th>
                  <th className="px-4 py-2">Secret</th>
                  <th className="px-4 py-2">Last verified</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {credentials.map(c => (
                  <tr key={c.id}>
                    <td className="px-4 py-2 font-medium">{c.vendor}</td>
                    <td className="px-4 py-2">{c.backend}</td>
                    <td className="px-4 py-2 font-mono text-xs">{c.username_masked || '—'}</td>
                    <td className="px-4 py-2 text-xs">
                      {c.has_secret ? '🔒 stored' : '—'}
                      {c.has_api_key ? ' · key' : ''}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {c.last_verified_ok === null ? '—'
                        : c.last_verified_ok ? <span className="text-green-700">✅ {formatTs(c.last_verified_at)}</span>
                        : <span className="text-red-700" title={c.last_verify_error || ''}>❌ {formatTs(c.last_verified_at)}</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => runVerify(c.vendor, c.backend)}
                        disabled={verifying === `${c.vendor}/${c.backend}`}
                        className="px-3 py-1 bg-gray-100 hover:bg-gray-200 rounded text-xs disabled:opacity-50"
                      >
                        {verifying === `${c.vendor}/${c.backend}` ? 'Testing…' : 'Test connection'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-gray-400 text-center">
        Telemetry is polled by the gensite poller (systemd); this page reads 1PDB only. Never renders vendor credentials.
      </p>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-lg px-2 py-1.5">
      <div className="text-[10px] uppercase text-gray-500">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
