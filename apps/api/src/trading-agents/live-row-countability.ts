/**
 * HM B4: website/relay live rows carry `non_countable` for forced closes.
 *
 * A force-flattened or maintenance close is not a strategy outcome. The bot already excludes these
 * from paper stats (services/btc-conservative-agent/paper_pnl_canon.py FORCED_EXIT_REASONS); live
 * rows rendered by the API must carry the same flag so no live/paper stat counts them.
 */

/** Mirrors paper_pnl_canon.FORCED_EXIT_REASONS (spec asserts this is a superset) plus relay-only forced closes. */
export const LIVE_FORCED_EXIT_REASONS: ReadonlySet<string> = new Set([
  'ADMIN_MANUAL_CLOSE',
  'ADMIN_FORCE_FLAT',
  'CIRCUIT_BREAKER_ADMIN_MANUAL',
  'DEPLOY_FORCE_FLAT',
  // Relay-side: the user stopped the relay and it flattened the copy.
  'USER_RELAY_STOP',
]);

/**
 * #852 item 4: the guardian's own non-countable close markers (live_tile_gate.FORCE_FLAT_CLOSE_MARKERS, matched as
 * substrings exactly like force_flat_non_countable). Spec asserts this is a superset of the Python tuple.
 */
export const GUARDIAN_FORCE_FLAT_CLOSE_MARKERS: readonly string[] = [
  'AUTHENTICATED_EMERGENCY_CLOSE',
  'FORCE_FLAT',
  'GUARDIAN_FORCE_FLAT',
  'STOP_FAILURE_FLATTEN',
  'LIQ_CHECK_FLATTEN',
  'LIQ_CLAMP_FLATTEN',
];

/** live_guardian.FILL_TO_STOP_MAX_SEC (HM G6) = live_tile_gate.STOP_RESIDENT_MAX_AGE_S (E6): spec pins both. */
export const LIVE_FILL_TO_STOP_MAX_S = 5;

/** Any reason naming a force-flat or a maintenance close is forced (e.g. ADMIN_PAUSE:DEPLOY_MAINTENANCE). */
const FORCED_PATTERN = /FORCE_FLAT|MAINTENANCE/;

const DISPLAY_PREFIXES = ['SOURCE_CONFIRMED_', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION: ', 'LATE_ENTRY_BETTER_ONLY_CONTINUATION'];

export type LiveRowCountability = { nonCountable: boolean; nonCountableReason: string | null };

/** Strip display prefixes the mapper adds, so `SOURCE_CONFIRMED_DEPLOY_FORCE_FLAT` classifies as forced. */
function canonicalReason(raw: string | null | undefined): string {
  let s = String(raw ?? '').trim().toUpperCase();
  for (const p of DISPLAY_PREFIXES) {
    if (s.startsWith(p)) s = s.slice(p.length).replace(/^:\s*/, '');
  }
  return s;
}

/** First forced reason among the candidates (row exit reason, showcase exit reason, terminal reason). */
export function liveRowCountability(...reasons: Array<string | null | undefined>): LiveRowCountability {
  for (const r of reasons) {
    const s = canonicalReason(r);
    if (!s) continue;
    if (LIVE_FORCED_EXIT_REASONS.has(s) || FORCED_PATTERN.test(s) || GUARDIAN_FORCE_FLAT_CLOSE_MARKERS.some((m) => s.includes(m))) {
      return { nonCountable: true, nonCountableReason: `FORCED_CLOSE:${s}` };
    }
  }
  return { nonCountable: false, nonCountableReason: null };
}

/**
 * #852 item 4: a live row also needs the guardian's stop evidence to count (same rule as live_guardian.evaluate 0b/1):
 * an exchange-authenticated resident reduce-only stop (STOP_LOSS_ARMED, the relay's STOP_CONFIRMED report to Fly)
 * confirmed within LIVE_FILL_TO_STOP_MAX_S of the fill, and no STOP_FAILED (stop_loss_placed === false at fill).
 * Missing, late, failed or unverifiable stop evidence => non-countable (fail closed). Continuous residency while
 * holding (<= 5 s evidence) is enforced live by the guardian, whose flatten then closes the row with a
 * GUARDIAN_FORCE_FLAT_CLOSE_MARKERS reason (non-countable above).
 */
export type LiveRowStopEvidence = { fillAtMs: number | null; stopConfirmedAtMs: number | null; stopFailed: boolean };

const NC = (reason: string): LiveRowCountability => ({ nonCountable: true, nonCountableReason: `NO_STOP_EVIDENCE:${reason}` });
const COUNTABLE: LiveRowCountability = { nonCountable: false, nonCountableReason: null };

export function liveRowStopCountability(ev: LiveRowStopEvidence | null | undefined): LiveRowCountability {
  if (!ev) return NC('UNLINKED');
  if (ev.stopFailed) return NC('STOP_FAILED');
  if (ev.fillAtMs == null || !Number.isFinite(ev.fillAtMs)) return NC('FILL_TIME_UNKNOWN');
  if (ev.stopConfirmedAtMs == null || !Number.isFinite(ev.stopConfirmedAtMs)) return NC('STOP_NOT_CONFIRMED');
  if (ev.stopConfirmedAtMs - ev.fillAtMs > LIVE_FILL_TO_STOP_MAX_S * 1000) return NC('FILL_TO_STOP_GT_5S');
  return COUNTABLE;
}

type EvidenceEvent = { eventType: string; payload: unknown; createdAt: Date };

function payloadMs(payload: unknown, key: string): number | null {
  const raw = payload && typeof payload === 'object' ? (payload as Record<string, unknown>)[key] : undefined;
  const ms = typeof raw === 'number' ? raw : typeof raw === 'string' ? Date.parse(raw) : Number.NaN;
  return Number.isFinite(ms) ? ms : null;
}

/** Same sources as live-copy-fly-reporter (fill_at_ts / stop_confirmed_at_ts / STOP_FAILED) so Fly and the website agree. */
export function liveRowStopEvidenceFromEvents(events: readonly EvidenceEvent[]): LiveRowStopEvidence {
  const filled = events.find((e) => e.eventType === 'FILLED');
  const armed = events.find((e) => e.eventType === 'STOP_LOSS_ARMED');
  const fillAtMs = filled
    ? payloadMs(filled.payload, 'exchange_fill_last_at') ?? payloadMs(filled.payload, 'fill_detected_at') ?? filled.createdAt.getTime()
    : null;
  const stopFailed = events.some((e) => e.eventType === 'FILLED' && e.payload && typeof e.payload === 'object'
    && (e.payload as Record<string, unknown>).stop_loss_placed === false);
  return { fillAtMs, stopConfirmedAtMs: armed ? armed.createdAt.getTime() : null, stopFailed };
}

/** A forced close reason wins; otherwise the stop evidence decides. */
export function liveRowCountabilityWithStop(
  ev: LiveRowStopEvidence | null | undefined,
  ...reasons: Array<string | null | undefined>
): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  return forced.nonCountable ? forced : liveRowStopCountability(ev);
}
