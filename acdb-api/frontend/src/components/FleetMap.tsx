import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  getFirmwareHistory,
  getFleetMap,
  type FirmwareHistoryEntry,
  type FleetMapMeter,
  type FleetMapResult,
} from '../lib/api';
import { formatLastSeen } from '../lib/datetime';

type MapColorMode = 'status' | 'firmware' | 'installed' | 'hybrid';

const FW_PALETTE = [
  '#2563eb', '#7c3aed', '#0891b2', '#ca8a04', '#db2777',
  '#0f766e', '#c2410c', '#4f46e5', '#65a30d', '#9333ea',
];
const FW_UNKNOWN = '#9ca3af';
const STATUS_ONLINE = '#16a34a';
const STATUS_OFFLINE = '#ef4444';
const INSTALL_UNKNOWN = '#9ca3af';

function installEpoch(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})/.exec(raw.trim());
  if (dateOnly) return Date.UTC(+dateOnly[1], +dateOnly[2] - 1, +dateOnly[3]);
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d.getTime();
}

/** Oldest installs are cool blue, newest are hot red. */
function heatColor(t: number): string {
  const stops = [
    { t: 0, r: 191, g: 219, b: 254 },
    { t: 0.55, r: 251, g: 191, b: 36 },
    { t: 1, r: 185, g: 28, b: 28 },
  ];
  const x = Math.min(1, Math.max(0, t));
  const upper = stops.find((s) => s.t >= x) || stops[stops.length - 1];
  const lower = [...stops].reverse().find((s) => s.t <= x) || stops[0];
  const span = upper.t - lower.t || 1;
  const f = (x - lower.t) / span;
  const ch = (a: number, b: number) => Math.round(a + (b - a) * f);
  return `rgb(${ch(lower.r, upper.r)}, ${ch(lower.g, upper.g)}, ${ch(lower.b, upper.b)})`;
}

function formatInstalled(raw: string | null | undefined): string {
  if (!raw) return '—';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})/.exec(raw.trim());
  if (!dateOnly) return formatLastSeen(raw);
  const d = new Date(Date.UTC(+dateOnly[1], +dateOnly[2] - 1, +dateOnly[3]));
  return d.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

const METHOD_LABEL: Record<FirmwareHistoryEntry['method'], { text: string; cls: string }> = {
  ota: { text: 'OTA', cls: 'bg-blue-50 text-blue-700' },
  serial: { text: 'serial', cls: 'bg-amber-50 text-amber-800' },
  initial: { text: 'first seen', cls: 'bg-gray-100 text-gray-600' },
  unknown: { text: 'OTA status unknown', cls: 'bg-gray-100 text-gray-600' },
};

/** Collapsed firmware timeline from the meter's readings; loads on first open. */
function FirmwareHistory({ meterId }: { meterId: string }) {
  const [rows, setRows] = useState<FirmwareHistoryEntry[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const onToggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!e.currentTarget.open || rows || loading) return;
    setLoading(true);
    setError('');
    getFirmwareHistory(meterId)
      .then((r) => setRows(r.history))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  };

  return (
    <details className="mt-1.5 text-xs" onToggle={onToggle}>
      <summary className="cursor-pointer text-gray-500 hover:text-gray-700">Firmware history</summary>
      {loading && <div className="mt-1 text-gray-400">Loading…</div>}
      {error && <div className="mt-1 text-red-600">{error}</div>}
      {rows && !rows.length && <div className="mt-1 text-gray-400">No readings yet.</div>}
      {rows && rows.length > 0 && (
        <ul className="mt-1 space-y-1 max-h-48 overflow-y-auto pr-1">
          {rows.map((h) => {
            const label = METHOD_LABEL[h.method];
            return (
              <li key={`${h.fw_version}-${h.thing_name}-${h.from}`} className="border-l-2 border-gray-200 pl-2">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono font-semibold text-gray-800">{h.fw_version}</span>
                  <span className={`rounded px-1 py-px text-[10px] font-medium ${label.cls}`}>{label.text}</span>
                  {h.gateway_changed && <span className="rounded bg-purple-50 px-1 py-px text-[10px] font-medium text-purple-700">new gateway</span>}
                </div>
                <div className="text-gray-500">
                  {h.from ? formatLastSeen(h.from) : '—'} → {h.to ? formatLastSeen(h.to) : '—'}
                </div>
                <div className="text-gray-400">
                  {h.thing_name || 'unknown gateway'} · {h.readings} reading{h.readings === 1 ? '' : 's'}
                  {h.ota_update_id ? ` · ${h.ota_update_id}` : ''}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) { map.setView(points[0], 15); return; }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [map, points]);
  return null;
}

/** Zooms to the focused meter and opens its popup. */
function FocusController({
  target,
  markerRefs,
}: {
  target: FleetMapMeter | null;
  markerRefs: React.MutableRefObject<Record<string, L.CircleMarker | null>>;
}) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.setView([target.lat, target.lng], 17);
    const t = setTimeout(() => {
      markerRefs.current[target.meter_id]?.openPopup();
    }, 400);
    return () => clearTimeout(t);
  }, [map, target, markerRefs]);
  return null;
}

interface FleetMapProps {
  site?: string;
  sites?: { concession: string }[];
  onSiteChange?: (site: string) => void;
  /** Meter serial to zoom to and open (e.g. after a successful assign). */
  focusMeterId?: string | null;
}

const normSerial = (s: string) => s.replace(/^0+/, '') || s;

export default function FleetMap({ site, sites, onSiteChange, focusMeterId }: FleetMapProps) {
  const [data, setData] = useState<FleetMapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [colorMode, setColorMode] = useState<MapColorMode>('status');
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState<FleetMapMeter | null>(null);
  const [notFound, setNotFound] = useState(false);
  const markerRefs = useRef<Record<string, L.CircleMarker | null>>({});

  useEffect(() => {
    setLoading(true);
    setError('');
    getFleetMap(site)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [site]);

  const findMeter = (q: string): FleetMapMeter | null => {
    const needle = q.trim().toLowerCase();
    if (!needle) return null;
    const needleSerial = normSerial(needle);
    return (
      (data?.meters || []).find((m) => {
        const mid = (m.meter_id || '').toLowerCase();
        return (
          normSerial(mid) === needleSerial ||
          (m.account_number || '').toLowerCase() === needle ||
          (m.thing_name || '').toLowerCase() === needle
        );
      }) || null
    );
  };

  // External focus request (e.g. "View on map" after an assign).
  useEffect(() => {
    if (!focusMeterId || !data) return;
    const m = findMeter(focusMeterId);
    if (m) {
      setNotFound(false);
      if (linkedOnly && !m.linked) setLinkedOnly(false); // make sure it renders
      setFocus(m);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusMeterId, data]);

  const visibleMeters = useMemo(() => {
    const base = (data?.meters || []).filter((m) => !linkedOnly || m.linked);
    // Guarantee the focused meter is rendered even if a filter would hide it.
    if (focus && !base.some((m) => m.meter_id === focus.meter_id)) base.push(focus);
    return base;
  }, [data, linkedOnly, focus]);

  const fwLegend = useMemo(() => {
    const counts = new Map<string, number>();
    for (const m of visibleMeters) {
      const key = (m.fw_version || '').trim() || 'no firmware';
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const versions = Array.from(counts.keys()).sort((a, b) => {
      if (a === 'no firmware') return 1;
      if (b === 'no firmware') return -1;
      return a.localeCompare(b, undefined, { numeric: true });
    });
    return versions.map((version, i) => ({
      version,
      n: counts.get(version) || 0,
      color: version === 'no firmware' ? FW_UNKNOWN : FW_PALETTE[i % FW_PALETTE.length],
    }));
  }, [visibleMeters]);

  const fwColor = useMemo(() => {
    const map = new Map(fwLegend.map((row) => [row.version, row.color]));
    return (version: string | null | undefined) => map.get((version || '').trim() || 'no firmware') || FW_UNKNOWN;
  }, [fwLegend]);

  const installScale = useMemo(() => {
    const times = visibleMeters
      .map((m) => installEpoch(m.installed_at))
      .filter((t): t is number => t != null);
    const min = times.length ? Math.min(...times) : null;
    const max = times.length ? Math.max(...times) : null;
    const unknown = visibleMeters.filter((m) => installEpoch(m.installed_at) == null).length;
    return {
      min,
      max,
      unknown,
      color: (raw: string | null | undefined) => {
        const t = installEpoch(raw);
        if (t == null || min == null || max == null) return INSTALL_UNKNOWN;
        if (max === min) return heatColor(1);
        return heatColor((t - min) / (max - min));
      },
    };
  }, [visibleMeters]);

  const points = useMemo(
    () => visibleMeters.map((m) => [m.lat, m.lng] as [number, number]),
    [visibleMeters]
  );
  const center: [number, number] = points.length ? points[0] : [-29.179, 27.592];

  const onSearch = () => {
    const m = findMeter(query);
    setNotFound(!m);
    if (m) {
      if (linkedOnly && !m.linked) setLinkedOnly(false);
      setFocus(m);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 flex-wrap">
        <div className="flex items-center gap-4 text-xs text-gray-600 flex-wrap">
          <div className="flex rounded-lg border border-gray-200 overflow-hidden">
            <button
              type="button"
              onClick={() => setColorMode('status')}
              className={`px-2 py-1 ${colorMode === 'status' ? 'bg-gray-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              Status
            </button>
            <button
              type="button"
              onClick={() => setColorMode('firmware')}
              className={`px-2 py-1 ${colorMode === 'firmware' ? 'bg-gray-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              Firmware
            </button>
            <button
              type="button"
              onClick={() => setColorMode('installed')}
              className={`px-2 py-1 ${colorMode === 'installed' ? 'bg-gray-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              Installed
            </button>
            <button
              type="button"
              onClick={() => setColorMode('hybrid')}
              className={`px-2 py-1 ${colorMode === 'hybrid' ? 'bg-gray-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              title="Fill is online or offline. Border is install-date heat."
            >
              Hybrid
            </button>
          </div>
          {colorMode === 'status' && (
            <>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-green-600" /> online / reporting ({data?.online ?? 0})</span>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-red-500" /> installed, offline ({data?.offline ?? 0})</span>
            </>
          )}
          {colorMode === 'firmware' && fwLegend.map((row) => (
            <span key={row.version} className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-3 rounded-full" style={{ background: row.color }} />
              {row.version} ({row.n})
            </span>
          ))}
          {(colorMode === 'installed' || colorMode === 'hybrid') && (
            <>
              {colorMode === 'hybrid' && (
                <>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-green-600" /> fill online</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-red-500" /> fill offline</span>
                </>
              )}
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block h-3 w-16 rounded"
                  style={{ background: 'linear-gradient(to right, rgb(191, 219, 254), rgb(251, 191, 36), rgb(185, 28, 28))' }}
                />
                {colorMode === 'hybrid' ? 'border' : 'install date'}{' '}
                {installScale.min != null ? formatInstalled(new Date(installScale.min).toISOString()) : '—'}
                {' → '}
                {installScale.max != null ? formatInstalled(new Date(installScale.max).toISOString()) : '—'}
              </span>
              {installScale.unknown > 0 && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-3 h-3 rounded-full" style={{ background: INSTALL_UNKNOWN }} />
                  no install date ({installScale.unknown})
                </span>
              )}
            </>
          )}
          {(data?.no_gps ?? 0) > 0 && <span className="text-gray-400">+{data?.no_gps} with no GPS</span>}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-1.5">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSearch()}
              placeholder="Find meter / account / gateway"
              className={`px-2.5 py-1.5 border rounded-lg text-xs bg-white w-44 ${notFound ? 'border-red-400' : 'border-gray-300'}`}
              title="Type a meter serial, account, or gateway name and press Enter to zoom to it"
            />
            <button
              onClick={onSearch}
              className="px-2.5 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700"
            >
              Find
            </button>
            {notFound && <span className="text-xs text-red-500">not found</span>}
          </div>
          <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer" title="Show only meters linked to a 1Meter gateway">
            <input
              type="checkbox"
              checked={linkedOnly}
              onChange={(e) => setLinkedOnly(e.target.checked)}
              className="rounded"
            />
            1Meter linked
          </label>
          {sites && onSiteChange && (
            <select
              value={site || ''}
              onChange={(e) => onSiteChange(e.target.value)}
              className="px-2.5 py-1.5 border border-gray-300 rounded-lg text-xs bg-white font-medium text-gray-700"
              title="Show meters for one site"
            >
              <option value="">All sites</option>
              {sites.map((s) => <option key={s.concession} value={s.concession}>{s.concession}</option>)}
            </select>
          )}
          <span className="text-xs text-gray-400">{data ? `${visibleMeters.length} mapped` : ''}</span>
        </div>
      </div>
      <div style={{ height: 520 }}>
        {loading ? (
          <div className="h-full flex items-center justify-center text-gray-400 text-sm gap-2">
            <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
            Loading fleet map…
          </div>
        ) : error ? (
          <div className="p-4"><div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div></div>
        ) : !data || !visibleMeters.length ? (
          <div className="h-full flex items-center justify-center text-gray-400 text-sm">{linkedOnly ? 'No 1Meter-linked meters with GPS to map.' : 'No meters with GPS to map.'}</div>
        ) : (
          <MapContainer center={center} zoom={13} style={{ height: '100%', width: '100%' }}>
            <FitBounds points={points} />
            <FocusController target={focus} markerRefs={markerRefs} />
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {visibleMeters.map((m) => {
              const isFocus = focus?.meter_id === m.meter_id;
              const statusFill = m.online ? STATUS_ONLINE : STATUS_OFFLINE;
              const heat = installScale.color(m.installed_at);
              const fill = colorMode === 'firmware'
                ? fwColor(m.fw_version)
                : colorMode === 'installed'
                  ? heat
                  : statusFill;
              const stroke = colorMode === 'hybrid'
                ? heat
                : colorMode === 'installed'
                  ? heat
                  : colorMode === 'firmware'
                    ? fill
                    : statusFill;
              const weight = colorMode === 'hybrid'
                ? (isFocus ? 6 : 4)
                : (isFocus ? 4 : (m.linked ? 3.5 : 1.5));
              return (
                <CircleMarker
                  key={`${m.meter_id}-${colorMode}-${fill}-${stroke}`}
                  ref={(r) => { markerRefs.current[m.meter_id] = r; }}
                  center={[m.lat, m.lng]}
                  radius={isFocus ? 11 : 7}
                  pathOptions={{
                    color: isFocus && colorMode !== 'hybrid' && colorMode !== 'installed' ? '#2563eb' : stroke,
                    fillColor: fill,
                    fillOpacity: 0.85,
                    weight,
                  }}
                >
                  <Popup>
                    <div className="text-sm">
                      <div className="font-semibold">{m.thing_name || m.meter_id}</div>
                      <div className="text-xs text-gray-600">
                        meter{' '}
                        <Link to={`/meters?meter=${encodeURIComponent(m.meter_id)}`} className="text-blue-600 hover:underline" title="Open this meter on the Meters page">
                          {m.meter_id}
                        </Link>
                        {m.account_number && (
                          <>
                            {' · '}
                            <Link to={`/customers/${encodeURIComponent(m.account_number)}`} className="text-blue-600 hover:underline" title="Open this customer">
                              {m.account_number}
                            </Link>
                          </>
                        )}
                      </div>
                      {m.village && <div className="text-xs text-gray-500">{m.village}</div>}
                      {m.linked && (
                        <div className="text-xs text-blue-600 font-medium">
                          1Meter linked{m.thing_name ? ` · ${m.thing_name}` : ''}
                          {m.pole_id ? ` · pole ${m.pole_id}` : ''}
                          {m.gateway_pending ? ' · gateway pending' : ''}
                        </div>
                      )}
                      <div className="text-xs mt-1">{m.online ? 'online / reporting' : 'installed, offline'}</div>
                      <div className="text-xs text-gray-600">installed {formatInstalled(m.installed_at)}</div>
                      {!m.online && (
                        <div className="text-xs text-gray-600">
                          offline since {m.offline_since ? formatLastSeen(m.offline_since) : 'never reported'}
                        </div>
                      )}
                      <div className="text-xs text-gray-600">FW {m.fw_version || '—'}</div>
                      {m.last_seen && <div className="text-xs text-gray-400">last seen {formatLastSeen(m.last_seen)}</div>}
                      {m.linked && <FirmwareHistory meterId={m.meter_id} />}
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}
          </MapContainer>
        )}
      </div>
    </div>
  );
}
