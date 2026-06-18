/**
 * Frozen Bitfinex live-copy policy (NestJS relay).
 * NOT overwritten by bybit_bot.py / sync-btc-research-bot — real money execution lives here.
 */
export const BITFINEX_COPY_POLICY_VERSION = 1;

/** Showcase dashboard max_active_signals drives copy concurrency when bot bridge is up. */
export const BITFINEX_COPY_DEFAULT_MAX_CONCURRENT = 3;

/** Platform margin cap per virtual lot (USD). */
export const BITFINEX_COPY_DEFAULT_MARGIN_USD = 20;

/** Default derivative leverage on Bitfinex orders (lev param required). */
export const BITFINEX_COPY_DEFAULT_LEVERAGE = 100;

/** Scenario C exits — must match subscriber-exit.ts ladder / thesis / hard stop. */
export const BITFINEX_COPY_EXIT_RULES = {
  thesisFastCutMarginPct: -12,
  thesisMfeProtectMarginPct: 2,
  hardStopMarginPct: -18,
  profitLockLadder: [
    [12, 8],
    [15, 10],
    [25, 18],
    [40, 28],
  ] as const,
} as const;
