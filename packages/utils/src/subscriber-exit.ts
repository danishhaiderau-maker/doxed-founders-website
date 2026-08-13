/**
 * Scenario C profit-lock ladder — canonical source of truth for the relay.
 * Mirrors live showcase bot `TRAIL_LADDER_SCENARIO_C`
 * (services/btc-conservative-agent/scenario_c_config.py — SCENARIO_C_PROFILE_ID
 * "SCENARIO_C_RUNNER_4_v7_20260813"). Synced 2026-08-13 to the live bot:
 * early 4→2 and 5→3 rungs protect small winners before the established 8→5
 * ladder. The relay must embed this exact snapshot in every new copied intent;
 * otherwise Bitfinex and Showcase can follow different exit rules.
 *
 * Each tuple is `[peak_margin_pct_trigger, protected_margin_pct_floor]`. When
 * peak unrealized margin % crosses `trigger`, the protective stop advances
 * so that ~`protected`% of peak margin is locked in.
 */
export const SCENARIO_C_LADDER: ReadonlyArray<readonly [number, number]> = [
  [4, 2],
  [5, 3],
  [8, 5],
  [12, 10],
  [19, 17],
  [40, 28],
  [60, 45],
  [80, 60],
  [100, 75],
  [150, 120],
];

/**
 * @deprecated Use {@link SCENARIO_C_LADDER} — kept under the legacy name so
 * existing call sites and persisted event references continue to resolve.
 */
export const SUBSCRIBER_TRAIL_LADDER: ReadonlyArray<readonly [number, number]> = SCENARIO_C_LADDER;

/**
 * Solve the active Scenario C rung for a peak-margin % value.
 *
 * Returns the index into {@link SCENARIO_C_LADDER} (0-based) of the highest
 * rung whose trigger has been reached, or `null` when peak is below the
 * first trigger (no profit-lock stop placed yet). Pure function so it can be
 * unit-tested without touching the exchange.
 *
 *   solveScenarioCRung(0)    → null
 *   solveScenarioCRung(7.99) → null
 *   solveScenarioCRung(8)    → 0
 *   solveScenarioCRung(11)   → 0  (only crossed first trigger)
 *   solveScenarioCRung(12)   → 1
 *   solveScenarioCRung(150)  → 7
 *   solveScenarioCRung(200)  → 7  (highest)
 */
export function solveScenarioCRung(
  peakMarginPct: number,
  ladder: ReadonlyArray<readonly [number, number]> = SCENARIO_C_LADDER,
): number | null {
  if (!Number.isFinite(peakMarginPct) || peakMarginPct < ladder[0][0]) return null;
  let rung: number | null = null;
  for (let i = 0; i < ladder.length; i++) {
    if (peakMarginPct >= ladder[i][0]) rung = i;
  }
  return rung;
}

export const SUBSCRIBER_PEAK_NEVER_LOSER_MIN_PEAK = 40;
export const SUBSCRIBER_PEAK_NEVER_LOSER_FLOOR = 10;

/** Max signed entry offset % from mark for hire copy (pullback / EMA hybrid). */
export const SUBSCRIBER_MAX_ENTRY_OFFSET_PCT = 3;

/** Reject limit prices farther than this % from current mark. */
export const SUBSCRIBER_MAX_LIMIT_DEVIATION_PCT = 8;

const DEFAULT_PULLBACK_FRACTION = 0.002;

/**
 * Convert bot pullback inputs to signed offset % for computeLimitFromMark.
 * Bot stores pullback_threshold as a fraction (0.001 = 0.1%). signal_info.pullback_pct
 * is mislabeled (copies pull_req) — prefer bot.pullback_threshold when present.
 */
export function normalizePullbackToOffsetPct(
  direction: 'LONG' | 'SHORT',
  opts?: {
    botPullbackThreshold?: number | null;
    signalPullback?: number | null;
    signalPullReq?: number | null;
  },
): number {
  const threshold = opts?.botPullbackThreshold;
  if (
    typeof threshold === 'number' &&
    Number.isFinite(threshold) &&
    threshold > 0 &&
    threshold <= 0.02
  ) {
    return signedOffsetFromFraction(threshold, direction);
  }

  const raw = pickPositivePullback(opts?.signalPullReq, opts?.signalPullback);
  const fraction = rawToPullbackFraction(raw);
  return signedOffsetFromFraction(fraction, direction);
}

function pickPositivePullback(...values: Array<number | null | undefined>): number | null {
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

function rawToPullbackFraction(raw: number | null): number {
  if (raw == null) return DEFAULT_PULLBACK_FRACTION;

  // Absolute BTC prices leaked into pullback fields (e.g. 64000 or 904).
  if (raw >= 500 || raw <= 0) return DEFAULT_PULLBACK_FRACTION;

  let fraction: number;
  if (raw > 1) {
    // Percent points: 0.2 (%), 2 (%), or 20 (%)
    fraction = raw / 100;
  } else if (raw > 0.02) {
    // Ambiguous 0.2 — treat as 0.2% not 20%
    fraction = raw / 100;
  } else {
    fraction = raw;
  }

  return Math.min(Math.max(fraction, 0.0001), SUBSCRIBER_MAX_ENTRY_OFFSET_PCT / 100);
}

function signedOffsetFromFraction(fraction: number, direction: 'LONG' | 'SHORT'): number {
  const offsetPct = Math.round(fraction * 100 * 10000) / 10000;
  const clamped = Math.min(offsetPct, SUBSCRIBER_MAX_ENTRY_OFFSET_PCT);
  return direction === 'LONG' ? -clamped : clamped;
}

export function computeLimitFromMark(mark: number, offsetPct: number): number {
  if (!Number.isFinite(mark) || mark <= 0) throw new Error('Invalid mark price');
  return mark * (1 + offsetPct / 100);
}

/** Returns null when limit is implausible vs mark (e.g. $904 on $64k BTC). */
export function sanitizeLimitPrice(
  mark: number,
  limitPrice: number,
  direction: 'LONG' | 'SHORT',
): number | null {
  if (!Number.isFinite(mark) || mark <= 0 || !Number.isFinite(limitPrice) || limitPrice <= 0) {
    return null;
  }

  const deviationPct = Math.abs((limitPrice - mark) / mark) * 100;
  if (deviationPct > SUBSCRIBER_MAX_LIMIT_DEVIATION_PCT) return null;

  if (direction === 'LONG' && limitPrice > mark * 1.001) return null;
  if (direction === 'SHORT' && limitPrice < mark * 0.999) return null;

  return Math.round(limitPrice * 100) / 100;
}

export function getProfitLockFloor(
  peakMarginPct: number,
  ladder: ReadonlyArray<readonly [number, number]> = SUBSCRIBER_TRAIL_LADDER,
): number | null {
  if (!Number.isFinite(peakMarginPct) || peakMarginPct < ladder[0][0]) return null;

  let floor: number | null = null;
  for (const [trigger, lock] of ladder) {
    if (peakMarginPct >= trigger) floor = lock;
  }

  if (peakMarginPct >= SUBSCRIBER_PEAK_NEVER_LOSER_MIN_PEAK) {
    floor = Math.max(floor ?? 0, SUBSCRIBER_PEAK_NEVER_LOSER_FLOOR);
  }

  return floor;
}

export function computeStopPrice(
  fill: number,
  direction: 'LONG' | 'SHORT',
  stopLossMarginPct: number,
  leverage: number,
): number {
  const distance = Math.abs(stopLossMarginPct) / (100 * Math.max(leverage, 1));
  if (direction === 'LONG') return fill * (1 - distance);
  return fill * (1 + distance);
}

/** Trailing profit-lock stop from margin % floor (tightens only). */
export function computeProfitLockStopPrice(
  fill: number,
  direction: 'LONG' | 'SHORT',
  lockFloorMarginPct: number,
  leverage: number,
): number {
  const distance = Math.abs(lockFloorMarginPct) / (100 * Math.max(leverage, 1));
  if (direction === 'LONG') return fill * (1 + distance);
  return fill * (1 - distance);
}

export function computeQty(marginUsd: number, leverage: number, price: number, minQty = 0.00004): number {
  const notional = marginUsd * leverage;
  const raw = notional / price;
  return Math.max(minQty, Math.floor(raw * 1e5) / 1e5);
}

/** Mirrors showcase bot LIMIT_CHASE_MIN_BUFFER_USD / MICRO_SR_ENTRY_BUFFER_USD. */
export const SUBSCRIBER_CHASE_MIN_BUFFER_USD = 15;
export const SUBSCRIBER_CHASE_NEAR_FILL_USD = 10;
export const SUBSCRIBER_CHASE_MAX_GAP_CLOSE_PCT = 0.9;
export const SUBSCRIBER_CHASE_STEP_PCT = 0.25;
/** Autonomous chase when no bot limit anchor (legacy). */
export const SUBSCRIBER_CHASE_INTERVAL_MS = 60_000;
/** Bot-anchored limit sync — match showcase limit moves within one poll tick. */
export const SUBSCRIBER_SHOWCASE_ANCHOR_CHASE_MS = 250;
/** Near-fill zone: chase every tick to mirror bot limit moves. */
export const SUBSCRIBER_CHASE_NEAR_FILL_INTERVAL_MS = 250;
/** Scenario C thesis fast-cut (margin %) — bot THESIS_FAST_EXIT_UNREAL_PCT. */
export const SUBSCRIBER_THESIS_FAST_EXIT_MARGIN_PCT = -12;
/** Skip thesis fast-cut when peak ever reached this margin % — bot THESIS_MFE_PROTECT_PCT.
 * Synced 2026-08-08 to bot.py:6348 THESIS_MFE_PROTECT_PCT = 5.0 (Stage 1 Fix #5,
 * 2026-08-06). A +2% MFE spike is chop noise (3 recent losers spiked +2% early as
 * fakeouts then bled to -$6); +5% is a real move so the fast-cut fires on real losers. */
export const SUBSCRIBER_THESIS_MFE_PROTECT_MARGIN_PCT = 5;
/** Default hard stop margin % — bot stop_loss path. */
/** User-approved real-copy disaster stop. Keep in sync with emitted Showcase intents. */
export const SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT = -13;

/**
 * Virtual lots on merged Bitfinex BTC-PERP (same direction only).
 * Live copy cap follows showcase dashboard max_active_signals (default 3).
 */
export const SUBSCRIBER_MAX_CONCURRENT_SIGNALS_DEFAULT = 3;
export const SUBSCRIBER_MAX_CONCURRENT_SIGNALS_CAP = 20;
/** @deprecated Use resolveMaxConcurrentCopySignals — kept for legacy imports */
export const SUBSCRIBER_MAX_OPEN_COPY_LEGS = SUBSCRIBER_MAX_CONCURRENT_SIGNALS_CAP;
/** @deprecated Use resolveMaxConcurrentCopySignals — kept for legacy imports */
export const SUBSCRIBER_MAX_PENDING_COPY_LEGS = SUBSCRIBER_MAX_CONCURRENT_SIGNALS_CAP;

/** Mirrors bot get_effective_max_active_signals() — dashboard value is authoritative. */
export function resolveMaxConcurrentCopySignals(opts?: {
  botMaxActiveSignals?: number | string | null;
  envOverride?: string | null;
}): number {
  const envRaw = opts?.envOverride?.trim();
  if (envRaw) {
    const env = Number.parseInt(envRaw, 10);
    if (Number.isFinite(env) && env >= 1) {
      return Math.min(SUBSCRIBER_MAX_CONCURRENT_SIGNALS_CAP, Math.floor(env));
    }
  }
  const botRaw = opts?.botMaxActiveSignals;
  const bot =
    typeof botRaw === 'number'
      ? botRaw
      : typeof botRaw === 'string'
        ? Number.parseInt(botRaw, 10)
        : NaN;
  if (Number.isFinite(bot) && bot >= 1) {
    return Math.min(SUBSCRIBER_MAX_CONCURRENT_SIGNALS_CAP, Math.floor(bot));
  }
  return SUBSCRIBER_MAX_CONCURRENT_SIGNALS_DEFAULT;
}

export type VirtualLotExitReason = 'PROFIT_LOCK' | 'THESIS_FAST_CUT' | 'HARD_STOP' | null;

/** Subscriber lot exit — defers thesis/profit-lock when showcase-mirror-only (default). */
export function evaluateSubscriberLotExit(opts: {
  unrealMarginPct: number;
  peakMarginPct: number;
  stopLossMarginPct?: number;
  showcaseMirrorOnly?: boolean;
}): { reason: VirtualLotExitReason; lockFloor?: number } {
  const mirrorOnly = opts.showcaseMirrorOnly ?? true;
  if (!mirrorOnly) return evaluateScenarioCLotExit(opts);
  // Mirror mode: showcase closure + exchange disaster stop only — no local thesis/profit-lock/hard-stop.
  return { reason: null };
}

/**
 * Real-side protective safety net for an OPEN Bitfinex copy lot.
 *
 * Context: in showcase-mirror-only mode (the default, controlled by
 * SUBSCRIBER_SHOWCASE_MIRROR_ONLY), {@link evaluateSubscriberLotExit} returns
 * `null` and the relay relies entirely on showcase closure events + the wide
 * MIRROR_DISASTER_STOP_MARGIN_PCT exchange stop to manage exits. That design
 * assumes paper and real stay in sync. When they desync (a missed maker fill
 * on the copy that nonetheless left a real position, or a fill on a different
 * signal than the one tracked by the copy's cycle), the showcase never emits
 * the matching POSITION_CLOSED and the real position rots unmanaged.
 *
 * This helper is the independent safety net. It runs the SAME Scenario C
 * math the showcase bot uses (THESIS_FAST_CUT / PROFIT_LOCK ladder / HARD_STOP)
 * against the REAL fill price + REAL mark, independent of mirror mode. When it
 * returns a non-null reason, the executor market-closes the real lot through
 * the existing closeVirtualLot machinery — even if no showcase exit ever
 * arrives. It is the last line of defense for real money.
 *
 * The helper is pure / deterministic so it can be unit-tested without touching
 * the exchange. It never reads env vars directly — the executor resolves all
 * policy inputs (stop-loss margin %, MFE-protect threshold) and passes them in,
 * so the safety net is testable and its behaviour is fully captured by the
 * call-site arguments.
 *
 * Default policy mirrors the showcase bot constants
 * (THESIS_FAST_EXIT_UNREAL_PCT=-12, THESIS_MFE_PROTECT_PCT=+5 margin,
 * SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT=-13, SCENARIO_C_LADDER). Callers can
 * override any of these (e.g. a tighter hard stop for a conservative cap)
 * without changing the showcase strategy itself.
 */
export function evaluateRealSideSafetyNetExit(opts: {
  unrealMarginPct: number;
  peakMarginPct: number;
  /** Hard stop margin % (negative). Default -13 (SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT). */
  hardStopMarginPct?: number;
  /** Thesis fast-cut trigger (negative margin %). Default -12. */
  thesisFastCutMarginPct?: number;
  /** MFE protect threshold (positive margin %). Skip fast-cut once peak exceeded this. Default +5. */
  thesisMfeProtectMarginPct?: number;
  /** Profit-lock ladder. Default SCENARIO_C_LADDER. */
  ladder?: ReadonlyArray<readonly [number, number]>;
}): { reason: VirtualLotExitReason; lockFloor?: number } {
  const ladder = opts.ladder ?? SCENARIO_C_LADDER;
  const hardStopMarginPct = opts.hardStopMarginPct ?? SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT;
  const thesisFastCutMarginPct =
    opts.thesisFastCutMarginPct ?? SUBSCRIBER_THESIS_FAST_EXIT_MARGIN_PCT;
  const thesisMfeProtectMarginPct =
    opts.thesisMfeProtectMarginPct ?? SUBSCRIBER_THESIS_MFE_PROTECT_MARGIN_PCT;

  const { unrealMarginPct, peakMarginPct } = opts;

  // 1. Profit-lock rung — once peak crossed a ladder trigger, the protective
  //    stop advances so the protected % of peak is locked in. The executor
  //    keeps the exchange stop synced (Option A dynamic stops) AND, if price
  //    reverses through the rung floor, this branch fires the market close.
  const lockFloor = getProfitLockFloor(peakMarginPct, ladder);
  if (
    lockFloor != null &&
    peakMarginPct >= ladder[0][0] &&
    unrealMarginPct <= lockFloor
  ) {
    return { reason: 'PROFIT_LOCK', lockFloor };
  }

  // 2. Thesis fast-cut — deep adverse move before any meaningful MFE. The
  //    showcase bot's THESIS_FAST_EXIT_UNREAL_PCT path. Skip once peak ever
  //    reached the MFE protect threshold (the trade "proved" itself; let the
  //    hard stop / profit-lock rungs own the exit instead).
  if (
    unrealMarginPct <= thesisFastCutMarginPct &&
    peakMarginPct < thesisMfeProtectMarginPct
  ) {
    return { reason: 'THESIS_FAST_CUT' };
  }

  // 3. Hard stop — the absolute floor, independent of MFE / ladder.
  if (unrealMarginPct <= hardStopMarginPct) {
    return { reason: 'HARD_STOP' };
  }

  return { reason: null };
}

/** Scenario C exit evaluation per virtual lot (margin %, same formula as showcase bot). */
export function evaluateScenarioCLotExit(opts: {
  unrealMarginPct: number;
  peakMarginPct: number;
  stopLossMarginPct?: number;
}): { reason: VirtualLotExitReason; lockFloor?: number } {
  const stopLossMarginPct = opts.stopLossMarginPct ?? SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT;
  const { unrealMarginPct, peakMarginPct } = opts;

  const lockFloor = getProfitLockFloor(peakMarginPct);
  if (
    lockFloor != null &&
    peakMarginPct >= SUBSCRIBER_TRAIL_LADDER[0][0] &&
    unrealMarginPct <= lockFloor
  ) {
    return { reason: 'PROFIT_LOCK', lockFloor };
  }

  if (
    unrealMarginPct <= SUBSCRIBER_THESIS_FAST_EXIT_MARGIN_PCT &&
    peakMarginPct < SUBSCRIBER_THESIS_MFE_PROTECT_MARGIN_PCT
  ) {
    return { reason: 'THESIS_FAST_CUT' };
  }

  if (unrealMarginPct <= stopLossMarginPct) {
    return { reason: 'HARD_STOP' };
  }

  return { reason: null };
}

export function computeUnrealizedMarginPct(
  fillPrice: number,
  markPrice: number,
  direction: 'LONG' | 'SHORT',
  leverage: number,
): number {
  if (!Number.isFinite(fillPrice) || fillPrice <= 0 || !Number.isFinite(markPrice) || markPrice <= 0) {
    return 0;
  }
  const dirFactor = direction === 'LONG' ? 1 : -1;
  const priceMove = ((markPrice - fillPrice) / fillPrice) * dirFactor;
  return priceMove * Math.max(leverage, 1) * 100;
}

/** Signed distance from limit to market (always >= 0 when limit is on correct side). */
export function limitChaseMarketGap(
  direction: 'LONG' | 'SHORT',
  limitPrice: number,
  marketPrice: number,
): number {
  if (direction === 'LONG') return Math.max(0, marketPrice - limitPrice);
  return Math.max(0, limitPrice - marketPrice);
}

/**
 * Nudge resting entry toward market — mirrors bot _compute_limit_chase_target.
 * SHORT: limit above mark, chase moves limit down. LONG: limit below mark, chase moves up.
 */
export function computeLimitChaseTarget(
  direction: 'LONG' | 'SHORT',
  currentLimit: number,
  marketPrice: number,
  originalLimit: number,
  stepPct = SUBSCRIBER_CHASE_STEP_PCT,
): { newLimit: number; reason: string } {
  const curGap = limitChaseMarketGap(direction, currentLimit, marketPrice);
  if (curGap <= 0) return { newLimit: currentLimit, reason: 'NO_GAP' };

  const origGap = limitChaseMarketGap(direction, originalLimit, marketPrice);
  if (origGap <= 0) return { newLimit: currentLimit, reason: 'NO_ORIG_GAP' };

  const closedPct = 1 - curGap / origGap;
  if (closedPct >= SUBSCRIBER_CHASE_MAX_GAP_CLOSE_PCT) {
    return { newLimit: currentLimit, reason: 'MAX_CHASE_REACHED' };
  }

  const step = stepPct * curGap;
  const buffer = SUBSCRIBER_CHASE_MIN_BUFFER_USD;
  let newLimit: number;
  if (direction === 'LONG') {
    newLimit = Math.min(currentLimit + step, marketPrice - buffer);
    newLimit = Math.max(newLimit, currentLimit);
  } else {
    newLimit = Math.max(currentLimit - step, marketPrice + buffer);
    newLimit = Math.min(newLimit, currentLimit);
  }

  if (Math.abs(newLimit - currentLimit) < 0.01) {
    return { newLimit: currentLimit, reason: 'NO_MOVE' };
  }
  return { newLimit: Math.round(newLimit * 100) / 100, reason: 'LIMIT_CHASE' };
}

export function isNearChaseFillZone(
  direction: 'LONG' | 'SHORT',
  limitPrice: number,
  marketPrice: number,
): boolean {
  return limitChaseMarketGap(direction, limitPrice, marketPrice) <= SUBSCRIBER_CHASE_NEAR_FILL_USD;
}

/** @deprecated Use computeLimitChaseTarget — kept for callers migrating gradually. */
export function computeChaseLimitPrice(
  mark: number,
  currentLimit: number,
  direction: 'LONG' | 'SHORT',
  stepPct = SUBSCRIBER_CHASE_STEP_PCT,
  originalLimit?: number,
): number {
  const { newLimit } = computeLimitChaseTarget(
    direction,
    currentLimit,
    mark,
    originalLimit ?? currentLimit,
    stepPct,
  );
  return newLimit;
}
