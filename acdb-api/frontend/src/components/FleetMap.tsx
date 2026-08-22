import { useEffect, useMemo, useRef, useState } from 'react';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { getFleetMap, type FleetMapMeter, type FleetMapResult } from '../lib/api';
import { formatLastSeen } from '../lib/datetime';

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
        <div className="flex items-center gap-4 text-xs text-gray-600">
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-green-600" /> online / reporting ({data?.online ?? 0})</span>
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-red-500" /> installed, offline ({data?.offline ?? 0})</span>
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
              return (
                <CircleMarker
                  key={m.meter_id}
                  ref={(r) => { markerRefs.current[m.meter_id] = r; }}
                  center={[m.lat, m.lng]}
                  radius={isFocus ? 11 : 7}
                  pathOptions={isFocus ? {
                    color: '#2563eb',
                    fillColor: '#2563eb',
                    fillOpacity: 0.9,
                    weight: 4,
                  } : {
                    color: m.online ? '#16a34a' : '#ef4444',
                    fillColor: m.online ? '#16a34a' : '#ef4444',
                    fillOpacity: 0.75,
                    // Linked 1Meters get a thicker border so they stand out
                    weight: m.linked ? 3.5 : 1.5,
                  }}
                >
                  <Popup>
                    <div className="text-sm">
                      <div className="font-semibold">{m.thing_name || m.meter_id}</div>
                      <div className="text-xs text-gray-600">meter {m.meter_id}{m.account_number ? ` · ${m.account_number}` : ''}</div>
                      {m.village && <div className="text-xs text-gray-500">{m.village}</div>}
                      {m.linked && <div className="text-xs text-blue-600 font-medium">1Meter linked{m.thing_name ? ` · ${m.thing_name}` : ''}</div>}
                      <div className="text-xs mt-1">{m.online ? 'online / reporting' : 'installed, offline'}</div>
                      {m.last_seen && <div className="text-xs text-gray-400">last seen {formatLastSeen(m.last_seen)}</div>}
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
