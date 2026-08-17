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

export default function FleetMap({ site }: { site?: string }) {
  const [data, setData] = useState<FleetMapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    setError('');
    getFleetMap(site)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [site]);

  const points = useMemo(
    () => (data?.meters || []).map((m) => [m.lat, m.lng] as [number, number]),
    [data]
  );
  const center: [number, number] = points.length ? points[0] : [-29.179, 27.592];

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <div className="flex items-center gap-4 text-xs text-gray-600">
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-green-600" /> online / reporting ({data?.online ?? 0})</span>
          <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-full bg-red-500" /> installed, offline ({data?.offline ?? 0})</span>
          {(data?.no_gps ?? 0) > 0 && <span className="text-gray-400">+{data?.no_gps} with no GPS</span>}
        </div>
        <span className="text-xs text-gray-400">{data ? `${data.total} mapped` : ''}</span>
      </div>
      <div style={{ height: 520 }}>
        {loading ? (
          <div className="h-full flex items-center justify-center text-gray-400 text-sm gap-2">
            <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
            Loading fleet map…
          </div>
        ) : error ? (
          <div className="p-4"><div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div></div>
        ) : !data || !data.meters.length ? (
          <div className="h-full flex items-center justify-center text-gray-400 text-sm">No meters with GPS to map.</div>
        ) : (
          <MapContainer center={center} zoom={13} style={{ height: '100%', width: '100%' }}>
            <FitBounds points={points} />
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {data.meters.map((m) => (
              <CircleMarker
                key={m.meter_id}
                center={[m.lat, m.lng]}
                radius={7}
                pathOptions={{
                  color: m.online ? '#16a34a' : '#ef4444',
                  fillColor: m.online ? '#16a34a' : '#ef4444',
                  fillOpacity: 0.75,
                  weight: 1.5,
                }}
              >
                <Popup>
                  <div className="text-sm">
                    <div className="font-semibold">{m.thing_name || m.meter_id}</div>
                    <div className="text-xs text-gray-600">meter {m.meter_id}{m.account_number ? ` · ${m.account_number}` : ''}</div>
                    {m.village && <div className="text-xs text-gray-500">{m.village}</div>}
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
