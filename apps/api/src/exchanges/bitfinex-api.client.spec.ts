import assert from 'node:assert/strict';
import test from 'node:test';
import './bitfinex-auth-trade-stream.spec';
import {
  BITFINEX_BTC_PERP_SYMBOL,
  BitfinexTradingClient,
  allocateBitfinexAuthNonce,
  bitfinexAuthPost,
  parseActiveOrdersPayload,
  parseOpenPositionPayload,
  parseOrderHistoryEvidence,
  parseOrderTrade,
  normalizeBitfinexOrderPrice,
} from './bitfinex-api.client';

test('Bitfinex stop prices use the authenticated five-significant-digit venue value', () => {
  assert.equal(normalizeBitfinexOrderPrice(63_066.88), 63_066);
  assert.equal(normalizeBitfinexOrderPrice(9_876.543), 9_876.5);
  assert.equal(normalizeBitfinexOrderPrice(0.01234567), 0.012345);
});

test('authenticated order-trade parsing preserves immutable fill identity and explicit fee facts', () => {
  const row = [998877, 'tBTCF0:USTF0', 1_723_000_000_000, 241234567890, -0.01, 64_000, 'LIMIT', 64_000, 1, -0.00002, 'UST'];
  assert.deepEqual(parseOrderTrade(row), {
    id: 998877,
    orderId: 241234567890,
    execPrice: 64_000,
    execAmount: -0.01,
    fee: -0.00002,
    mtsCreate: 1_723_000_000_000,
    feeCurrency: 'UST',
  });
  assert.equal(parseOrderTrade([null, 'tBTCF0:USTF0', 1, 2, 0.01, 64_000, '', 0, 0, 0, 'UST']), null);
});

test('REST and WS nonce allocations share one strictly monotonic api-key sequence', () => {
  const values = Array.from({ length: 20 }, () => BigInt(allocateBitfinexAuthNonce('shared-nonce-key')));
  for (let index = 1; index < values.length; index++) assert.ok(values[index] > values[index - 1]);
});

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

test('order-history evidence distinguishes terminal unfilled cancellation from execution', () => {
  const cancelled = activeOrderRow();
  cancelled[6] = -0.0005;
  cancelled[7] = -0.0005;
  cancelled[13] = 'CANCELED';
  const evidence = parseOrderHistoryEvidence([cancelled], 241234567890);
  assert.equal(evidence?.terminal, true);
  assert.equal(evidence?.filledQty, 0);

  const executed = activeOrderRow();
  executed[6] = 0;
  executed[7] = -0.0005;
  executed[13] = 'EXECUTED @ 65000';
  assert.ok((parseOrderHistoryEvidence([executed], 241234567890)?.filledQty ?? 0) > 0);
  assert.equal(parseOrderHistoryEvidence([cancelled], 999), null);
  assert.throws(() => parseOrderHistoryEvidence({ ok: true }, 1), /not an array/);
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

test('queued order mutation takes priority over queued reconciliation reads', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  let releaseFirstRead: (() => void) | undefined;
  let markFirstReadStarted: (() => void) | undefined;
  const firstReadFinished = new Promise<void>((resolve) => {
    releaseFirstRead = resolve;
  });
  const firstReadStarted = new Promise<void>((resolve) => {
    markFirstReadStarted = resolve;
  });
  globalThis.fetch = (async (url) => {
    const path = new URL(String(url)).pathname;
    calls.push(path);
    if (path.endsWith('/r/positions')) {
      markFirstReadStarted?.();
      await firstReadFinished;
    }
    return new Response('[]', { status: 200 });
  }) as typeof fetch;

  try {
    const firstRead = bitfinexAuthPost(testCreds, 'v2/auth/r/positions', {}, 1_000);
    await firstReadStarted;
    const queuedRead = bitfinexAuthPost(testCreds, 'v2/auth/r/orders', {}, 1_000);
    const queuedMutation = bitfinexAuthPost(
      testCreds,
      'v2/auth/w/order/submit',
      { type: 'LIMIT' },
      1_000,
    );

    releaseFirstRead?.();
    await Promise.all([firstRead, queuedMutation]);
    assert.deepEqual(calls.slice(0, 2), [
      '/v2/auth/r/positions',
      '/v2/auth/w/order/submit',
    ]);
    await queuedRead;
    assert.deepEqual(calls, [
      '/v2/auth/r/positions',
      '/v2/auth/w/order/submit',
      '/v2/auth/r/orders',
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('native limit update keeps the existing order id and sends only the in-place amend request', async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body ?? '{}')) });
    return new Response(JSON.stringify([Date.now(), 'ou-req', null, null, [241234567890], null, 'SUCCESS']), {
      status: 200,
    });
  }) as typeof fetch;

  try {
    const client = new BitfinexTradingClient();
    const id = await client.updateLimitOrder(
      { apiKey: 'native-update-test', apiSecret: 'not-a-real-secret', testnet: false },
      { orderId: 241234567890, direction: 'SHORT', qty: 0.03123, price: 63_500.12, leverage: 10 },
    );
    assert.equal(id, 241234567890);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, 'https://api.bitfinex.com/v2/auth/w/order/update');
    assert.deepEqual(requests[0].body, {
      id: 241234567890,
      amount: '-0.03123',
      price: '63500.12',
      lev: 10,
      meta: { aff_code: 'doxxedcrypto' },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
