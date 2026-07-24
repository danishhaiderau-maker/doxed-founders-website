import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BITFINEX_BTC_PERP_SYMBOL,
  parseActiveOrdersPayload,
  parseOpenPositionPayload,
} from './bitfinex-api.client';

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
