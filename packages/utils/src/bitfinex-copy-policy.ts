/**
 * Frozen Bitfinex live-copy policy (NestJS relay).
 * NOT overwritten by bybit_bot.py / sync-btc-research-bot — real money execution lives here.
 */
export const BITFINEX_COPY_POLICY_VERSION = 3;

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
