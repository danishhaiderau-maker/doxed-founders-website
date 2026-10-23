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

/**
 * HM R4 B1 + RK 08:03 (R4 one helper, R17): THE exit-price provenance of a closed live row, shared by the Fly reporter
 * (D3 loss caps on Fly read close.price/close.price_source) and the countability rule below. Only a REAL Bitfinex fill
 * counts: `exchange_fill` = the authenticated close-order trades (resolveNormalCloseExchangeEvidence:
 * exit_exchange_fill_price) and `stop_fill` = the resident stop's own order trades at the exact lot qty
 * (pnl_source bitfinex_order_trades + EXCHANGE_STOP + exchange_exit_fill_ids). Everything else (ticker/mark, entry-price
 * fallback, failed or budget-refused fill read, partial, non-USD fee => no evidence) has price null: D3 UNREADABLE
 * (AMBER entry block on Fly) and the row non-countable. Never a $0 loss.
 */
export type LiveExitPriceSource = 'exchange_fill' | 'stop_fill' | 'mark' | 'entry_fallback' | 'none';
export const LIVE_REAL_EXIT_FILL_SOURCES: ReadonlySet<LiveExitPriceSource> = new Set(['exchange_fill', 'stop_fill']);
export type LiveExitFillEvidence = { price: number | null; source: LiveExitPriceSource; reportedPrice: number | null };

const finitePos = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : Number.NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
};

export function liveExitFillEvidence(exitPayload: unknown, entryFillPrice?: unknown): LiveExitFillEvidence {
  const pl = exitPayload && typeof exitPayload === 'object' ? (exitPayload as Record<string, unknown>) : {};
  const reported = finitePos(pl.exit_price);
  const fill = finitePos(pl.exit_exchange_fill_price);
  if (fill != null) return { price: fill, source: 'exchange_fill', reportedPrice: reported ?? fill };
  const stopIds = Array.isArray(pl.exchange_exit_fill_ids) ? pl.exchange_exit_fill_ids : [];
  if (reported != null && String(pl.pnl_source ?? '') === 'bitfinex_order_trades'
      && String(pl.exit_reason ?? '').toUpperCase() === 'EXCHANGE_STOP' && stopIds.length > 0
      && finitePos(pl.exchange_exit_filled_qty) != null) {
    return { price: reported, source: 'stop_fill', reportedPrice: reported };
  }
  if (reported == null) return { price: null, source: 'none', reportedPrice: null };
  const entry = finitePos(entryFillPrice ?? pl.entry_exchange_fill_price ?? pl.fill_price);
  return { price: null, source: entry != null && Math.abs(entry - reported) < 1e-9 ? 'entry_fallback' : 'mark', reportedPrice: reported };
}

/** The latest EXIT event of a participant, with the FILLED price as the entry reference. */
export function liveExitFillEvidenceFromEvents(events: readonly { eventType: string; payload: unknown }[]): LiveExitFillEvidence {
  const exit = [...events].reverse().find((e) => e.eventType === 'EXIT');
  const filled = events.find((e) => e.eventType === 'FILLED');
  const fp = filled && filled.payload && typeof filled.payload === 'object' ? (filled.payload as Record<string, unknown>) : {};
  return liveExitFillEvidence(exit?.payload, fp.exchange_fill_price ?? fp.fill_price);
}

/** R17: a closed row whose exit price is not a real Bitfinex fill is non-countable. */
export function liveRowExitFillCountability(ev: LiveExitFillEvidence | null | undefined): LiveRowCountability {
  if (ev && LIVE_REAL_EXIT_FILL_SOURCES.has(ev.source) && ev.price != null) return COUNTABLE;
  return { nonCountable: true, nonCountableReason: `EXIT_FILL_UNREADABLE:${ev?.source ?? 'none'}` };
}

/** HM 02:55 blocker 4 (R17): a live row with no leg stamps to check (e.g. a Bitfinex cash-ledger-only close) is
 * never countable: FORCED_CLOSE when forced, else LIVE_LEGS_UNCHECKED. */
export const LIVE_LEGS_UNCHECKED = 'LIVE_LEGS_UNCHECKED';
export function liveRowUncheckedCountability(...reasons: Array<string | null | undefined>): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  return forced.nonCountable ? forced : { nonCountable: true, nonCountableReason: LIVE_LEGS_UNCHECKED };
}

/**
 * HM 01:43 item 3 (R17): per-leg check of a LIVE row. Each side (entry; exit when closed by a Fly-sent order)
 * needs the four stamps signal -> Fly send -> relay (Railway) receive -> exchange ack/fill; each stamp ends one
 * leg and starts the next, so no interval is counted twice. A missing stamp or a leg that ends before it starts
 * (beyond the clock slack) makes the row non-countable. Mirrors services/btc-conservative-agent/live_ack_pool.py
 * LIVE_LEGS / live_legs (test_live_vs_paper.py asserts every name and key below appears here).
 */
export const LIVE_LEGS = {
  entry: [
    ['signal', ['fly_signal_at_ts']],
    ['fly_send', ['fly_intent_emitted_at_ts']],
    ['relay_receive', ['railway_received_at_ts']],
    ['ack_fill', ['exchange_ack_at_ts', 'fill_at_ts']],
  ],
  exit: [
    ['signal', ['exit_signal_at_ts']],
    ['fly_send', ['fly_send_started_at_ts']],
    ['relay_receive', ['railway_received_at_ts']],
    ['ack_fill', ['exit_ack_at_ts', 'exit_confirmed_at_ts']],
  ],
  // RK (1) 24 Oct: exchange-resident stop exit: no Fly send leg; trigger = the real trade print that crossed the
  // stop, fill = Bitfinex's own fill record. Both required (missing -> non-countable).
  exit_resident: [
    ['signal', ['exit_trigger_print_at_ts']],
    ['ack_fill', ['exit_fill_record_at_ts']],
  ],
} as const;

export type LiveExitSide = 'exit' | 'exit_resident';

export const LIVE_LEG_CLOCK_SLACK_S = 0.05;

/** Epoch-second stamps of one side, keyed like the live chain / Fly outbox record. */
export type LiveLegStamps = Record<string, number | null | undefined>;

export type LiveLegCheck = LiveRowCountability & { legReasons: string[] };

const posNum = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null);

/** `exit` null = no exit legs required (row still open). `exitSide` picks the bot-sent or exchange-resident legs. */
export function liveRowLegCheck(
  entry: LiveLegStamps,
  exit: LiveLegStamps | null,
  exitSide: LiveExitSide = 'exit',
): LiveLegCheck {
  const reasons: string[] = [];
  const sides: Array<['entry' | LiveExitSide, LiveLegStamps]> = [['entry', entry ?? {}]];
  if (exit) sides.push([exitSide, exit]);
  for (const [side, src] of sides) {
    const spec: ReadonlyArray<readonly [string, ReadonlyArray<string>]> = LIVE_LEGS[side];
    const stamps = spec.map(([name, keys]) => {
      const v = keys.map((k) => posNum(src[k])).find((x) => x != null) ?? null;
      if (v == null) reasons.push(`LIVE_LEG_MISSING:${side}.${name}`);
      return [name, v] as const;
    });
    for (let i = 0; i + 1 < stamps.length; i++) {
      const [n0, v0] = stamps[i];
      const [n1, v1] = stamps[i + 1];
      if (v0 != null && v1 != null && v1 - v0 < -LIVE_LEG_CLOCK_SLACK_S) reasons.push(`LIVE_LEG_ORDER:${side}.${n0}->${n1}`);
    }
  }
  return { nonCountable: reasons.length > 0, nonCountableReason: reasons[0] ?? null, legReasons: reasons };
}

/**
 * THE live-row countability rule (one rule for every live row: #842 HM B4 + HM 01:43 item 3 + #852 item 4, merged in #852 B1):
 *   1. a forced close reason => FORCED_CLOSE (B4);
 *   2. the guardian's stop evidence for a FILLED row (#852 item 4: exchange-resident stop confirmed <= 5 s after the
 *      fill, no STOP_FAILED). `stopEvidence === undefined` means the row never filled (no stop to require); `null`
 *      means filled but unlinked => NO_STOP_EVIDENCE:UNLINKED (fail closed);
 *   3. the per-leg check (HM 01:43 item 3);
 *   4. a closed row's exit price must be a real Bitfinex fill (liveExitFillEvidence; HM R4 B1, RK 08:03, R17).
 * First failure wins; rows with no leg stamps at all use liveRowUncheckedCountability (always non-countable).
 */
export function liveRowCountabilityWithLegs(
  reasons: Array<string | null | undefined>,
  entry: LiveLegStamps,
  exit: LiveLegStamps | null,
  exitSide: LiveExitSide = 'exit',
  stopEvidence?: LiveRowStopEvidence | null,
  exitFill?: LiveExitFillEvidence | null,
): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  if (forced.nonCountable) return forced;
  if (stopEvidence !== undefined) {
    const stop = liveRowStopCountability(stopEvidence);
    if (stop.nonCountable) return stop;
  }
  const legs = liveRowLegCheck(entry, exit, exitSide);
  if (legs.nonCountable) return { nonCountable: true, nonCountableReason: legs.nonCountableReason };
  // HM R4 B1 / RK 08:03 (R17): a closed row counts only with a REAL Bitfinex exit fill (shared liveExitFillEvidence).
  if (exit && exitFill !== undefined) return liveRowExitFillCountability(exitFill);
  return COUNTABLE;
}
