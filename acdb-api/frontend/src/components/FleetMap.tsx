import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { getFleetMap, getFleetMapDownloads, getFleetMapOta, type FleetMapMeter, type FleetMapOta, type FleetMapResult } from '../lib/api';
import FirmwareHistory from './FirmwareHistory';
import { formatLastSeen, parseTelemetryTs } from '../lib/datetime';

type MapColorMode = 'status' | 'firmware' | 'installed' | 'hybrid';

const FW_PALETTE = [
  '#2563eb', '#7c3aed', '#0891b2', '#ca8a04', '#db2777',
  '#0f766e', '#c2410c', '#4f46e5', '#65a30d', '#9333ea',
];
const FW_UNKNOWN = '#9ca3af';
const STATUS_LIVE = '#16a34a';
const STATUS_RECENT = '#d97706';
const STATUS_SILENT = '#ef4444';
const STATUS_DOWNLOADING = '#7c3aed';
const LIVE_MS = 20 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

type ReportFreshness = 'live' | 'recent' | 'silent';

/** Live is the normal report cadence. Recent still counted as today's fleet. */
function reportFreshness(lastSeen: string | null | undefined): ReportFreshness {
  const seen = parseTelemetryTs(lastSeen);
  if (!seen) return 'silent';
  const age = Date.now() - seen.getTime();
  if (age <= LIVE_MS) return 'live';
  if (age <= DAY_MS) return 'recent';
  return 'silent';
}

function statusColor(freshness: ReportFreshness): string {
  if (freshness === 'live') return STATUS_LIVE;
  if (freshness === 'recent') return STATUS_RECENT;
  return STATUS_SILENT;
}

/** History loads after the popup opens. Re-measure so the box stays in the map. */
function RefitPopup() {
  const map = useMap();
  useEffect(() => {
    const pane = map.getPane('popupPane');
    if (!pane) return;
    let timer = 0;
    const obs = new MutationObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const popup = (map as unknown as { _popup?: L.Popup })._popup;
        if (popup?.isOpen()) popup.update();
      }, 40);
    });
    obs.observe(pane, { childList: true, subtree: true });
    return () => {
      obs.disconnect();
      window.clearTimeout(timer);
    };
  }, [map]);
  return null;
}
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

/** Metres between two map points. */
function metresApart(aLat: number, aLng: number, bLat: number, bLng: number): number {
  const rad = Math.PI / 180;
  const p1 = aLat * rad;
  const p2 = bLat * rad;
  const dPhi = (bLat - aLat) * rad;
  const dLng = (bLng - aLng) * rad;
  const h = Math.sin(dPhi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

interface GatewayPin {
  key: string;
  thingName: string | null;
  lat: number;
  lng: number;
  meters: FleetMapMeter[];
  /** How far the furthest recorded coordinate is from the pin. */
  spreadM: number;
}

/**
 * A gateway is one device, so it is one pin. Meters that report through it
 * keep their own coordinates in the popup. A meter with no gateway stays
 * its own pin.
 */
function pinsForMeters(meters: FleetMapMeter[]): GatewayPin[] {
  const groups = new Map<string, FleetMapMeter[]>();
  for (const meter of meters) {
    const thing = (meter.thing_name || '').trim();
    const key = thing || `meter:${meter.meter_id}`;
    const list = groups.get(key);
    if (list) list.push(meter);
    else groups.set(key, [meter]);
  }
  const pins: GatewayPin[] = [];
  for (const [key, list] of groups) {
    const lat = median(list.map((meter) => meter.lat));
    const lng = median(list.map((meter) => meter.lng));
    const spreadM = Math.max(...list.map((meter) => metresApart(lat, lng, meter.lat, meter.lng)));
    pins.push({
      key,
      thingName: (list[0].thing_name || '').trim() || null,
      lat,
      lng,
      meters: list,
      spreadM,
    });
  }
  return pins;
}

function newestMeter(meters: FleetMapMeter[]): FleetMapMeter {
  return meters.reduce((best, meter) => {
    const bestAt = parseTelemetryTs(best.last_seen)?.getTime() || 0;
    const at = parseTelemetryTs(meter.last_seen)?.getTime() || 0;
    return at > bestAt ? meter : best;
  });
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
    const marker = markerRefs.current[target.meter_id];
    const at = marker?.getLatLng();
    map.setView(at ? [at.lat, at.lng] : [target.lat, target.lng], 17);
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
  selectedIds?: ReadonlySet<string>;
  canTargetFirmware?: boolean;
  onToggleMeter?: (meterId: string) => void;
  onSelectMeters?: (meterIds: string[]) => void;
  onTargetMeter?: (meterId: string) => void;
}

/** Live download for the gateway this meter reports through. Polls only while open. */
function MeterOta({ thingName }: { thingName: string }) {
  const [ota, setOta] = useState<FleetMapOta | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      getFleetMapOta(thingName)
        .then((row) => { if (!cancelled) { setOta(row); setError(''); } })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [thingName]);

  if (error) return <div className="text-xs text-gray-400 mt-1">OTA status unavailable</div>;
  if (!ota?.in_flight) return null;
  const pct = Math.max(0, Math.min(100, ota.percent ?? 0));
  const version = ota.target_version || '';
  const phase = ota.phase || (ota.status === 'QUEUED' ? 'starting' : 'downloading');
  const blocks = ota.blocks_total
    ? `${ota.blocks_received ?? 0}/${ota.blocks_total} blocks`
    : '';
  const named = version ? ` ${version}` : '';
  const line = phase === 'waiting_online'
    ? `Queued for${named}. Starts when the gateway is online.`
    : phase === 'held'
      ? `Queued for${named}. Held until this site is resumed.`
      : phase === 'starting'
        ? `Queued for${named}. Starting it on the gateway now.`
        : version
          ? `Downloading ${version}`
          : 'Downloading firmware';
  return (
    <div className="mt-1.5">
      <div className="text-xs text-gray-700">{line}</div>
      {phase === 'downloading' && (
        <>
          <div className="mt-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div className="h-full bg-blue-600" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-[11px] text-gray-500">{pct}%{blocks ? ` · ${blocks}` : ''}</div>
        </>
      )}
    </div>
  );
}

const normSerial = (s: string) => s.replace(/^0+/, '') || s;

export default function FleetMap({
  site, sites, onSiteChange, focusMeterId,
  selectedIds, canTargetFirmware, onToggleMeter, onSelectMeters, onTargetMeter,
}: FleetMapProps) {
  const { t } = useTranslation('meters');
  const [selectMode, setSelectMode] = useState(false);
  const [openMeterId, setOpenMeterId] = useState<string | null>(null);
  const [data, setData] = useState<FleetMapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [colorMode, setColorMode] = useState<MapColorMode>('status');
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState<FleetMapMeter | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [downloading, setDownloading] = useState<Set<string>>(new Set());
  const markerRefs = useRef<Record<string, L.CircleMarker | null>>({});

  useEffect(() => {
    let cancel = false;
    const load = () => {
      getFleetMapDownloads()
        .then((row) => { if (!cancel) setDownloading(new Set(row.things || [])); })
        .catch(() => { if (!cancel) setDownloading(new Set()); });
    };
    load();
    const timer = window.setInterval(load, 20000);
    return () => { cancel = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancel = false;
    const load = (first: boolean) => {
      if (first) {
        setLoading(true);
        setError('');
      }
      getFleetMap(site)
        .then((row) => { if (!cancel) setData(row); })
        .catch((e) => { if (!cancel && first) setError(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (!cancel && first) setLoading(false); });
    };
    load(true);
    // Last-seen is a snapshot. Without this, a meter goes orange after 20 minutes
    // on screen even while it keeps reporting.
    const timer = window.setInterval(() => load(false), 60_000);
    return () => { cancel = true; window.clearInterval(timer); };
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

  const pins = useMemo(() => pinsForMeters(visibleMeters), [visibleMeters]);
  const points = useMemo(
    () => pins.map((pin) => [pin.lat, pin.lng] as [number, number]),
    [pins]
  );
  const center: [number, number] = points.length ? points[0] : [-29.179, 27.592];
  const freshnessCounts = useMemo(() => {
    const counts = { live: 0, recent: 0, silent: 0, downloading: 0 };
    for (const m of data?.meters || []) {
      if (m.thing_name && downloading.has(m.thing_name)) {
        counts.downloading += 1;
        continue;
      }
      counts[reportFreshness(m.last_seen)] += 1;
    }
    return counts;
  }, [data, downloading]);

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
              title="Fill is how recently the meter reported. Border is install-date heat."
            >
              Hybrid
            </button>
          </div>
          {canTargetFirmware && (
            <>
              <button
                type="button"
                onClick={() => setSelectMode((on) => !on)}
                className={`px-2 py-1 rounded border ${selectMode ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-200'}`}
              >
                {t('selectMode')}
              </button>
              <button
                type="button"
                onClick={() => onSelectMeters?.((data?.meters || []).map((m) => m.meter_id))}
                className="px-2 py-1 rounded border border-gray-200 bg-white text-gray-600"
              >
                {site ? t('selectAllSite') : t('selectAllOnMap')}
              </button>
            </>
          )}
          {colorMode === 'status' && (
            <>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_DOWNLOADING }} /> downloading ({freshnessCounts.downloading})</span>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_LIVE }} /> reporting now ({freshnessCounts.live})</span>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_RECENT }} /> last 24 hours ({freshnessCounts.recent})</span>
              <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_SILENT }} /> not reporting ({freshnessCounts.silent})</span>
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
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_DOWNLOADING }} /> fill downloading</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_LIVE }} /> fill now</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_RECENT }} /> fill 24 h</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full" style={{ background: STATUS_SILENT }} /> fill silent</span>
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
          <MapContainer center={center} zoom={13} className="relative z-0" style={{ height: '100%', width: '100%' }}>
            <FitBounds points={points} />
            <RefitPopup />
            <FocusController target={focus} markerRefs={markerRefs} />
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {pins.map((pin) => {
              const m = newestMeter(pin.meters);
              const isFocus = pin.meters.some((meter) => meter.meter_id === focus?.meter_id);
              const freshness = reportFreshness(m.last_seen);
              const downloadingNow = Boolean(pin.thingName && downloading.has(pin.thingName));
              const statusFill = downloadingNow ? STATUS_DOWNLOADING : statusColor(freshness);
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
              const picked = pin.meters.some((meter) => selectedIds?.has(meter.meter_id));
              const weight = picked ? 5 : colorMode === 'hybrid'
                ? (isFocus ? 6 : 4)
                : (isFocus ? 4 : (m.linked ? 3.5 : 1.5));
              const pole = pin.meters.map((meter) => meter.pole_id).find(Boolean);
              return (
                <CircleMarker
                  key={`${pin.key}-${colorMode}-${fill}-${stroke}-${picked ? 1 : 0}`}
                  ref={(r) => { for (const meter of pin.meters) markerRefs.current[meter.meter_id] = r; }}
                  center={[pin.lat, pin.lng]}
                  radius={isFocus || picked ? 11 : 7}
                  pathOptions={{
                    color: picked || (isFocus && colorMode !== 'hybrid' && colorMode !== 'installed') ? '#2563eb' : stroke,
                    fillColor: fill,
                    fillOpacity: 0.85,
                    weight,
                  }}
                  eventHandlers={{
                    click: () => { if (selectMode && pin.meters.length === 1) onToggleMeter?.(pin.meters[0].meter_id); },
                    popupopen: () => setOpenMeterId(pin.key),
                    popupclose: () => setOpenMeterId((cur) => (cur === pin.key ? null : cur)),
                  }}
                >
                  <Popup
                    maxHeight={300}
                    autoPan
                    keepInView
                    autoPanPaddingTopLeft={L.point(24, 24)}
                    autoPanPaddingBottomRight={L.point(24, 24)}
                  >
                    <div className="text-sm">
                      <div className="font-semibold">{pin.thingName || m.meter_id}</div>
                      {pin.meters.length > 1 && pin.spreadM > 50 && (
                        <div className="text-xs text-amber-800 mt-1">
                          One gateway. These meters are recorded {Math.round(pin.spreadM)} m apart, so it is shown once.
                        </div>
                      )}
                      {pin.meters.map((meter) => (
                        <div key={meter.meter_id} className="text-xs text-gray-600 mt-1">
                          meter{' '}
                          <Link to={`/meters/${encodeURIComponent(meter.meter_id)}`} className="text-blue-600 hover:underline" title="Open meter detail">
                            {meter.meter_id}
                          </Link>
                          {meter.account_number && (
                            <>
                              {' · '}
                              <Link to={`/customers/${encodeURIComponent(meter.account_number)}`} className="text-blue-600 hover:underline" title="Open this customer">
                                {meter.account_number}
                              </Link>
                            </>
                          )}
                          {selectMode && (
                            <button
                              type="button"
                              className="ml-2 text-blue-600 hover:underline"
                              onClick={() => onToggleMeter?.(meter.meter_id)}
                            >
                              {selectedIds?.has(meter.meter_id) ? 'selected' : 'select'}
                            </button>
                          )}
                        </div>
                      ))}
                      {m.village && <div className="text-xs text-gray-500">{m.village}</div>}
                      {m.linked && (
                        <div className="text-xs text-blue-600 font-medium">
                          1Meter linked{pin.thingName ? ` · ${pin.thingName}` : ''}
                          {pole ? ` · pole ${pole}` : ''}
                          {m.gateway_pending ? ' · gateway pending' : ''}
                        </div>
                      )}
                      <div className="text-xs mt-1">
                        {downloadingNow
                          ? 'downloading firmware'
                          : freshness === 'live' ? 'reporting now' : freshness === 'recent' ? 'reported in the last 24 hours' : 'not reporting'}
                      </div>
                      <div className="text-xs text-gray-600">installed {formatInstalled(m.installed_at)}</div>
                      {!m.online && (
                        <div className="text-xs text-gray-600">
                          offline since {m.offline_since ? formatLastSeen(m.offline_since) : 'never reported'}
                        </div>
                      )}
                      <div className="text-xs text-gray-600">FW {m.fw_version || '—'}</div>
                      {canTargetFirmware && pin.meters.map((meter) => (
                        <button
                          key={`fw-${meter.meter_id}`}
                          type="button"
                          className="mt-1 block text-xs text-blue-600 hover:underline"
                          onClick={() => onTargetMeter?.(meter.meter_id)}
                        >
                          {t('updateFirmware')}{pin.meters.length > 1 ? ` · ${meter.meter_id}` : ''}
                        </button>
                      ))}
                      {openMeterId === pin.key && pin.thingName && <MeterOta thingName={pin.thingName} />}
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
