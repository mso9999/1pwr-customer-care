import { useEffect, useMemo, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { listUGPPoles, type UGPPole } from '../lib/api';

// Fit the map to show ALL poles (every village) on load, instead of centering on
// the first pole's village. Without this the map opens zoomed into one village and
// the operator thinks the other villages' poles are missing.
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 16);
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [map, points]);
  return null;
}

// Fix Leaflet default marker icons (Vite bundling drops the image assets).
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';
delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
L.Icon.Default.mergeOptions({ iconUrl: markerIcon, iconRetinaUrl: markerIcon2x, shadowUrl: markerShadow });

interface Props {
  site: string;
  onSelect: (pole: UGPPole) => void;
  onClose: () => void;
}

const ptbIcon = (hasPtb: boolean) =>
  L.divIcon({
    className: '',
    html: `<div style="width:14px;height:14px;border-radius:50%;border:2px solid #fff;background:${hasPtb ? '#16a34a' : '#2563eb'};box-shadow:0 0 2px rgba(0,0,0,.5)"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });

export default function UGPPolePicker({ site, onSelect, onClose }: Props) {
  const [poles, setPoles] = useState<UGPPole[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!site) return;
    setLoading(true);
    setError('');
    listUGPPoles(site)
      .then((d) => setPoles(d.poles || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [site]);

  const withGps = useMemo(() => poles.filter((p) => p.gps_lat != null && p.gps_lon != null), [poles]);
  const filtered = useMemo(() => {
    const base = withGps;
    if (!search.trim()) return base;
    const q = search.toLowerCase();
    return base.filter((p) => p.pole_id.toLowerCase().includes(q));
  }, [withGps, search]);

  const center: [number, number] = withGps.length
    ? [withGps[0].gps_lat!, withGps[0].gps_lon!]
    : [-29.179, 27.592]; // Ha Makebe default

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white w-full max-w-2xl rounded-2xl shadow-xl max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 pt-5 pb-3 border-b shrink-0">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold text-gray-800">Select pole ({site})</h3>
            <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg transition">
              <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by pole ID…"
            className="w-full px-3 py-2 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-blue-400 focus:border-transparent outline-none"
          />
          <div className="flex gap-3 mt-2 text-xs text-gray-500">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-full bg-blue-600 border border-white" /> no PTB yet</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-full bg-green-600 border border-white" /> has PTB</span>
            <span className="ml-auto">{filtered.length} poles</span>
          </div>
        </div>
        <div className="flex-1 min-h-[320px]">
          {loading ? (
            <div className="text-center py-16 text-gray-400 text-sm flex items-center justify-center gap-2">
              <span className="animate-spin inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
              Loading poles…
            </div>
          ) : error ? (
            <div className="p-4"><div className="p-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">{error}</div></div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 text-gray-400 text-sm">No poles with GPS found{search ? ' for this filter' : ''}.</div>
          ) : (
            <MapContainer center={center} zoom={15} style={{ height: '100%', minHeight: 320, width: '100%' }}>
              <FitBounds points={filtered.map((p) => [p.gps_lat!, p.gps_lon!] as [number, number])} />
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              {filtered.map((p) => (
                <Marker
                  key={p.pole_id}
                  position={[p.gps_lat!, p.gps_lon!]}
                  icon={ptbIcon(p.has_ptb)}
                  eventHandlers={{ click: () => onSelect(p) }}
                >
                  <Popup>
                    <div className="text-sm">
                      <div className="font-semibold">{p.pole_id}</div>
                      <div className="text-xs text-gray-600">{p.subnetwork}</div>
                      <div className="text-xs mt-1">
                        {p.has_ptb ? `PTB: ${p.ptb_id} (status ${p.ptb_status || 'P'})` : 'No PTB yet — will be created'}
                      </div>
                      <div className="text-xs text-gray-500">{p.drop_count} drop(s)</div>
                      <button
                        onClick={(e) => { e.stopPropagation(); onSelect(p); }}
                        className="mt-2 px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700"
                      >
                        Select this pole
                      </button>
                    </div>
                  </Popup>
                </Marker>
              ))}
            </MapContainer>
          )}
        </div>
      </div>
    </div>
  );
}
