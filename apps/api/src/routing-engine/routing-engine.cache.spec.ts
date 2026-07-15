/**
 * Unit tests for the routing cache backends and factory (Phase 1 / kernel).
 *
 *   1. InMemoryRoutingCache — LRU + TTL semantics, key normalization.
 *   2. NeonRoutingCache — best-effort persistence + lazy expiry, against an
 *      in-memory Prisma stub (no DATABASE_URL required).
 *   3. createRoutingCache — env-var wiring + safe fallback.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  createRoutingCache,
  InMemoryRoutingCache,
  NeonRoutingCache,
  type RoutingCache,
} from './routing-engine.cache';
import type { RoutingDecision } from './routing-engine.types';

function mkDecision(overrides: Partial<RoutingDecision> = {}): RoutingDecision {
  return {
    requestId: 'req-1',
    chosenProvider: 'openai',
    chosenModel: 'gpt-4',
    score: 0.9,
    cacheLevel: 'miss',
    candidates: [{ provider: 'openai', model: 'gpt-4', score: 0.9 }],
    ...overrides,
  };
}

describe('InMemoryRoutingCache — LRU + TTL', () => {
  let cache: InMemoryRoutingCache;

  beforeEach(() => {
    cache = new InMemoryRoutingCache({ maxEntries: 3, ttlMs: 1000 });
  });

  it('computeKey is deterministic for the same prompt', () => {
    const a = cache.computeKey('  Hello   World  ');
    const b = cache.computeKey('hello world');
    const c = cache.computeKey('different prompt');
    assert.equal(a, b, 'whitespace + case must normalize');
    assert.notEqual(a, c);
    assert.ok(a.startsWith('v1:'));
  });

  it('returns null on miss and bumps the miss counter', () => {
    const got = cache.get('missing') as RoutingDecision | null;
    assert.equal(got, null);
    assert.equal(cache.stats().misses, 1);
    assert.equal(cache.stats().hits, 0);
  });

  it('set → get round-trip', () => {
    const key = cache.computeKey('a prompt');
    cache.set(key, mkDecision());
    const got = cache.get(key) as RoutingDecision | null;
    assert.ok(got);
    assert.equal(got!.chosenModel, 'gpt-4');
    assert.equal(cache.stats().hits, 1);
  });

  it('LRU evicts the oldest entry when maxEntries is exceeded', () => {
    cache.set('k1', mkDecision({ chosenModel: 'm1' }));
    cache.set('k2', mkDecision({ chosenModel: 'm2' }));
    cache.set('k3', mkDecision({ chosenModel: 'm3' }));
    cache.set('k4', mkDecision({ chosenModel: 'm4' }));
    assert.equal(cache.stats().size, 3);
    assert.equal(cache.get('k1'), null, 'k1 was the LRU and must have evicted');
    assert.ok(cache.get('k4'));
  });

  it('LRU reorders on access (MRU)', () => {
    cache.set('k1', mkDecision({ chosenModel: 'm1' }));
    cache.set('k2', mkDecision({ chosenModel: 'm2' }));
    cache.set('k3', mkDecision({ chosenModel: 'm3' }));
    // touch k1 → it becomes MRU; k2 is now LRU.
    cache.get('k1');
    cache.set('k4', mkDecision({ chosenModel: 'm4' }));
    assert.ok(cache.get('k1'), 'k1 was just touched, must survive');
    assert.equal(cache.get('k2'), null, 'k2 was LRU after the touch, must evict');
  });

  it('TTL expires entries', async () => {
    const short = new InMemoryRoutingCache({ ttlMs: 10 });
    short.set('k', mkDecision());
    assert.ok(short.get('k'));
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(short.get('k'), null);
  });

  it('clear() wipes everything and resets counters', () => {
    cache.set('k', mkDecision());
    cache.get('k');
    cache.clear();
    assert.equal(cache.stats().size, 0);
    assert.equal(cache.stats().hits, 0);
    assert.equal(cache.stats().misses, 0);
  });
});

describe('NeonRoutingCache — best-effort Postgres persistence', () => {
  type Row = {
    cacheKey: string;
    decision: unknown;
    expiresAt: Date;
    createdAt: Date;
    updatedAt: Date;
  };

  function makeStubPrisma(fail = false) {
    const rows = new Map<string, Row>();
    return {
      routingCacheEntry: {
        findUnique: async ({ where }: { where: { cacheKey: string } }) => {
          if (fail) throw new Error('db down');
          return rows.get(where.cacheKey) ?? null;
        },
        upsert: async ({
          where,
          create,
          update,
        }: {
          where: { cacheKey: string };
          create: Partial<Row>;
          update: Partial<Row>;
        }) => {
          if (fail) throw new Error('db down');
          const existing = rows.get(where.cacheKey);
          if (existing) {
            Object.assign(existing, update, { updatedAt: new Date() });
            return existing;
          }
          const row: Row = {
            cacheKey: where.cacheKey,
            decision: create.decision,
            expiresAt: create.expiresAt!,
            createdAt: new Date(),
            updatedAt: new Date(),
          };
          rows.set(where.cacheKey, row);
          return row;
        },
        delete: async ({ where }: { where: { cacheKey: string } }) => {
          rows.delete(where.cacheKey);
          return null;
        },
        deleteMany: async () => {
          rows.clear();
          return { count: 0 };
        },
      },
      _rows: rows,
    };
  }

  it('set → get round-trip persists through the Prisma stub', async () => {
    const prisma = makeStubPrisma();
    const cache: RoutingCache = new NeonRoutingCache(prisma as never);
    const key = cache.computeKey('persisted decision');
    await cache.set(key, mkDecision());
    const got = await cache.get(key);
    assert.ok(got);
    assert.equal(got!.chosenModel, 'gpt-4');
  });

  it('returns null on miss', async () => {
    const prisma = makeStubPrisma();
    const cache: RoutingCache = new NeonRoutingCache(prisma as never);
    const got = await cache.get(cache.computeKey('absent'));
    assert.equal(got, null);
  });

  it('lazy-expires stale entries (delete on read)', async () => {
    const prisma = makeStubPrisma();
    const cache: RoutingCache = new NeonRoutingCache(prisma as never, { ttlMs: 10 });
    const key = cache.computeKey('will expire');
    await cache.set(key, mkDecision());
    await new Promise((r) => setTimeout(r, 20));
    const got = await cache.get(key);
    assert.equal(got, null);
    // The lazy sweep must have deleted the stale row.
    assert.equal(prisma._rows.size, 0);
  });

  it('degrades to a miss on DB error (never throws)', async () => {
    const prisma = makeStubPrisma(true /* fail */);
    const cache: RoutingCache = new NeonRoutingCache(prisma as never);
    const got = await cache.get('any');
    assert.equal(got, null);
    // set must also swallow
    await cache.set('any', mkDecision());
  });

  it('clear() is best-effort and resets counters', async () => {
    const prisma = makeStubPrisma();
    const cache: RoutingCache = new NeonRoutingCache(prisma as never);
    const key = cache.computeKey('x');
    await cache.set(key, mkDecision());
    await cache.get(key);
    await cache.clear();
    // hit/miss counters reset
    const stats = cache.stats();
    assert.equal(stats.hits, 0);
    assert.equal(stats.misses, 0);
  });
});

describe('createRoutingCache — env-var backend selection', () => {
  it('defaults to InMemoryRoutingCache when no env', () => {
    const cache = createRoutingCache({});
    assert.ok(cache instanceof InMemoryRoutingCache);
  });

  it('selects neon when ROUTING_CACHE_BACKEND=neon AND a prisma client is provided', () => {
    const fakePrisma = { routingCacheEntry: {} } as never;
    const cache = createRoutingCache({ ROUTING_CACHE_BACKEND: 'neon' }, fakePrisma);
    assert.ok(cache instanceof NeonRoutingCache);
  });

  it('falls back to memory when neon is requested but prisma is missing', () => {
    const cache = createRoutingCache({ ROUTING_CACHE_BACKEND: 'neon' });
    assert.ok(cache instanceof InMemoryRoutingCache);
  });

  it('falls back to memory for unknown backend', () => {
    const cache = createRoutingCache({ ROUTING_CACHE_BACKEND: 'redis' });
    assert.ok(cache instanceof InMemoryRoutingCache);
  });

  it('respects case-insensitive backend value', () => {
    const cache = createRoutingCache({ ROUTING_CACHE_BACKEND: 'MEMORY' });
    assert.ok(cache instanceof InMemoryRoutingCache);
  });
});
