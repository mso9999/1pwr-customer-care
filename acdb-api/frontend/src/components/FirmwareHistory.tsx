import { useEffect, useState } from 'react';
import { getFirmwareHistory, type FirmwareHistoryEntry } from '../lib/api';
import { formatLastSeen } from '../lib/datetime';

const METHOD_LABEL: Record<FirmwareHistoryEntry['method'], { text: string; cls: string }> = {
  ota: { text: 'OTA', cls: 'bg-blue-50 text-blue-700' },
  serial: { text: 'serial', cls: 'bg-amber-50 text-amber-800' },
  initial: { text: 'first seen', cls: 'bg-gray-100 text-gray-600' },
  unknown: { text: 'OTA status unknown', cls: 'bg-gray-100 text-gray-600' },
};

/** Firmware timeline from the meter's readings. Collapsed ones load on first open. */
export default function FirmwareHistory({ meterId, defaultOpen = false }: { meterId: string; defaultOpen?: boolean }) {
  const [rows, setRows] = useState<FirmwareHistoryEntry[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = () => {
    if (rows || loading) return;
    setLoading(true);
    setError('');
    getFirmwareHistory(meterId)
      .then((r) => setRows(r.history))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (defaultOpen) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meterId, defaultOpen]);

  return (
    <details
      className="mt-1.5 text-xs"
      open={defaultOpen}
      onToggle={(e) => { if (e.currentTarget.open) load(); }}
    >
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
