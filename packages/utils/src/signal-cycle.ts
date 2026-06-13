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
