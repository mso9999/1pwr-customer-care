import { useEffect, useMemo, useState } from 'react';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { getFleetMap, type FleetMapResult } from '../lib/api';

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) { map.setView(points[0], 15); return; }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [map, points]);
  return null;
}

interface FleetMapProps {
  site?: string;
  sites?: { concession: string }[];
  onSiteChange?: (site: string) => void;
}

export default function FleetMap({ site, sites, onSiteChange }: FleetMapProps) {
  const [data, setData] = useState<FleetMapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [linkedOnly, setLinkedOnly] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError('');
    getFleetMap(site)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [site]);

  const visibleMeters = useMemo(
    () => (data?.meters || []).filter((m) => !linkedOnly || m.linked),
    [data, linkedOnly]
  );
  const points = useMemo(
    () => visibleMeters.map((m) => [m.lat, m.lng] as [number, number]),
    [visibleMeters]
  );
  const center: [number, number] = points.length ? points[0] : [-29.179, 27.592];

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 flex-wrap">
        <div className="flex items-center gap-4 text-xs text-gray-600">
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-green-600" /> online / reporting ({data?.online ?? 0})</span>
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-red-500" /> installed, offline ({data?.offline ?? 0})</span>
          {(data?.no_gps ?? 0) > 0 && <span className="text-gray-400">+{data?.no_gps} with no GPS</span>}
        </div>
        <div className="flex items-center gap-3">
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
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {visibleMeters.map((m) => (
              <CircleMarker
                key={m.meter_id}
                center={[m.lat, m.lng]}
                radius={7}
                pathOptions={{
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
                    {m.last_seen && <div className="text-xs text-gray-400">last seen {m.last_seen}</div>}
                  </div>
                </Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        )}
      </div>
    </div>
  );
}
