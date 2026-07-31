import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isFreshBotSnapshot,
  probePublicBotHealth,
  summarizeCanonicalBotHealth,
  type ProbeFetch,
} from './public-bot-health-probe';

test('cached snapshots count as connected only while their own integrity timestamp is fresh', () => {
  assert.equal(
    isFreshBotSnapshot({ state_integrity: { snapshot_age_sec: 12, rest_healthy: true } }),
    true,
  );
  assert.equal(
    isFreshBotSnapshot({ state_integrity: { snapshot_age_sec: 120, rest_healthy: true } }),
    false,
  );
  assert.equal(
    isFreshBotSnapshot({ state_integrity: { snapshot_age_sec: 12, rest_healthy: false } }),
    false,
  );
});

test('direct health probe reports the named host online only after a real 2xx', async () => {
  const calls: string[] = [];
  const fetcher: ProbeFetch = async (url) => {
    calls.push(url);
    if (url.endsWith('/ready')) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { ok: true, dashboard_owner: true, source_git_rev: 'abc123' };
        },
      };
    }
    throw new Error('not needed');
  };

  const result = await probePublicBotHealth('https://doxed-btc-bot.fly.dev/', fetcher, 100);
  assert.equal(result.ok, true);
  assert.equal(result.url, 'https://doxed-btc-bot.fly.dev');
  assert.equal(result.payload?.dashboard_owner, true);
  assert.ok(calls.some((url) => url.endsWith('/ready')));
});

test('direct health probe never converts failed requests into a false online state', async () => {
  const fetcher: ProbeFetch = async () => ({
    ok: false,
    status: 503,
    async json() {
      return {};
    },
  });
  const result = await probePublicBotHealth('https://doxed-btc-bot.fly.dev', fetcher, 100);
  assert.deepEqual(result, {
    ok: false,
    url: 'https://doxed-btc-bot.fly.dev',
    error: 'direct health probe failed',
  });
});

test('canonical health accepts only the exact Fly probe or a fresh authenticated snapshot', () => {
  const failedFly = {
    ok: false,
    url: 'https://doxed-btc-bot.fly.dev',
    error: 'direct health probe failed',
  };
  const stale = summarizeCanonicalBotHealth(failedFly, {
    state_integrity: { snapshot_age_sec: 180, rest_healthy: true },
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.botConnected, false);
  assert.equal(stale.source, 'stale-signed-snapshot');
  assert.equal('cloudflare' in stale, false);

  const snapshot = summarizeCanonicalBotHealth(failedFly, {
    state_integrity: { snapshot_age_sec: 5, rest_healthy: true },
  });
  assert.equal(snapshot.ok, true);
  assert.equal(snapshot.snapshotFresh, true);
  assert.equal(snapshot.source, 'signed-snapshot-cache');

  const fly = summarizeCanonicalBotHealth(
    { ok: true, url: 'https://doxed-btc-bot.fly.dev', status: 200 },
    null,
  );
  assert.equal(fly.ok, true);
  assert.equal(fly.fly, true);
  assert.equal(fly.source, 'fly-direct');
});
