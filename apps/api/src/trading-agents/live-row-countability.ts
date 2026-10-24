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
    if (LIVE_FORCED_EXIT_REASONS.has(s) || FORCED_PATTERN.test(s)) {
      return { nonCountable: true, nonCountableReason: `FORCED_CLOSE:${s}` };
    }
  }
  return { nonCountable: false, nonCountableReason: null };
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

/** Forced close first (B4), else the per-leg check (HM 01:43 item 3). */
export function liveRowCountabilityWithLegs(
  reasons: Array<string | null | undefined>,
  entry: LiveLegStamps,
  exit: LiveLegStamps | null,
  exitSide: LiveExitSide = 'exit',
): LiveRowCountability {
  const forced = liveRowCountability(...reasons);
  if (forced.nonCountable) return forced;
  const legs = liveRowLegCheck(entry, exit, exitSide);
  return { nonCountable: legs.nonCountable, nonCountableReason: legs.nonCountableReason };
}
