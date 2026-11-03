/**
 * Short in-process cache for GET ops/relay-status so ChainSupervisor's
 * authenticated poll (every 5–10 min) does not hit Neon on every tick.
 *
 * Auth stays outside the cache (caller asserts the token first). Default TTL
 * is 10 minutes so a 5-minute supervisor leaves ≥5 min Neon-idle gaps.
 *
 * RK 22:14 / HM 9754e839380f:
 * - Armed / LIVE status reads **always bypass** the cache.
 * - Only disarmed snapshots are stored.
 * - Writers invalidate via setInstancePaused + PrismaService hooks (outside
 *   bitfinex-policy hash-locked files), covering the four pause/arm paths:
 *   setInstancePaused, failClosedStuckLiveInstances,
 *   pauseRelayForSustainedFidelityBreach, pauseRelayForPositionMismatch.
 */
export const RELAY_STATUS_CACHE_TTL_MS = Math.max(
  60_000,
  Math.min(
    30 * 60_000,
    Number(process.env.RELAY_STATUS_CACHE_TTL_MS ?? 600_000) || 600_000,
  ),
);

type CacheEntry = {
  value: unknown;
  expiresAt: number;
  generation: number;
};

const entries = new Map<string, CacheEntry>();
const inFlight = new Map<string, { generation: number; promise: Promise<unknown> }>();
let generation = 0;
let clock: () => number = () => Date.now();

export function relayStatusCacheKey(slug: string, userId: string): string {
  return `${slug.trim()}|${userId.trim()}`;
}

/** True when a relay-status payload reports armed or live execution. */
export function isRelayStatusArmedOrLive(status: unknown): boolean {
  if (!status || typeof status !== 'object') return false;
  const s = status as Record<string, unknown>;
  if (typeof s.relayArmedAt === 'string' && s.relayArmedAt.trim()) return true;
  if (s.relayExecutionMode === 'LIVE') return true;
  return false;
}

/**
 * Load relay-status with cache. Armed/LIVE results always bypass (never served
 * from cache, never stored). Disarmed results may hit the TTL cache.
 */
export async function cachedRelayStatus<T>(
  key: string,
  load: () => Promise<T>,
  ttlMs: number = RELAY_STATUS_CACHE_TTL_MS,
): Promise<T> {
  const hit = entries.get(key);
  if (hit && hit.generation === generation && clock() < hit.expiresAt) {
    if (!isRelayStatusArmedOrLive(hit.value)) {
      return hit.value as T;
    }
    // Stale armed entry must never be served.
    entries.delete(key);
  }
  const pending = inFlight.get(key);
  if (pending && pending.generation === generation) {
    return pending.promise as Promise<T>;
  }
  const loadGeneration = generation;
  const promise = load().then((value) => {
    if (loadGeneration === generation) {
      if (!isRelayStatusArmedOrLive(value)) {
        entries.set(key, { value, expiresAt: clock() + ttlMs, generation: loadGeneration });
      } else {
        entries.delete(key);
      }
    }
    return value;
  });
  inFlight.set(key, { generation: loadGeneration, promise });
  try {
    return await promise;
  } finally {
    if (inFlight.get(key)?.promise === promise) inFlight.delete(key);
  }
}

/** Drop one key, or the whole table when key is omitted. */
export function invalidateRelayStatusCache(key?: string): void {
  if (key) {
    entries.delete(key);
    return;
  }
  generation += 1;
  entries.clear();
  inFlight.clear();
}

/** Drop every cached snapshot for a user (any slug). */
export function invalidateRelayStatusCacheForUser(userId: string): void {
  const suffix = `|${userId.trim()}`;
  for (const key of [...entries.keys()]) {
    if (key.endsWith(suffix)) entries.delete(key);
  }
}

/**
 * True when a Prisma TradingAgentInstance write can change pause/arm visibility
 * for ops/relay-status (the four named pause/arm writers all go through these).
 */
export function tradingAgentInstanceWriteTouchesArmOrPause(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false;
  const d = data as Record<string, unknown>;
  if (d.status != null) return true;
  const dash = d.dashboardState;
  if (!dash || typeof dash !== 'object') return false;
  const ds = dash as Record<string, unknown>;
  return (
    'relayArmedAt' in ds ||
    'relayExecutionMode' in ds ||
    'realTradingConfirmedAt' in ds
  );
}

/** Test hook: swap the clock used for TTL checks. Pass `null` to restore. */
export function setRelayStatusCacheClockForTests(fn: (() => number) | null): void {
  clock = fn ?? (() => Date.now());
}
