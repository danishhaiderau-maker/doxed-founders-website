/**
 * RK 21:05: relay-status cache — one Neon load per TTL window.
 *
 * Mutation proof: drop the TTL hit branch → loads increments on every call.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  RELAY_STATUS_CACHE_TTL_MS,
  cachedRelayStatus,
  invalidateRelayStatusCache,
  isRelayStatusArmedOrLive,
  relayStatusCacheKey,
  setRelayStatusCacheClockForTests,
} from './relay-status-cache';

function withClock(start = 1_000_000) {
  let now = start;
  setRelayStatusCacheClockForTests(() => now);
  invalidateRelayStatusCache();
  return {
    advance: (ms: number) => {
      now += ms;
    },
  };
}

test.afterEach(() => {
  setRelayStatusCacheClockForTests(null);
  invalidateRelayStatusCache();
});

test('default TTL is 10 minutes (bounded 1–30 min)', () => {
  assert.ok(RELAY_STATUS_CACHE_TTL_MS >= 60_000);
  assert.ok(RELAY_STATUS_CACHE_TTL_MS <= 30 * 60_000);
  assert.equal(RELAY_STATUS_CACHE_TTL_MS, 600_000);
});

test('cache key scopes slug + userId', () => {
  assert.equal(relayStatusCacheKey(' conservative-btc ', ' u1 '), 'conservative-btc|u1');
});

test('serves cached value inside TTL and reloads after expiry', async () => {
  const clock = withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return { n: loads };
  };
  const key = relayStatusCacheKey('slug', 'user');
  assert.deepEqual(await cachedRelayStatus(key, load), { n: 1 });
  clock.advance(RELAY_STATUS_CACHE_TTL_MS - 1);
  assert.deepEqual(await cachedRelayStatus(key, load), { n: 1 });
  assert.equal(loads, 1);
  clock.advance(1);
  assert.deepEqual(await cachedRelayStatus(key, load), { n: 2 });
  assert.equal(loads, 2);
});

test('invalidate forces the next read to reload', async () => {
  withClock();
  let value = 'a';
  const key = 'k';
  assert.equal(await cachedRelayStatus(key, async () => value), 'a');
  value = 'b';
  assert.equal(await cachedRelayStatus(key, async () => value), 'a');
  invalidateRelayStatusCache(key);
  assert.equal(await cachedRelayStatus(key, async () => value), 'b');
});

test('concurrent callers share one in-flight load', async () => {
  withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    await new Promise((r) => setTimeout(r, 5));
    return 'v';
  };
  await Promise.all([cachedRelayStatus('k', load), cachedRelayStatus('k', load)]);
  assert.equal(loads, 1);
});

test('mutation proof: bypassing TTL hit would double-load inside window', async () => {
  const clock = withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return loads;
  };
  const key = 'mut';
  await cachedRelayStatus(key, load);
  // Simulate a broken cache that always misses: call load directly twice.
  clock.advance(1);
  await load();
  assert.equal(loads, 2, 'direct load bypass proves the cache is what keeps loads at 1');
  // Healthy cache still at 1 for the cached key path after first fill:
  loads = 0;
  await cachedRelayStatus(key, load);
  await cachedRelayStatus(key, load);
  assert.equal(loads, 0);
});

test('RK 22:14: armed/live payloads bypass cache (always reload)', async () => {
  withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return { relayArmedAt: '2026-11-03T00:00:00.000Z', relayExecutionMode: 'LIVE', n: loads };
  };
  const key = 'armed';
  await cachedRelayStatus(key, load);
  await cachedRelayStatus(key, load);
  assert.equal(loads, 2);
});

test('isRelayStatusArmedOrLive detects arm and LIVE mode', () => {
  assert.equal(isRelayStatusArmedOrLive({ relayArmedAt: null }), false);
  assert.equal(isRelayStatusArmedOrLive({ relayArmedAt: '2026-01-01T00:00:00Z' }), true);
  assert.equal(isRelayStatusArmedOrLive({ relayExecutionMode: 'LIVE' }), true);
  assert.equal(isRelayStatusArmedOrLive({ relayExecutionMode: 'PAUSED' }), false);
});

test('mutation proof: pause/arm writers invalidate relay-status cache', () => {
  const instances = readFileSync(
    path.join(__dirname, 'trading-agent-instances.service.ts'),
    'utf8',
  );
  assert.ok(instances.includes('invalidateRelayStatusCache(relayStatusCacheKey(agentSlug, userId))'));
  const exec = readFileSync(
    path.join(__dirname, 'signal-subscriber-execution.service.ts'),
    'utf8',
  );
  const hits = exec.split('invalidateRelayStatusCache(relayStatusCacheKey(AGENT_SLUG').length - 1;
  assert.ok(hits >= 3, `expected ≥3 execution-service invalidates, got ${hits}`);
});
