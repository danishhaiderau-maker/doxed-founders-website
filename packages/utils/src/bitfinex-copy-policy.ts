/**
 * Frozen Bitfinex live-copy policy (NestJS relay).
 * NOT overwritten by bybit_bot.py / sync-btc-research-bot — real money execution lives here.
 */
export const BITFINEX_COPY_POLICY_VERSION = 5;

/** Wide disaster stop (margin %) in showcase-mirror mode — crash/disconnect insurance only. */
export const BITFINEX_COPY_MIRROR_DISASTER_STOP_MARGIN_PCT_DEFAULT = -40;

/** Resolve MIRROR_DISASTER_STOP_MARGIN_PCT (default -40 margin at leverage). */
export function resolveMirrorDisasterStopMarginPct(envOverride?: string | number | null): number {
  const raw = Number(
    envOverride ?? process.env.MIRROR_DISASTER_STOP_MARGIN_PCT ?? BITFINEX_COPY_MIRROR_DISASTER_STOP_MARGIN_PCT_DEFAULT,
  );
  return Number.isFinite(raw) && raw < 0 ? raw : BITFINEX_COPY_MIRROR_DISASTER_STOP_MARGIN_PCT_DEFAULT;
}

/** When true (default), subscribers exit only on showcase close + exchange hard stop — no independent Scenario C. */
export function isShowcaseMirrorOnlyMode(): boolean {
  const raw = process.env.SUBSCRIBER_SHOWCASE_MIRROR_ONLY;
  if (raw === 'false' || raw === '0') return false;
  return true;
}

/** Showcase dashboard max_active_signals drives copy concurrency when bot bridge is up. */
export const BITFINEX_COPY_DEFAULT_MAX_CONCURRENT = 3;

/** Platform margin cap per virtual lot (USD). */
export const BITFINEX_COPY_DEFAULT_MARGIN_USD = 20;

/** Default derivative leverage on Bitfinex orders (lev param required). */
export const BITFINEX_COPY_DEFAULT_LEVERAGE = 100;

/** Scenario C exits — must match subscriber-exit.ts ladder / thesis / hard stop.
 * Synced 2026-08-08 to bot.py THESIS_MFE_PROTECT_PCT=5.0 and scenario_c_config.py
 * TRAIL_LADDER_SCENARIO_C (4→2, 5→3, then 8→5). */
export const BITFINEX_COPY_EXIT_RULES = {
  thesisFastCutMarginPct: -12,
  thesisMfeProtectMarginPct: 5,
  hardStopMarginPct: -13,
  profitLockLadder: [
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
  ] as const,
} as const;
