import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  cancelFirmwareTarget,
  enqueueFirmwareTarget,
  getFirmwareLibrary,
  getFirmwareTargetQueue,
  previewFirmwareTarget,
  resumeFirmwareTarget,
  type FirmwareLibraryEntry,
  type FirmwareTargetPreview,
  type FirmwareTargetQueue,
} from '../lib/api';

function reasonKey(reason: string): string {
  if (reason === 'too_old') return 'firmwareReasonTooOld';
  if (reason === 'already') return 'firmwareReasonAlready';
  if (reason === 'already_queued') return 'firmwareReasonQueued';
  return 'firmwareReasonNoGateway';
}

export function FirmwareTargetDialog({
  meterIds,
  onClose,
  onQueued,
}: {
  meterIds: string[];
  onClose: () => void;
  onQueued: () => void;
}) {
  const { t } = useTranslation('meters');
  const [versions, setVersions] = useState<FirmwareLibraryEntry[]>([]);
  const [version, setVersion] = useState('');
  const [preview, setPreview] = useState<FirmwareTargetPreview | null>(null);
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getFirmwareLibrary()
      .then((res) => {
        setVersions(res.versions || []);
        const newest = (res.versions || []).find((row) => row.selectable);
        if (newest) setVersion(newest.version);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (!version) return;
    let cancelled = false;
    previewFirmwareTarget(meterIds, version)
      .then((row) => { if (!cancelled) setPreview(row); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [meterIds, version]);

  const queuedCount = useMemo(
    () => (preview?.gateways || []).filter((row) => row.action === 'update' || row.action === 'rollback').length,
    [preview],
  );
  const skipped = useMemo(() => {
    const rows = [
      ...(preview?.gateways || [])
        .filter((row) => row.action !== 'update' && row.action !== 'rollback')
        .map((row) => ({ name: row.thing_name, reason: row.action })),
      ...(preview?.skipped || []).map((row) => ({ name: row.meter_id || row.thing_name || '', reason: row.reason })),
    ];
    return rows;
  }, [preview]);
  const needsConfirm = Boolean(preview?.rollback);
  const canSubmit = Boolean(version) && queuedCount > 0 && (!needsConfirm || confirm.trim() === version);

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      await enqueueFirmwareTarget(meterIds, version, needsConfirm ? confirm.trim() : undefined);
      onQueued();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full mx-4 p-6 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-gray-800">{t('firmwareTitle')}</h3>
        <p className="text-xs text-gray-500 mt-1">{t('firmwareHint')}</p>
        <label className="block text-sm font-medium text-gray-700 mt-4">{t('firmwareVersion')}</label>
        <select
          value={version}
          onChange={(e) => { setVersion(e.target.value); setConfirm(''); }}
          className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
        >
          {versions.map((row) => (
            <option key={row.version} value={row.version} disabled={!row.selectable}>
              {row.version}{row.selectable ? '' : ` (${t('firmwareNotSelectable')})`}
            </option>
          ))}
        </select>
        {preview && (
          <p className="text-sm text-gray-700 mt-3">
            {t('firmwareSummary', { meters: meterIds.length, gateways: queuedCount })}
          </p>
        )}
        {skipped.length > 0 && (
          <ul className="mt-2 text-xs text-gray-500 space-y-1">
            {skipped.slice(0, 8).map((row) => (
              <li key={`${row.reason}-${row.name}`}>{row.name}: {t(reasonKey(row.reason))}</li>
            ))}
            {skipped.length > 8 && <li>{t('firmwareMoreSkipped', { count: skipped.length - 8 })}</li>}
          </ul>
        )}
        {needsConfirm && (
          <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm text-amber-900">{t('firmwareRollback')}</p>
            <input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={version}
              className="mt-2 w-full border border-amber-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
        )}
        {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
        <div className="flex gap-3 mt-5">
          <button type="button" onClick={onClose} className="flex-1 py-2.5 bg-gray-100 text-gray-700 rounded-xl text-sm font-medium">
            {t('cancel')}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit || busy}
            className="flex-1 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold disabled:opacity-40"
          >
            {busy ? t('processing') : t('firmwareQueue')}
          </button>
        </div>
      </div>
    </div>
  );
}

export function FirmwareQueueLine() {
  const { t } = useTranslation('meters');
  const [queue, setQueue] = useState<FirmwareTargetQueue | null>(null);

  const load = () => {
    getFirmwareTargetQueue()
      .then(setQueue)
      .catch(() => setQueue(null));
  };

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, []);

  if (!queue || (queue.pending === 0 && queue.active.length === 0 && queue.held === 0)) return null;
  const sites = Array.from(new Set([
    ...queue.active.map((row) => row.site_code),
    ...queue.rows.filter((row) => row.status === 'held' || row.status === 'pending').map((row) => row.site_code),
  ]));
  const failed = queue.rows.find((row) => row.status === 'failed' && row.detail);

  return (
    <div className="mb-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
      {queue.active.map((row) => (
        <div key={row.thing_name}>{t('firmwareActive', { thing: row.thing_name, version: row.target_version })}</div>
      ))}
      {queue.pending > 0 && <div>{t('firmwareWaiting', { count: queue.pending })}</div>}
      {queue.held > 0 && <div>{t('firmwareHeld', { count: queue.held })}{failed?.detail ? ` — ${failed.detail}` : ''}</div>}
      <div className="flex gap-2 mt-2">
        {sites.map((site) => (
          <button
            key={`cancel-${site}`}
            type="button"
            className="px-2 py-1 text-xs rounded-lg bg-white border border-blue-200"
            onClick={() => cancelFirmwareTarget({ site_code: site }).then(load)}
          >
            {t('firmwareCancelSite', { site })}
          </button>
        ))}
        {queue.held > 0 && sites.map((site) => (
          <button
            key={`resume-${site}`}
            type="button"
            className="px-2 py-1 text-xs rounded-lg bg-white border border-blue-200"
            onClick={() => resumeFirmwareTarget(site).then(load)}
          >
            {t('firmwareResumeSite', { site })}
          </button>
        ))}
      </div>
    </div>
  );
}
