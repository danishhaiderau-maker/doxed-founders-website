/**
 * Short in-process cache for GET ops/relay-status so ChainSupervisor's
 * authenticated poll (every 5–10 min) does not hit Neon on every tick.
 *
 * Auth stays outside the cache (caller asserts the token first). Default TTL
 * is 10 minutes so a 5-minute supervisor leaves ≥5 min Neon-idle gaps.
 *
 * RK 22:14: armed / live payloads never use the cache — pause + arming paths
 * also invalidate so a disarm cannot leave a stale ARMED read.
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

export async function cachedRelayStatus<T>(
  key: string,
  load: () => Promise<T>,
  ttlMs: number = RELAY_STATUS_CACHE_TTL_MS,
): Promise<T> {
  const hit = entries.get(key);
  if (hit && hit.generation === generation && clock() < hit.expiresAt) {
    // RK 22:14: never serve armed/live from cache (bypass).
    if (!isRelayStatusArmedOrLive(hit.value)) {
      return hit.value as T;
    }
    entries.delete(key);
  }
  const pending = inFlight.get(key);
  if (pending && pending.generation === generation) {
    return pending.promise as Promise<T>;
  }
  const loadGeneration = generation;
  const promise = load().then((value) => {
    if (loadGeneration === generation) {
      // Only cache disarmed / non-live snapshots (Neon idle for ChainSupervisor).
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

/** Test hook: swap the clock used for TTL checks. Pass `null` to restore. */
export function setRelayStatusCacheClockForTests(fn: (() => number) | null): void {
  clock = fn ?? (() => Date.now());
}
