/** User-controlled live relay policy for the Conservative BTC agent. */
export const CONSERVATIVE_BTC_LIVE_RELAY_POLICY = 'continuous_typeb_tile2_limit_only_v3';
export const CONSERVATIVE_BTC_LIVE_RELAY_LANES = [
  'CONTINUOUS',
  'TYPE_B_HUNTER_V1',
  'SR_MICRO_TILE_V2_STATIC',
] as const;

export function hasActiveLiveRelayConsent(dashboardState: unknown): boolean {
  if (!dashboardState || typeof dashboardState !== 'object') return false;
  const state = dashboardState as Record<string, unknown>;
  return (
    state.relayExecutionMode === 'LIVE' &&
    state.relayPolicyVersion === CONSERVATIVE_BTC_LIVE_RELAY_POLICY &&
    typeof state.realTradingConfirmedAt === 'string' &&
    state.realTradingConfirmedAt.length > 0
  );
}

/**
 * Fail closed unless the authenticated Start action stamped active consent.
 * An ops dry-run flag always wins, while an explicit false/unset flag can only
 * enable execution for an instance that carries the current consent stamp.
 */
export function shouldDryRunIntentMirror(
  envValue: string | null | undefined,
  dashboardState: unknown,
): boolean {
  const normalized = (envValue ?? '').trim().toLowerCase();
  if (['1', 'true', 'on', 'yes'].includes(normalized)) return true;
  if (!hasActiveLiveRelayConsent(dashboardState)) return true;
  return false;
}
