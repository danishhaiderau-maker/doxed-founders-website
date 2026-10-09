import type { BitfinexFuturesPairConstraints } from '../exchanges/bitfinex-api.client';

/**
 * Minimum ratio between the estimated liquidation distance and the protective
 * stop distance. At 100x with Bitfinex's 0.5% maintenance margin the posted-
 * margin liquidation sits ~0.50% away and the clamped -13% margin stop sits
 * 0.13% away (3.85x), which passes. Anything that brings liquidation within 3x
 * of the stop (wider stop, higher maintenance margin, higher leverage) fails.
 * Env may only raise this floor.
 */
export const DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE = 3;

/**
 * Bitfinex derivatives expose no readable margin-mode field. The executor sets
 * `lev` on every order and models liquidation from the posted position margin
 * alone. For a single BTC-PERP position that is the worst case: any extra
 * derivatives-wallet collateral only moves liquidation farther away.
 */
export const BITFINEX_ASSUMED_MARGIN_MODEL = 'PER_ORDER_LEV_POSTED_MARGIN_WORST_CASE' as const;

export function resolveMinLiquidationToStopMultiple(
  envValue: string | undefined = process.env.BITFINEX_MIN_LIQ_TO_STOP_MULTIPLE,
): number {
  const parsed = Number(envValue);
  return Number.isFinite(parsed) && parsed > DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE
    ? parsed
    : DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE;
}

export type PreTradeLiquidationSafety =
  | {
      ok: true;
      marginModel: typeof BITFINEX_ASSUMED_MARGIN_MODEL;
      stopDistanceFraction: number;
      liquidationDistanceFraction: number;
      multiple: number;
      minMultiple: number;
    }
  | {
      ok: false;
      reason:
        | 'VENUE_MARGIN_EVIDENCE_MISSING'
        | 'MARGIN_MODE_LEVERAGE_UNVERIFIED'
        | 'LEVERAGE_EXCEEDS_VENUE_MAXIMUM'
        | 'STOP_DISTANCE_INVALID'
        | 'LIQUIDATION_INSIDE_MAINTENANCE'
        | 'LIQUIDATION_TOO_CLOSE_TO_STOP';
      detail: string;
    };

/** Pure pre-trade gate: fail closed unless liquidation clears the stop by the configured multiple. */
export function evaluatePreTradeLiquidationSafety(input: {
  leverage: number;
  orderLeverage: number;
  stopLossMarginPct: number;
  venue: Pick<BitfinexFuturesPairConstraints, 'initialMarginFraction' | 'maintenanceMarginFraction'> | null;
  minMultiple?: number;
  /**
   * Approval-backed entries only (Boss 2026-10-09): replaces the 3x multiple
   * with "stop <= liquidation - buffer bp". Never set for legacy paths.
   */
  minLiquidationBufferBp?: number;
}): PreTradeLiquidationSafety {
  const minMultiple = Math.max(
    DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE,
    input.minMultiple ?? DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE,
  );
  const venue = input.venue;
  if (
    !venue
    || !(venue.initialMarginFraction > 0)
    || !(venue.maintenanceMarginFraction > 0)
  ) {
    return { ok: false, reason: 'VENUE_MARGIN_EVIDENCE_MISSING', detail: 'live venue margin fractions unavailable' };
  }
  if (
    !Number.isInteger(input.leverage)
    || input.leverage < 1
    || input.orderLeverage !== input.leverage
  ) {
    return {
      ok: false,
      reason: 'MARGIN_MODE_LEVERAGE_UNVERIFIED',
      detail: `resolved leverage ${input.leverage} != order lev ${input.orderLeverage}`,
    };
  }
  const venueMaxLeverage = Math.floor(1 / venue.initialMarginFraction + 1e-9);
  if (input.leverage > venueMaxLeverage) {
    return {
      ok: false,
      reason: 'LEVERAGE_EXCEEDS_VENUE_MAXIMUM',
      detail: `leverage ${input.leverage} > venue max ${venueMaxLeverage}`,
    };
  }
  const stopDistanceFraction = Math.abs(input.stopLossMarginPct) / 100 / input.leverage;
  if (!(input.stopLossMarginPct < 0) || !(stopDistanceFraction > 0)) {
    return { ok: false, reason: 'STOP_DISTANCE_INVALID', detail: `stop margin ${input.stopLossMarginPct}%` };
  }
  const liquidationDistanceFraction = 1 / input.leverage - venue.maintenanceMarginFraction;
  if (!(liquidationDistanceFraction > 0)) {
    return {
      ok: false,
      reason: 'LIQUIDATION_INSIDE_MAINTENANCE',
      detail: `1/${input.leverage} <= maintenance ${venue.maintenanceMarginFraction}`,
    };
  }
  const multiple = liquidationDistanceFraction / stopDistanceFraction;
  if (input.minLiquidationBufferBp != null) {
    const bufferBp = Math.max(15, input.minLiquidationBufferBp);
    const gapBp = (liquidationDistanceFraction - stopDistanceFraction) * 1e4;
    if (!(gapBp + 1e-9 >= bufferBp)) {
      return {
        ok: false,
        reason: 'LIQUIDATION_TOO_CLOSE_TO_STOP',
        detail:
          `liq ${(liquidationDistanceFraction * 1e4).toFixed(2)}bp vs backup stop ${(stopDistanceFraction * 1e4).toFixed(2)}bp `
          + `= ${gapBp.toFixed(2)}bp < ${bufferBp}bp buffer`,
      };
    }
  } else if (multiple < minMultiple) {
    return {
      ok: false,
      reason: 'LIQUIDATION_TOO_CLOSE_TO_STOP',
      detail:
        `liq ${(liquidationDistanceFraction * 100).toFixed(4)}% vs stop ${(stopDistanceFraction * 100).toFixed(4)}% `
        + `= ${multiple.toFixed(2)}x < ${minMultiple}x`,
    };
  }
  return {
    ok: true,
    marginModel: BITFINEX_ASSUMED_MARGIN_MODEL,
    stopDistanceFraction,
    liquidationDistanceFraction,
    multiple,
    minMultiple,
  };
}
