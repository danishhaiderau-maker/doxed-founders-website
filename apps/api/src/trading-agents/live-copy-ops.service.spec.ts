import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assertBotAdminToken, parseBitfinexKeyPermissions, readLiveExchangeExposure } from './live-copy-ops.service';

test('parses Bitfinex key permissions and exposes withdraw/trade flags', () => {
  const p = parseBitfinexKeyPermissions([['account', 1, 0], ['orders', 1, 1], ['withdraw', 0, 0], ['wallets', 1, 0]]);
  assert.deepEqual(p?.withdraw, { read: false, write: false });
  assert.deepEqual(p?.orders, { read: true, write: true });
  assert.equal(parseBitfinexKeyPermissions(null), null);
  assert.equal(parseBitfinexKeyPermissions([]), null);
});

test('admin token is required and compared exactly', () => {
  const prev = process.env.BOT_ADMIN_TOKEN;
  try {
    delete process.env.BOT_ADMIN_TOKEN;
    assert.throws(() => assertBotAdminToken('x'));
    process.env.BOT_ADMIN_TOKEN = 'tok-123';
    assert.throws(() => assertBotAdminToken('tok-12'));
    assert.throws(() => assertBotAdminToken(undefined, undefined));
    assert.doesNotThrow(() => assertBotAdminToken('tok-123'));
    assert.doesNotThrow(() => assertBotAdminToken(undefined, 'Bearer tok-123'));
  } finally {
    if (prev === undefined) delete process.env.BOT_ADMIN_TOKEN; else process.env.BOT_ADMIN_TOKEN = prev;
  }
});

test('R8-live: exchange net position and open orders are read live via /v2/auth/r/ only, no key material', async () => {
  const paths: string[] = [];
  const creds = { apiKey: 'k-secret', apiSecret: 's-secret' } as never;
  const post = (async (_c: unknown, path: string) => {
    paths.push(path);
    if (path === 'v2/auth/r/positions') {
      return [['tETHF0:USTF0', 'ACTIVE', 1, 2000, 0, 0, 0, 0], ['tBTCF0:USTF0', 'ACTIVE', -0.002, 65000, 0, 0, 1.5, 0.1]];
    }
    return [];
  }) as never;
  const out = await readLiveExchangeExposure(creds, post);
  assert.deepEqual(paths, ['v2/auth/r/positions', 'v2/auth/r/orders']);
  assert.ok(paths.every((p) => p.startsWith('v2/auth/r/')));
  assert.equal(out.net_position_btc, -0.002);
  assert.equal(out.open_order_count, 0);
  assert.equal(out.cached, false);
  assert.ok(!JSON.stringify(out).includes('secret'));
  const failing = (async () => { throw new Error('boom'); }) as never;
  const bad = await readLiveExchangeExposure(creds, failing);
  assert.equal(bad.net_position_btc, null);
  assert.equal(bad.open_order_count, null);
});
