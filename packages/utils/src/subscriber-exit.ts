/** Mirrors live showcase bot TRAIL_LADDER_SCENARIO_C (v1.1.21). */
export const SUBSCRIBER_TRAIL_LADDER: ReadonlyArray<readonly [number, number]> = [
  [12, 8],
  [15, 10],
  [25, 18],
  [40, 28],
];

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

/** Limit chase step — move resting entry toward mark by fraction of remaining gap. */
export function computeChaseLimitPrice(
  mark: number,
  currentLimit: number,
  direction: 'LONG' | 'SHORT',
  stepPct = 0.25,
): number {
  if (direction === 'LONG') {
    const gap = currentLimit - mark;
    if (gap <= 0) return currentLimit;
    return Math.round((currentLimit - gap * stepPct) * 100) / 100;
  }
  const gap = mark - currentLimit;
  if (gap <= 0) return currentLimit;
  return Math.round((currentLimit + gap * stepPct) * 100) / 100;
}
