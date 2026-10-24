/**
 * RK L27 on EVERY arm path (#852, 24 Oct). Fly owns the latest real L (persisted from Bitfinex reads) and the ONE shared
 * arm-gate liq check (live_tile_gate.l27_arm_block -> liq_too_close, <= 15 bp past the stop until a new stop amendment
 * passes). The relay's arm/re-arm routes (instance resume, ops/rearm, live hire) never re-implement it: they read the
 * helper's verdict from Fly's GET /api/live-copy/arm-gate and refuse on it. Unreadable verdict => refuse (fail closed).
 */
export const FLY_ARM_GATE_PATH = '/api/live-copy/arm-gate';
export const FLY_ARM_GATE_SCHEMA = 'fly_live_arm_gate_v1';
/** Same code as live_tile_gate.DENY_L27_LATEST_L. */
export const DENY_L27_LATEST_L = 'LIVE_L27_LATEST_REAL_L_LE_15BP';
export const DENY_L27_GATE_UNREADABLE = 'LIVE_L27_ARM_GATE_UNREADABLE';
export const FLY_ARM_GATE_TIMEOUT_MS = 4_000;

export type LiveArmPath = 'instance/resume' | 'ops/rearm' | 'hire';

/** Pure: Fly arm-gate body -> refusal code, or null when the shared helper allows arming. */
export function l27ArmGateVerdict(body: unknown): string | null {
  if (!body || typeof body !== 'object') return DENY_L27_GATE_UNREADABLE;
  const b = body as Record<string, unknown>;
  if (b.schema !== FLY_ARM_GATE_SCHEMA || !('l27_block' in b)) return DENY_L27_GATE_UNREADABLE;
  if (b.l27_block === null) return null;
  return typeof b.l27_block === 'string' && b.l27_block.trim() ? b.l27_block.trim() : DENY_L27_GATE_UNREADABLE;
}

export function flyBotBaseUrl(env: NodeJS.ProcessEnv = process.env): string {
  return (env.FLY_BOT_URL?.trim() || 'https://doxed-btc-bot.fly.dev').replace(/\/$/, '');
}

/** Read Fly's verdict (admin token header; never logged). Any failure => DENY_L27_GATE_UNREADABLE. */
export async function fetchL27ArmBlock(
  fetchImpl: typeof fetch = fetch,
  env: NodeJS.ProcessEnv = process.env,
): Promise<string | null> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = env.BOT_ADMIN_TOKEN?.trim();
  if (token) headers['X-Bot-Admin-Token'] = token;
  try {
    const res = await fetchImpl(`${flyBotBaseUrl(env)}${FLY_ARM_GATE_PATH}`, {
      method: 'GET',
      headers,
      signal: AbortSignal.timeout(FLY_ARM_GATE_TIMEOUT_MS),
    });
    if (!res.ok) return DENY_L27_GATE_UNREADABLE;
    return l27ArmGateVerdict(await res.json());
  } catch {
    return DENY_L27_GATE_UNREADABLE;
  }
}
