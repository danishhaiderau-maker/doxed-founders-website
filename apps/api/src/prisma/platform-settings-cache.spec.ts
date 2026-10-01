import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {
  PLATFORM_SETTINGS_CACHE_TTL_MS,
  cachedPlatformSettings,
  invalidatePlatformSettingsCache,
  setPlatformSettingsCacheClockForTests,
} from './platform-settings-cache';
import { loadSubscriberMaxMarginUsd } from '../trading-agents/subscriber-margin.util';
import { RateLimiterService } from '../events/rate-limiter.service';

function withClock(start = 1_000_000) {
  let now = start;
  setPlatformSettingsCacheClockForTests(() => now);
  invalidatePlatformSettingsCache();
  return {
    advance: (ms: number) => {
      now += ms;
    },
  };
}

test.afterEach(() => {
  setPlatformSettingsCacheClockForTests(null);
  invalidatePlatformSettingsCache();
});

test('TTL is bounded to 30-60 s by default', () => {
  assert.ok(PLATFORM_SETTINGS_CACHE_TTL_MS >= 1_000 && PLATFORM_SETTINGS_CACHE_TTL_MS <= 60_000);
  assert.equal(PLATFORM_SETTINGS_CACHE_TTL_MS, 30_000);
});

test('serves cached value inside the TTL and reloads after it expires', async () => {
  const clock = withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return loads;
  };
  assert.equal(await cachedPlatformSettings('k', load), 1);
  clock.advance(PLATFORM_SETTINGS_CACHE_TTL_MS - 1);
  assert.equal(await cachedPlatformSettings('k', load), 1);
  assert.equal(loads, 1);
  clock.advance(1);
  assert.equal(await cachedPlatformSettings('k', load), 2);
  assert.equal(loads, 2);
});

test('admin-write invalidation forces the next read to reload', async () => {
  withClock();
  let value = 20;
  const load = async () => value;
  assert.equal(await cachedPlatformSettings('margin', load), 20);
  value = 5;
  assert.equal(await cachedPlatformSettings('margin', load), 20);
  invalidatePlatformSettingsCache();
  assert.equal(await cachedPlatformSettings('margin', load), 5);
});

test('a load that started before an invalidation never repopulates the cache', async () => {
  withClock();
  let release: (v: number) => void = () => undefined;
  const slow = cachedPlatformSettings('k', () => new Promise<number>((r) => { release = r; }));
  invalidatePlatformSettingsCache();
  release(1);
  assert.equal(await slow, 1);
  assert.equal(await cachedPlatformSettings('k', async () => 2), 2);
});

test('concurrent callers share one in-flight load', async () => {
  withClock();
  let loads = 0;
  const load = async () => {
    loads += 1;
    await new Promise((r) => setTimeout(r, 5));
    return 'v';
  };
  await Promise.all([cachedPlatformSettings('k', load), cachedPlatformSettings('k', load)]);
  assert.equal(loads, 1);
});

test('failed loads are not cached', async () => {
  withClock();
  await assert.rejects(() => cachedPlatformSettings('k', async () => {
    throw new Error('P1001 connection refused');
  }));
  assert.equal(await cachedPlatformSettings('k', async () => 'ok'), 'ok');
});

test('margin cap selects only its column and is cached per TTL', async () => {
  const clock = withClock();
  const calls: unknown[] = [];
  const prisma = {
    platformSettings: {
      findUnique: async (args: unknown) => {
        calls.push(args);
        return { subscriberMaxMarginUsd: 7 };
      },
    },
  };
  const original = process.env.SUBSCRIBER_MAX_MARGIN_USD;
  delete process.env.SUBSCRIBER_MAX_MARGIN_USD;
  try {
    const first = await loadSubscriberMaxMarginUsd(prisma as never);
    await loadSubscriberMaxMarginUsd(prisma as never);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0], {
      where: { id: 'default' },
      select: { subscriberMaxMarginUsd: true },
    });
    clock.advance(PLATFORM_SETTINGS_CACHE_TTL_MS);
    assert.equal(await loadSubscriberMaxMarginUsd(prisma as never), first);
    assert.equal(calls.length, 2);
  } finally {
    if (original !== undefined) process.env.SUBSCRIBER_MAX_MARGIN_USD = original;
  }
});

test('rate limiter selects only the limit columns and caches them', async () => {
  withClock();
  const settingsCalls: unknown[] = [];
  const prisma = {
    platformSettings: {
      findUnique: async (args: unknown) => {
        settingsCalls.push(args);
        return { rateLimitDaily: 50, rateLimitHourly: 10 };
      },
    },
    rateLimit: {
      aggregate: async () => ({ _sum: { count: 0 } }),
      upsert: async () => ({}),
    },
  };
  const limiter = new RateLimiterService(prisma as never);
  await limiter.checkLimit('u1', 'copilot');
  await limiter.checkLimit('u1', 'copilot');
  assert.equal(settingsCalls.length, 1);
  assert.deepEqual(settingsCalls[0], {
    where: { id: 'default' },
    select: { rateLimitDaily: true, rateLimitHourly: true },
  });
});

function listSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...listSourceFiles(full));
    else if (name.endsWith('.ts') && !name.endsWith('.spec.ts') && !name.endsWith('.test.ts')) {
      out.push(full);
    }
  }
  return out;
}

function callArguments(source: string, openParen: number): string {
  let depth = 0;
  for (let i = openParen; i < source.length; i += 1) {
    if (source[i] === '(') depth += 1;
    else if (source[i] === ')') {
      depth -= 1;
      if (depth === 0) return source.slice(openParen + 1, i);
    }
  }
  return source.slice(openParen + 1);
}

test('every PlatformSettings query in the API selects explicit columns', () => {
  const srcRoot = path.resolve(__dirname, '..');
  const pattern =
    /platformSettings\s*\.\s*(findUnique|findFirst|findUniqueOrThrow|findFirstOrThrow|findMany|upsert|update|create)\s*\(/g;
  const offenders: string[] = [];
  let checked = 0;
  for (const file of listSourceFiles(srcRoot)) {
    const source = readFileSync(file, 'utf8');
    for (const match of source.matchAll(pattern)) {
      checked += 1;
      const open = (match.index ?? 0) + match[0].length - 1;
      const args = callArguments(source, open);
      if (!/\bselect\s*:/.test(args)) {
        const line = source.slice(0, match.index).split('\n').length;
        offenders.push(`${path.relative(srcRoot, file)}:${line} ${match[1]}`);
      }
    }
  }
  assert.ok(checked >= 20, `expected to inspect the PlatformSettings call sites, saw ${checked}`);
  assert.deepEqual(offenders, [], `PlatformSettings calls without select:\n${offenders.join('\n')}`);
});
