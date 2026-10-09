import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assertBotAdminToken, parseBitfinexKeyPermissions } from './live-copy-ops.service';

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
