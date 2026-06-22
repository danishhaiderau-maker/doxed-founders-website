/** Display timestamps in Melbourne local time (24h) for bot + Agent Hub. */

const MELBOURNE_TZ = 'Australia/Melbourne';

function toDate(input: string | number | Date | null | undefined): Date | null {
  if (input == null || input === '') return null;
  if (input instanceof Date) return Number.isNaN(input.getTime()) ? null : input;
  if (typeof input === 'number') {
    const ms = input > 1e12 ? input : input * 1000;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const parsed = Date.parse(input);
  if (!Number.isNaN(parsed)) return new Date(parsed);
  return null;
}

/** `2026-06-22 14:30:45 AEST` (24h, Melbourne). */
export function formatMelbourneDateTime(input: string | number | Date | null | undefined): string {
  const d = toDate(input);
  if (!d) return '—';
  const parts = new Intl.DateTimeFormat('en-AU', {
    timeZone: MELBOURNE_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? '';
  const abbrev =
    new Intl.DateTimeFormat('en-AU', { timeZone: MELBOURNE_TZ, timeZoneName: 'short' })
      .formatToParts(d)
      .find((p) => p.type === 'timeZoneName')?.value ?? 'Melbourne';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')} ${abbrev}`;
}

/** ISO UTC suffix for audit exports: `2026-06-22 14:30 AEST (2026-06-22T04:30:00.000Z)`. */
export function formatMelbourneWithUtc(input: string | number | Date | null | undefined): string {
  const d = toDate(input);
  if (!d) return '—';
  return `${formatMelbourneDateTime(d)} (${d.toISOString()})`;
}

export function parseTimestampMs(input: string | number | Date | null | undefined): number | null {
  const d = toDate(input);
  return d ? d.getTime() : null;
}
