/**
 * Hot `PlatformSettings` reads (margin cap, rate limits, admin status) are
 * served from a short in-process cache so per-tick callers do not hit Neon.
 *
 * The row also carries the ~180 KB relay snapshot and encrypted credentials;
 * every loader must pass an explicit `select` so neither is transferred.
 *
 * Writes in this process call {@link invalidatePlatformSettingsCache}. Other
 * processes (the relay-executor worker) converge within the TTL.
 */
export const PLATFORM_SETTINGS_ID = 'default';

export const PLATFORM_SETTINGS_CACHE_TTL_MS = Math.max(
  1_000,
  Math.min(60_000, Number(process.env.PLATFORM_SETTINGS_CACHE_TTL_MS ?? 30_000) || 30_000),
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

export async function cachedPlatformSettings<T>(
  key: string,
  load: () => Promise<T>,
  ttlMs: number = PLATFORM_SETTINGS_CACHE_TTL_MS,
): Promise<T> {
  const hit = entries.get(key);
  if (hit && hit.generation === generation && clock() < hit.expiresAt) {
    return hit.value as T;
  }
  const pending = inFlight.get(key);
  if (pending && pending.generation === generation) {
    return pending.promise as Promise<T>;
  }
  const loadGeneration = generation;
  const promise = load().then(
    (value) => {
      // An admin write that landed mid-load must not be overwritten by the older value.
      if (loadGeneration === generation) {
        entries.set(key, { value, expiresAt: clock() + ttlMs, generation: loadGeneration });
      }
      return value;
    },
  );
  inFlight.set(key, { generation: loadGeneration, promise });
  try {
    return await promise;
  } finally {
    if (inFlight.get(key)?.promise === promise) inFlight.delete(key);
  }
}

export function invalidatePlatformSettingsCache(): void {
  generation += 1;
  entries.clear();
  inFlight.clear();
}

/** Test hook: swap the clock used for TTL checks. Pass `null` to restore. */
export function setPlatformSettingsCacheClockForTests(fn: (() => number) | null): void {
  clock = fn ?? (() => Date.now());
}
