import test from 'node:test';
import assert from 'node:assert/strict';
import {
  bitfinexPermissionsReadOnly, verifyBitfinexReadOnlyKey, isBitfinexAuthRead, bitfinexReadKeyActive,
  setBitfinexReadKeyForTests,
} from './bitfinex-api.client';

const trading = { apiKey: 'T', apiSecret: 'TS' } as never;

test('1500: permissions parser - any write scope is not read-only', () => {
  assert.deepEqual(bitfinexPermissionsReadOnly([['orders', 1, 0], ['wallets', 1, 0]]), { readOnly: true, writeScopes: [] });
  assert.deepEqual(bitfinexPermissionsReadOnly([['orders', 1, 1], ['withdraw', 0, 1]]).writeScopes, ['orders', 'withdraw']);
  assert.equal(bitfinexPermissionsReadOnly(null).readOnly, false);
});

test('1500: only v2/auth/r reads route to the read key', () => {
  assert.equal(isBitfinexAuthRead('v2/auth/r/positions'), true);
  assert.equal(isBitfinexAuthRead('v2/auth/w/order/submit'), false);
});

test('1500: absent vars -> unused (current behaviour)', async () => {
  assert.equal(await verifyBitfinexReadOnlyKey(trading, {}), false);
  assert.equal(bitfinexReadKeyActive(), false);
});

test('1500: write permission -> refuse to start', async () => {
  const post = (async (_c: unknown, path: string) => path.endsWith('permissions') ? [['orders', 1, 1]] : [1]) as never;
  await assert.rejects(verifyBitfinexReadOnlyKey(trading, { BFX_READ_API_KEY: 'R', BFX_READ_API_SECRET: 'RS' }, post), /not read-only/);
  assert.equal(bitfinexReadKeyActive(), false);
});

test('1500: read-only + same user -> active; different user -> unused', async () => {
  const mk = (other: number) => (async (c: { apiKey: string }, path: string) =>
    path.endsWith('permissions') ? [['orders', 1, 0]] : [c.apiKey === 'R' ? other : 42]) as never;
  assert.equal(await verifyBitfinexReadOnlyKey(trading, { BFX_READ_API_KEY: 'R', BFX_READ_API_SECRET: 'RS' }, mk(42)), true);
  assert.equal(bitfinexReadKeyActive(), true);
  assert.equal(await verifyBitfinexReadOnlyKey(trading, { BFX_READ_API_KEY: 'R', BFX_READ_API_SECRET: 'RS' }, mk(7)), false);
  setBitfinexReadKeyForTests(null);
});
