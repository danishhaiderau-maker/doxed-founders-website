/**
 * Display timestamps in Melbourne time (24h) for the bot dashboards and Agent Hub.
 *
 * One shared helper for every displayed time (mirrors the bot's Python
 * `melbourne_time.py`): IANA zone `Australia/Melbourne` (UTC+11 during daylight
 * saving, UTC+10 otherwise) and one label, `Melbourne time`. Never hard-code
 * an offset or an "AEST" string. Stored timestamps stay UTC; this only formats.
 *
 * Inputs may also be the bot's interchange form `2026-10-04 19:41:00 AEDT`
 * (abbreviation derived from the zone) or an already-labelled
 * `... Melbourne time` string; both parse back to the exact instant.
 */

export const MELBOURNE_TZ = 'Australia/Melbourne';
export const MELBOURNE_TIME_LABEL = 'Melbourne time';

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

const MELBOURNE_SUFFIX = '(AEST|AEDT|Melbourne time|Melbourne)';
const PRE_FORMATTED_MELBOURNE = new RegExp(
  `^(\\d{4})-(\\d{2})-(\\d{2}) (\\d{2}):(\\d{2})(?::(\\d{2}))? ${MELBOURNE_SUFFIX}$`,
);

function isPreFormattedMelbourne(value: string): boolean {
  return PRE_FORMATTED_MELBOURNE.test(value.trim());
}

function melbourneParts(d: Date): Record<string, string> {
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
  const out: Record<string, string> = {};
  for (const p of parts) out[p.type] = p.value;
  return out;
}

/** Melbourne UTC offset in minutes at an instant (600 or 660), from the IANA zone. */
export function melbourneOffsetMinutes(input: Date | number): number {
  const d = input instanceof Date ? input : new Date(input);
  const p = melbourneParts(d);
  const hour = p.hour === '24' ? 0 : Number(p.hour);
  const asUtc = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), hour, Number(p.minute), Number(p.second));
  return Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / 60000);
}

/** Melbourne wall-clock fields -> UTC ms, resolving daylight saving through the zone. */
function melbourneWallToUtcMs(y: number, mo: number, d: number, h: number, mi: number, s: number): number {
  const wall = Date.UTC(y, mo - 1, d, h, mi, s);
  let guess = wall - 600 * 60000;
  for (let i = 0; i < 2; i += 1) guess = wall - melbourneOffsetMinutes(guess) * 60000;
  return guess;
}

/**
 * Parse a pre-formatted Melbourne string back to a UTC Date. AEST/AEDT carry
 * their own offset; "Melbourne time" / "Melbourne" resolve it from the zone.
 * The bot mapper occasionally emits hour=24 around Melbourne midnight; it is
 * collapsed to 00 the next day.
 */
function parsePreFormattedMelbourne(value: string): Date | null {
  const m = PRE_FORMATTED_MELBOURNE.exec(value.trim());
  if (!m) return null;
  const [, yyyy, mm, dd, hh, mi, ss = '00', tz] = m;
  let y = Number(yyyy);
  let mo = Number(mm);
  let day = Number(dd);
  let hour = Number(hh);
  if (hour === 24) {
    const rolled = new Date(Date.UTC(y, mo - 1, day + 1));
    y = rolled.getUTCFullYear();
    mo = rolled.getUTCMonth() + 1;
    day = rolled.getUTCDate();
    hour = 0;
  }
  let ms: number;
  if (tz === 'AEST' || tz === 'AEDT') {
    const offsetMin = tz === 'AEDT' ? 660 : 600;
    ms = Date.UTC(y, mo - 1, day, hour, Number(mi), Number(ss)) - offsetMin * 60000;
  } else {
    ms = melbourneWallToUtcMs(y, mo, day, hour, Number(mi), Number(ss));
  }
  const parsed = new Date(ms);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** `2026-10-04 19:41:05 Melbourne time` (24h). */
export function formatMelbourneDateTime(input: string | number | Date | null | undefined): string {
  if (typeof input === 'string') {
    const trimmed = input.trim();
    if (!trimmed || trimmed === '-' || trimmed === '—') return '—';
    if (isPreFormattedMelbourne(trimmed)) {
      const reparsed = parsePreFormattedMelbourne(trimmed);
      if (reparsed) return formatMelbourneDateTime(reparsed);
      return trimmed;
    }
  }
  const d = toDate(input);
  if (!d) return '—';
  const p = melbourneParts(d);
  const hour = p.hour === '24' ? '00' : p.hour;
  return `${p.year}-${p.month}-${p.day} ${hour}:${p.minute}:${p.second} ${MELBOURNE_TIME_LABEL}`;
}

/** Audit exports: `2026-10-04 19:41:00 Melbourne time (2026-10-04T08:41:00.000Z)`. */
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
 * Parse a Melbourne-formatted timestamp (`2026-07-31 16:00:00 AEST`,
 * `2026-10-04 19:41:00 Melbourne time`) to ms since epoch. Falls back to
 * `parseTimestampMs` for ISO 8601, epoch, or Date inputs.
 */
export function parseMelbourneTimestampMs(
  input: string | number | Date | null | undefined,
): number | null {
  if (input == null) return null;
  if (typeof input !== 'string') return parseTimestampMs(input);
  const trimmed = input.trim();
  if (!trimmed || trimmed === '-' || trimmed === '—') return null;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(trimmed)) return parseTimestampMs(trimmed);
  if (!isPreFormattedMelbourne(trimmed)) return parseTimestampMs(trimmed);
  const d = parsePreFormattedMelbourne(trimmed);
  return d ? d.getTime() : null;
}

/** Format once — accepts raw ISO/epoch or pre-formatted Melbourne strings from the bot mapper. */
export function displayMelbourneTime(input: string | number | Date | null | undefined): string {
  return formatMelbourneDateTime(input);
}
