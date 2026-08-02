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

const PRE_FORMATTED_MELBOURNE =
  /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})? (AEST|AEDT|Melbourne)$/;

function isPreFormattedMelbourne(value: string): boolean {
  return PRE_FORMATTED_MELBOURNE.test(value.trim());
}

/**
 * Parse a pre-formatted Melbourne string (`2026-08-02 24:57:21 AEST`) back to a
 * UTC Date. The bot mapper occasionally emits hour=24 around Melbourne midnight
 * (UTC+10) when it formats a UTC midnight boundary; native Date rejects hour=24,
 * so we collapse 24 → 00 next day by hand before constructing an ISO string.
 *
 * Returns null when the string cannot be parsed.
 */
function parsePreFormattedMelbourne(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))? (AEST|AEDT|Melbourne)$/.exec(value.trim());
  if (!m) return null;
  let [, yyyy, mm, dd, hh, mi, ss = '00', tz] = m;
  // Hour 24 is invalid in ISO 8601 / JS Date — roll into next day at 00.
  // This is the Melbourne-midnight boundary case the bot mapper mishandles.
  if (hh === '24') {
    const rolled = new Date(Date.UTC(Number(yyyy), Number(mm) - 1, Number(dd), 0, 0, 0));
    rolled.setUTCDate(rolled.getUTCDate() + 1);
    yyyy = String(rolled.getUTCFullYear());
    mm = String(rolled.getUTCMonth() + 1).padStart(2, '0');
    dd = String(rolled.getUTCDate()).padStart(2, '0');
    hh = '00';
  }
  // Melbourne fixed-offset: AEST = +10:00, AEDT = +11:00. "Melbourne" with no
  // abbreviation falls back to +10:00 (AEST); the formatter re-derives the
  // correct abbreviation from the resolved instant below.
  const offset = tz === 'AEDT' ? '+11:00' : '+10:00';
  const isoUtcGuess = `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}${offset}`;
  const parsed = new Date(isoUtcGuess);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** `2026-06-22 14:30:45 AEST` (24h, Melbourne). */
export function formatMelbourneDateTime(input: string | number | Date | null | undefined): string {
  if (typeof input === 'string') {
    const trimmed = input.trim();
    if (!trimmed || trimmed === '-' || trimmed === '—') return '—';
    if (isPreFormattedMelbourne(trimmed)) {
      // Re-normalize: the bot mapper can emit hour=24 around Melbourne midnight.
      // Round-tripping through Date + Intl collapses 24 → 00 next day and also
      // validates the abbreviation (AEDT during AU summer, AEST otherwise).
      const reparsed = parsePreFormattedMelbourne(trimmed);
      if (reparsed) return formatMelbourneDateTime(reparsed);
      return trimmed;
    }
  }
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
    hourCycle: 'h23',
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

/**
 * Parse a Melbourne-formatted timestamp (`2026-07-31 16:00:00 AEST`) to
 * milliseconds-since-epoch. Used by viewers that need to compute durations
 * (e.g. expired-order age) from columns that already went through the bot's
 * Melbourne formatter — `Date.parse` rejects the `AEST` suffix, so we strip it
 * and re-apply the canonical +10:00 / +11:00 offset.
 *
 * Falls back to `parseTimestampMs` for inputs that are already ISO 8601, epoch,
 * or Date objects.
 */
export function parseMelbourneTimestampMs(
  input: string | number | Date | null | undefined,
): number | null {
  if (input == null) return null;
  if (typeof input !== 'string') return parseTimestampMs(input);
  const trimmed = input.trim();
  if (!trimmed || trimmed === '-' || trimmed === '—') return null;
  // Already ISO 8601 (with offset or Z) — parse directly.
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(trimmed)) return parseTimestampMs(trimmed);
  // Melbourne pre-formatted string.
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))? (AEST|AEDT|Melbourne)$/.exec(trimmed);
  if (!m) return parseTimestampMs(trimmed);
  let [, yyyy, mm, dd, hh, mi, ss = '00', tz] = m;
  // Hour 24 is invalid in ISO 8601 / JS Date — roll into next day at 00.
  // See parsePreFormattedMelbourne for the underlying regression rationale.
  if (hh === '24') {
    const rolled = new Date(Date.UTC(Number(yyyy), Number(mm) - 1, Number(dd), 0, 0, 0));
    rolled.setUTCDate(rolled.getUTCDate() + 1);
    yyyy = String(rolled.getUTCFullYear());
    mm = String(rolled.getUTCMonth() + 1).padStart(2, '0');
    dd = String(rolled.getUTCDate()).padStart(2, '0');
    hh = '00';
  }
  const offset = tz === 'AEDT' ? '+11:00' : '+10:00';
  const iso = `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}${offset}`;
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : parsed;
}

/** Format once — accepts raw ISO/epoch or pre-formatted Melbourne strings from the bot mapper. */
export function displayMelbourneTime(input: string | number | Date | null | undefined): string {
  return formatMelbourneDateTime(input);
}
