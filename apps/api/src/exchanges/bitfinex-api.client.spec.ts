import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BITFINEX_BTC_PERP_SYMBOL,
  bitfinexAuthPost,
  parseActiveOrdersPayload,
  parseOpenPositionPayload,
} from './bitfinex-api.client';

const testCreds = {
  apiKey: 'nonce-lane-deadline-test',
  apiSecret: 'not-a-real-secret',
  testnet: false,
};

function activeOrderRow(symbol = BITFINEX_BTC_PERP_SYMBOL): unknown[] {
  const row = Array(32).fill(null);
  row[0] = 241234567890;
  row[2] = 123456789;
  row[3] = symbol;
  row[4] = Date.now();
  row[6] = -0.0003;
  row[7] = -0.0005;
  row[8] = 'LIMIT';
  row[13] = 'ACTIVE';
  row[16] = 65_000;
  return row;
}

test('active-order proof accepts only a valid array payload', () => {
  assert.throws(
    () => parseActiveOrdersPayload({ ok: true }),
    /not an array/,
  );
  assert.throws(
    () => parseActiveOrdersPayload([[241234567890]]),
    /row 0 is malformed/,
  );
  assert.deepEqual(parseActiveOrdersPayload([]), []);
  assert.equal(parseActiveOrdersPayload([activeOrderRow()]).length, 1);
  assert.equal(
    parseActiveOrdersPayload([activeOrderRow('tETHF0:USTF0')]).length,
    0,
  );
});

test('position proof rejects malformed successful payloads instead of reporting flat', () => {
  assert.throws(
    () => parseOpenPositionPayload({ ok: true }),
    /not an array/,
  );
  assert.throws(
    () => parseOpenPositionPayload([BITFINEX_BTC_PERP_SYMBOL]),
    /row 0 is malformed/,
  );
  assert.throws(
    () => parseOpenPositionPayload([[]]),
    /has no symbol/,
  );
  assert.throws(
    () =>
      parseOpenPositionPayload([
        [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', 'not-a-number', 65_000, 0, 0, 0, 0],
      ]),
    /invalid numeric fields/,
  );
  assert.throws(
    () =>
      parseOpenPositionPayload([
        [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', 0.000000001, 65_000, 0, 0, 0, 0],
      ]),
    /amount or base price is invalid/,
  );
  assert.throws(
    () =>
      parseOpenPositionPayload([
        [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', 0, 0, 0, 0, 0, 0],
      ]),
    /zero-amount position row/,
  );
  assert.throws(
    () =>
      parseOpenPositionPayload([
        [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', 0.00002, 65_000, 0, 0, 0, 0],
        [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', 0.00001, 65_010, 0, 0, 0, 0],
      ]),
    /duplicate/,
  );
  assert.equal(parseOpenPositionPayload([]), null);
});

test('position proof returns a valid exact eight-decimal BTC position', () => {
  assert.deepEqual(
    parseOpenPositionPayload([
      [BITFINEX_BTC_PERP_SYMBOL, 'ACTIVE', -0.00003999, 65_000, 0, 0, 0.25, 0.01],
    ]),
    {
      symbol: BITFINEX_BTC_PERP_SYMBOL,
      amount: -0.00003999,
      basePrice: 65_000,
      pnlUsd: 0.25,
      pnlPct: 0.01,
      direction: 'SHORT',
    },
  );
});

test('authenticated nonce lane bounds queue wait and never sends expired queued work later', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 60));
    return new Response('[]', { status: 200 });
  }) as typeof fetch;

  try {
    const first = bitfinexAuthPost(testCreds, 'v2/auth/r/positions', {}, 100);
    await new Promise((resolve) => setTimeout(resolve, 2));
    const queued = bitfinexAuthPost(testCreds, 'v2/auth/r/orders', {}, 20);

    await assert.rejects(
      queued,
      /authenticated request total deadline exceeded after 20ms/,
    );
    assert.equal(calls, 1, 'the expired queued request must not reach Bitfinex');
    await first;
    await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(calls, 1, 'the expired queued request must not execute after the lane drains');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
