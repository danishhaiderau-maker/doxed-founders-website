import { createHash } from 'node:crypto';
import type { PrismaClient } from '@prisma/client';
import type { RoutingDecision } from './routing-engine.types';

/**
 * Layer 1 of the routing pipeline (docs/KERNEL.md §6): a cache of routing
 * decisions keyed by a hash of the normalized prompt prefix.
 *
 * v1 spec:
 *   - maxEntries = 512 (in-memory backend; persisted backends are unbounded)
 *   - ttlMs      = 5 minutes
 *   - key        = "v1:" + sha256(normalized first 4KB of prompt)
 *
 * ## Backends
 *
 * Multi-instance Railway deployments can't share a per-process LRU Map, so
 * the cache is now pluggable via `ROUTING_CACHE_BACKEND`:
 *
 *   - `memory` (default, dev/test) — `InMemoryRoutingCache`, the original
 *      LRU Map with TTL. Fast, no network, but per-process.
 *   - `neon`  — `NeonRoutingCache`, persists to the `RoutingCacheEntry`
 *      Postgres table (see `prisma/migrations/<ts>_routing_cache_entry`).
 *      Shares state across instances. Reads are best-effort: a DB hiccup
 *      degrades to a miss, never throws.
 *   - `redis` — reserved; not wired here because Redis isn't part of the
 *      stack yet. The interface is ready for it.
 *
 * The interface intentionally keeps the original `computeKey`, `get`, `set`,
 * `clear`, `stats` surface so `RoutingEngineService` is unchanged.
 */

/** Backend-agnostic routing cache surface. */
export interface RoutingCache {
  computeKey(prompt: string): string;
  get(key: string): Promise<RoutingDecision | null> | RoutingDecision | null;
  set(key: string, decision: RoutingDecision): Promise<void> | void;
  clear(): Promise<void> | void;
  stats(): { size: number; hits: number; misses: number };
}

/** DI token for the active routing cache backend. */
export const ROUTING_CACHE = Symbol('ROUTING_CACHE');

/** Backend selector — read once at module-wire time. */
export type RoutingCacheBackend = 'memory' | 'neon' | 'redis';

const DEFAULT_TTL_MS = 5 * 60 * 1000;
const DEFAULT_MAX_ENTRIES = 512;

/**
 * In-memory LRU + TTL cache. Identical behaviour to the original
 * RoutingEngineCache — kept as the default dev/test backend.
 *
 * Eviction is LRU based on insertion / access order. Expiry is lazy —
 * entries are checked on read and reaped when found stale.
 */
export class InMemoryRoutingCache implements RoutingCache {
  private readonly maxEntries: number;
  private readonly ttlMs: number;
  private readonly store = new Map<string, { decision: RoutingDecision; expiresAt: number }>();
  private hits = 0;
  private misses = 0;

  constructor(opts?: { maxEntries?: number; ttlMs?: number }) {
    this.maxEntries = opts?.maxEntries ?? DEFAULT_MAX_ENTRIES;
    this.ttlMs = opts?.ttlMs ?? DEFAULT_TTL_MS;
  }

  computeKey(prompt: string): string {
    const normalized = prompt.replace(/\s+/g, ' ').trim().toLowerCase();
    const head = normalized.slice(0, 4096);
    const hash = createHash('sha256').update(head, 'utf8').digest('hex');
    return `v1:${hash}`;
  }

  get(key: string): RoutingDecision | null {
    const entry = this.store.get(key);
    if (!entry) {
      this.misses += 1;
      return null;
    }
    if (entry.expiresAt < Date.now()) {
      this.store.delete(key);
      this.misses += 1;
      return null;
    }
    // Move to most-recently-used position.
    this.store.delete(key);
    this.store.set(key, entry);
    this.hits += 1;
    return entry.decision;
  }

  set(key: string, decision: RoutingDecision): void {
    // Delete first so re-inserting the same key reorders it to MRU.
    this.store.delete(key);
    this.store.set(key, { decision, expiresAt: Date.now() + this.ttlMs });

    while (this.store.size > this.maxEntries) {
      // Map iteration is insertion-order; oldest entry is the first key.
      const oldestKey = this.store.keys().next().value;
      if (oldestKey === undefined) break;
      this.store.delete(oldestKey);
    }
  }

  clear(): void {
    this.store.clear();
    this.hits = 0;
    this.misses = 0;
  }

  stats(): { size: number; hits: number; misses: number } {
    return {
      size: this.store.size,
      hits: this.hits,
      misses: this.misses,
    };
  }
}

/**
 * Postgres-backed cache (Neon). Persists each entry to the
 * `RoutingCacheEntry` table so every Railway replica sees the same cache.
 *
 * Best-effort: any DB error degrades to a miss and bumps the miss counter.
 * This keeps the routing pipeline resilient — a Neon cold-start or split
 * brain should never block request routing.
 *
 * Lazy expiry: stale rows are deleted on read; a periodic cleanup is not
 * required because writes always upsert with a fresh `expiresAt`.
 */
export class NeonRoutingCache implements RoutingCache {
  private readonly ttlMs: number;
  private hits = 0;
  private misses = 0;

  constructor(
    private readonly prisma: PrismaClient,
    opts?: { ttlMs?: number },
  ) {
    this.ttlMs = opts?.ttlMs ?? DEFAULT_TTL_MS;
  }

  computeKey(prompt: string): string {
    const normalized = prompt.replace(/\s+/g, ' ').trim().toLowerCase();
    const head = normalized.slice(0, 4096);
    const hash = createHash('sha256').update(head, 'utf8').digest('hex');
    return `v1:${hash}`;
  }

  async get(key: string): Promise<RoutingDecision | null> {
    try {
      const row = await this.prisma.routingCacheEntry.findUnique({
        where: { cacheKey: key },
      });
      if (!row) {
        this.misses += 1;
        return null;
      }
      if (row.expiresAt.getTime() < Date.now()) {
        // Lazy sweep — best-effort, ignore failures.
        await this.prisma.routingCacheEntry
          .delete({ where: { cacheKey: key } })
          .catch(() => {});
        this.misses += 1;
        return null;
      }
      this.hits += 1;
      return row.decision as unknown as RoutingDecision;
    } catch {
      // Best-effort: a DB error must not break routing.
      this.misses += 1;
      return null;
    }
  }

  async set(key: string, decision: RoutingDecision): Promise<void> {
    try {
      await this.prisma.routingCacheEntry.upsert({
        where: { cacheKey: key },
        create: {
          cacheKey: key,
          decision: decision as unknown as never,
          expiresAt: new Date(Date.now() + this.ttlMs),
        },
        update: {
          decision: decision as unknown as never,
          expiresAt: new Date(Date.now() + this.ttlMs),
        },
      });
    } catch {
      // Best-effort: swallow the error so routing continues.
    }
  }

  async clear(): Promise<void> {
    try {
      await this.prisma.routingCacheEntry.deleteMany({});
    } catch {
      // ignore
    }
    this.hits = 0;
    this.misses = 0;
  }

  stats(): { size: number; hits: number; misses: number } {
    // `size` is approximate — we don't block on a COUNT(*) per call. The
    // hit/miss counters are still meaningful for observability.
    return { size: -1, hits: this.hits, misses: this.misses };
  }
}

/**
 * Build a `RoutingCache` from the environment. Used by the routing module
 * to pick the right backend at bootstrap.
 *
 *   ROUTING_CACHE_BACKEND=memory (default) | neon | redis
 *
 * When `neon` is selected without a Prisma client (e.g. in unit tests),
 * falls back to `memory` so the caller never crashes.
 */
export function createRoutingCache(
  env: NodeJS.ProcessEnv = process.env,
  prisma?: PrismaClient,
): RoutingCache {
  const backend = (env.ROUTING_CACHE_BACKEND ?? 'memory').toLowerCase() as RoutingCacheBackend;
  if (backend === 'neon') {
    if (!prisma) {
      // Fall back to memory if Prisma isn't available — never crash.
      return new InMemoryRoutingCache();
    }
    return new NeonRoutingCache(prisma);
  }
  if (backend === 'redis') {
    // Redis isn't part of the stack yet — fall back to memory. When Redis
    // lands, plug a RedisRoutingCache implementation in here.
    return new InMemoryRoutingCache();
  }
  return new InMemoryRoutingCache();
}

/**
 * @deprecated Use `RoutingCache` / `InMemoryRoutingCache` instead. Kept as a
 * thin subclass so existing Nest DI providers that reference
 * `RoutingEngineCache` by name keep resolving at bootstrap.
 */
export class RoutingEngineCache extends InMemoryRoutingCache {}
