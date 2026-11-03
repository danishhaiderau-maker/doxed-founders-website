/**
 * RK 22:14 / HM 9754e839380f: relay-status cache — disarmed only; armed/live
 * always bypass; pause/arm invalidation outside hash-locked files.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  RELAY_STATUS_CACHE_TTL_MS,
  cachedRelayStatus,
  invalidateRelayStatusCache,
  invalidateRelayStatusCacheForUser,
  isRelayStatusArmedOrLive,
  relayStatusCacheKey,
  setRelayStatusCacheClockForTests,
  tradingAgentInstanceWriteTouchesArmOrPause,
} from './relay-status-cache';
import { installRelayStatusCachePrismaHooks } from './relay-status-cache-prisma-hooks';

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

test('serves cached disarmed value inside TTL and reloads after expiry', async () => {
  const clock = withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return { relayArmedAt: null, relayExecutionMode: 'PAUSED', n: loads };
  };
  const key = relayStatusCacheKey('slug', 'user');
  assert.equal((await cachedRelayStatus(key, load)).n, 1);
  clock.advance(RELAY_STATUS_CACHE_TTL_MS - 1);
  assert.equal((await cachedRelayStatus(key, load)).n, 1);
  assert.equal(loads, 1);
  clock.advance(1);
  assert.equal((await cachedRelayStatus(key, load)).n, 2);
  assert.equal(loads, 2);
});

test('invalidate forces the next read to reload', async () => {
  withClock();
  let value = { relayArmedAt: null, v: 'a' };
  const key = 'k';
  assert.equal((await cachedRelayStatus(key, async () => value)).v, 'a');
  value = { relayArmedAt: null, v: 'b' };
  assert.equal((await cachedRelayStatus(key, async () => value)).v, 'a');
  invalidateRelayStatusCache(key);
  assert.equal((await cachedRelayStatus(key, async () => value)).v, 'b');
});

test('invalidateRelayStatusCacheForUser drops every slug for that user', async () => {
  withClock();
  await cachedRelayStatus(relayStatusCacheKey('a', 'u1'), async () => ({ relayArmedAt: null, n: 1 }));
  await cachedRelayStatus(relayStatusCacheKey('b', 'u1'), async () => ({ relayArmedAt: null, n: 1 }));
  await cachedRelayStatus(relayStatusCacheKey('a', 'u2'), async () => ({ relayArmedAt: null, n: 1 }));
  invalidateRelayStatusCacheForUser('u1');
  let loads = 0;
  await cachedRelayStatus(relayStatusCacheKey('a', 'u1'), async () => {
    loads += 1;
    return { relayArmedAt: null, n: loads };
  });
  assert.equal(loads, 1);
  loads = 0;
  await cachedRelayStatus(relayStatusCacheKey('a', 'u2'), async () => {
    loads += 1;
    return { relayArmedAt: null, n: loads };
  });
  assert.equal(loads, 0, 'u2 cache must survive u1 invalidation');
});

test('concurrent callers share one in-flight load', async () => {
  withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    await new Promise((r) => setTimeout(r, 5));
    return { relayArmedAt: null, v: 'v' };
  };
  await Promise.all([cachedRelayStatus('k', load), cachedRelayStatus('k', load)]);
  assert.equal(loads, 1);
});

test('RK 22:14 / HM: armed/live payloads always bypass cache', async () => {
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

test('tradingAgentInstanceWriteTouchesArmOrPause detects pause/arm fields', () => {
  assert.equal(tradingAgentInstanceWriteTouchesArmOrPause({ status: 'PAUSED' }), true);
  assert.equal(
    tradingAgentInstanceWriteTouchesArmOrPause({
      dashboardState: { relayArmedAt: null },
    }),
    true,
  );
  assert.equal(tradingAgentInstanceWriteTouchesArmOrPause({ lastError: 'x' }), false);
});

test('Prisma hooks invalidate on arm/pause writes (outside locked files)', async () => {
  withClock();
  const key = relayStatusCacheKey('conservative-btc', 'user-1');
  await cachedRelayStatus(key, async () => ({ relayArmedAt: null, n: 1 }));

  let updateCalls = 0;
  const prisma = {
    tradingAgentInstance: {
      update: async (args: unknown) => {
        updateCalls += 1;
        return { userId: 'user-1', ...(args as object) };
      },
      updateMany: async () => ({ count: 0 }),
    },
  };
  installRelayStatusCachePrismaHooks(prisma);
  await prisma.tradingAgentInstance.update({
    where: { id: 'i1' },
    data: { status: 'PAUSED', dashboardState: { relayArmedAt: null, relayExecutionMode: 'PAUSED' } },
  });
  assert.equal(updateCalls, 1);

  let loads = 0;
  await cachedRelayStatus(key, async () => {
    loads += 1;
    return { relayArmedAt: null, n: loads };
  });
  assert.equal(loads, 1, 'pause write must invalidate cached disarmed snapshot');
});

test('mutation proof: locked execution file has no relay-status-cache import', () => {
  const exec = readFileSync(
    path.join(__dirname, 'signal-subscriber-execution.service.ts'),
    'utf8',
  );
  assert.equal(exec.includes('relay-status-cache'), false);
  assert.equal(exec.includes('invalidateRelayStatusCache'), false);
});

test('mutation proof: PrismaService installs relay-status cache hooks', () => {
  const src = readFileSync(path.join(__dirname, '../prisma/prisma.service.ts'), 'utf8');
  assert.ok(src.includes('installRelayStatusCachePrismaHooks'));
});

test('mutation proof: setInstancePaused still invalidates (non-locked)', () => {
  const instances = readFileSync(
    path.join(__dirname, 'trading-agent-instances.service.ts'),
    'utf8',
  );
  assert.ok(instances.includes('invalidateRelayStatusCache(relayStatusCacheKey(agentSlug, userId))'));
});
