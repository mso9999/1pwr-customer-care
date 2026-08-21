/** Telemetry timestamp helpers.
 *
 * meter_last_seen stores compact UTC stamps (`YYYYMMDDHHMM` or
 * `YYYYMMDDHHMMSS`) alongside ISO strings depending on the writer — parse
 * both, render once.
 */

export function parseTelemetryTs(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const s = raw.trim();
  const compact = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?$/.exec(s);
  if (compact) {
    const [, y, mo, d, h, mi, sec] = compact;
    const dt = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +(sec ?? 0)));
    return Number.isNaN(dt.getTime()) ? null : dt;
  }
  const dt = new Date(s);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function relativeAge(d: Date): string {
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 0) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days} day${days === 1 ? '' : 's'} ago`;
  const months = Math.round(days / 30);
  return `${months} mo ago`;
}

/** "25 Jul 2026, 14:24 (27 days ago)" — falls back to the raw string when
 *  unparseable so no data is ever hidden. */
export function formatLastSeen(raw: string | null | undefined): string {
  const d = parseTelemetryTs(raw);
  if (!d) return raw || '—';
  const abs =
    d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) +
    ', ' +
    d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  return `${abs} (${relativeAge(d)})`;
}
