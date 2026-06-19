/** Platform-enforced max collateral (margin) per hire/signal trade — admin can raise via PlatformSettings. */
export const DEFAULT_SUBSCRIBER_MAX_MARGIN_USD = 20;

/** Matches showcase bot DEFAULT_RESEARCH_LEVERAGE (100x on Bitfinex derivatives). */
export const DEFAULT_SUBSCRIBER_LEVERAGE = 100;

/** Default API poll interval for subscriber copy execution (ms). Override via SUBSCRIBER_EXECUTION_POLL_MS. */
export const DEFAULT_SUBSCRIBER_EXECUTION_POLL_MS = 250;

/** Default poll interval for bot → signal cycle bridge (ms). Override via SIGNAL_CYCLE_POLL_MS. */
export const DEFAULT_SIGNAL_CYCLE_POLL_MS = 250;

/** Minimum allowed poll interval (ms) — 100ms for instant showcase relay wake. */
export const MIN_SUBSCRIBER_POLL_MS = 100;

export function resolveSubscriberMaxMarginUsd(input?: {
  envValue?: string | number | null;
  platformValue?: number | null;
}): number {
  const fromPlatform = input?.platformValue;
  if (fromPlatform != null && Number.isFinite(fromPlatform) && fromPlatform > 0) {
    return fromPlatform;
  }
  const raw = Number(
    input?.envValue ?? process.env.SUBSCRIBER_MAX_MARGIN_USD ?? DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  );
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_SUBSCRIBER_MAX_MARGIN_USD;
}

export function resolveSubscriberExecutionPollMs(envValue?: string | number | null): number {
  const raw = Number(envValue ?? process.env.SUBSCRIBER_EXECUTION_POLL_MS ?? DEFAULT_SUBSCRIBER_EXECUTION_POLL_MS);
  return Number.isFinite(raw) && raw >= MIN_SUBSCRIBER_POLL_MS ? raw : DEFAULT_SUBSCRIBER_EXECUTION_POLL_MS;
}

export function resolveSignalCyclePollMs(envValue?: string | number | null): number {
  const raw = Number(envValue ?? process.env.SIGNAL_CYCLE_POLL_MS ?? DEFAULT_SIGNAL_CYCLE_POLL_MS);
  return Number.isFinite(raw) && raw >= MIN_SUBSCRIBER_POLL_MS ? raw : DEFAULT_SIGNAL_CYCLE_POLL_MS;
}

/** Exchange-neutral signal envelope (ENSE) — portable across venues. */
export type SignalEntryMode = 'PULLBACK_PCT' | 'EMA_OFFSET_PCT';

export type SignalIntentEnvelope = {
  schema: 'dcf-signal-intent/v1';
  cycleId: string;
  signalId: string;
  version: string;
  action: 'ENTER';
  direction: 'LONG' | 'SHORT';
  entry: {
    type: 'LIMIT';
    mode: SignalEntryMode;
    /** Signed % from subscriber mark at receipt (LONG: negative = below mark). */
    offset_pct: number;
    reference: 'SUBSCRIBER_MARK_AT_RECEIPT';
    ttl_sec: number;
  };
  risk: {
    /** Mandatory exchange stop — margin % from entry fill (e.g. -18). */
    stop_loss_margin_pct: number;
    take_profit_ladder: Array<{ at_margin_pct: number; close_position_pct: number }>;
    leverage_hint: number;
    /** Platform-enforced max collateral USD per trade (hire relay cannot exceed this). */
    max_margin_usd: number;
  };
  context: {
    regime: string;
    edge: number;
    ai_win_prob: number;
    entry_mode_source: string;
    research_venue: string;
    disclaimer: string;
  };
};

export type SignalCycleEventType =
  | 'ORDER_PLACED'
  | 'FILLED'
  | 'STOP_LOSS_ARMED'
  | 'UPDATE_STOPS'
  | 'EXIT'
  | 'EXPIRED';

export const SIGNAL_SUCCESS_FEE_PCT = 0.1;
export const SIGNAL_MIN_FEE_USD = 0.2;
export const SIGNAL_MIN_CHARGE_USD = 0.1;

/** Shown on mandate, docs, and settlement responses — not investment advice. */
export const SIGNAL_LEGAL_DISCLAIMER =
  'Signals are informational only, not investment advice or a solicitation. You execute all trades on your own exchange account and bear full risk. Past showcase performance does not guarantee future results. Success fees apply only to reported profitable closes per the subscriber API contract.';

/** Success fee: 10% of profit; $0 if loss; waive if 10% < $0.20; else max(10%, raw). */
export function computeSignalSuccessFeeUsd(netProfitUsd: number): number {
  if (!Number.isFinite(netProfitUsd) || netProfitUsd <= 0) return 0;
  const raw = netProfitUsd * SIGNAL_SUCCESS_FEE_PCT;
  if (raw < SIGNAL_MIN_FEE_USD) return 0;
  return Math.max(SIGNAL_MIN_CHARGE_USD, Math.round(raw * 100) / 100);
}

export const SIGNAL_SUBSCRIBER_MANDATE = {
  stopLossAtFill: true,
  useLocalMark: true,
  noAbsolutePricesFromResearchVenue: true,
} as const;
