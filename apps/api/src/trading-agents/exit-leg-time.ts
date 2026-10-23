/**
 * HM #842 R3 (24 Oct 2026) live-path blocker: exit leg timestamps never throw and never leave their window.
 *
 * A finite > 0 check alone let a us/ns trigger reach `new Date(ms).toISOString()`, which throws RangeError AFTER
 * the Bitfinex close (DB left OPEN while the exchange is flat). Every exit leg time is now bounded to the signed
 * close's own event/receive window; anything outside it is null = unmeasured, so the row is non-countable (R17),
 * never a throw and never an invented value.
 */

/** How far before the signed close event a real exit trigger may be (same 5 min as the signed close max age). */
export const EXIT_TRIGGER_WINDOW_MS = 5 * 60_000;
/** Clock slack between Fly and Railway (same 5 s as TERMINAL_EVIDENCE_CLOCK_SKEW_MS). */
export const EXIT_LEG_CLOCK_SKEW_MS = 5_000;
/** Stamped on the EXIT payload when a trigger was sent but is outside the window (row stays non-countable). */
export const EXIT_TRIGGER_OUT_OF_WINDOW = 'EXIT_TRIGGER_OUT_OF_WINDOW';

/** ISO string for an epoch-ms value, or null. Never throws (us/ns/NaN/negative/out-of-range all -> null). */
export function safeIsoMs(ms?: number | null): string | null {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return null;
  const d = new Date(ms);
  if (!Number.isFinite(d.getTime())) return null;
  try {
    return d.toISOString();
  } catch {
    return null;
  }
}

/**
 * An exit leg time (epoch ms) bounded to the signed close's window:
 * [min(source event, platform receive) - EXIT_TRIGGER_WINDOW_MS, platform receive + EXIT_LEG_CLOCK_SKEW_MS].
 * No platform receive -> no window -> null (unmeasured). Out of window / not representable -> null.
 */
export function boundedExitLegMs(
  legMs: unknown,
  window: { sourceEventAtMs?: number | null; platformReceivedAtMs?: number | null },
): number | null {
  if (typeof legMs !== 'number' || !Number.isFinite(legMs) || legMs <= 0) return null;
  const rx = window.platformReceivedAtMs;
  if (typeof rx !== 'number' || !Number.isFinite(rx) || rx <= 0) return null;
  const ev = typeof window.sourceEventAtMs === 'number' && Number.isFinite(window.sourceEventAtMs)
    ? window.sourceEventAtMs
    : rx;
  const lo = Math.min(ev, rx) - EXIT_TRIGGER_WINDOW_MS;
  const hi = rx + EXIT_LEG_CLOCK_SKEW_MS;
  if (legMs < lo || legMs > hi) return null;
  return safeIsoMs(legMs) ? legMs : null;
}
