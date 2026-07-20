import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canonicalPendingIntentCycles,
  isBenignShowcaseEntryWait,
  readFreshSignedShowcaseExactLimit,
  readSignedShowcaseClose,
  shouldPersistLotMetaRepair,
} from './signal-subscriber-execution.service';

test('repairs a legacy catch-up lot that has qty but no direction', () => {
  assert.equal(
    shouldPersistLotMetaRepair({ qty: 0.03104 }, { qty: 0.03104, direction: 'SHORT' }),
    true,
  );
});

test('repairs missing quantity when direction is already durable', () => {
  assert.equal(
    shouldPersistLotMetaRepair({ direction: 'LONG' }, { qty: 0.02, direction: 'LONG' }),
    true,
  );
});

test('does not append duplicate repair events for complete metadata', () => {
  assert.equal(
    shouldPersistLotMetaRepair(
      { qty: 0.02, direction: 'LONG' },
      { qty: 0.02, direction: 'LONG' },
    ),
    false,
  );
});

for (const message of [
  'Showcase trade is not present in the current canonical book.',
  'Waiting for the showcase to publish its exact resting limit.',
  'Showcase filled before copy entry; waiting for the catch-up reconciler.',
  'Showcase bot has not placed a limit yet (virtual defer / chase bucket) — relay waiting.',
  'Waiting for showcase limit.',
]) {
  test(`classifies expected strategy wait text as non-error: ${message}`, () => {
    assert.equal(isBenignShowcaseEntryWait(message), true);
  });
}

for (const message of [
  'Showcase bridge unavailable — exact-copy entry blocked.',
  'RECONCILE ALERT: exchange 0.03 BTC ≠ ledger 0 BTC',
  'CANCEL_FAILED_ORDER_STILL_LIVE',
  null,
]) {
  test(`preserves operational and safety errors: ${message}`, () => {
    assert.equal(isBenignShowcaseEntryWait(message), false);
  });
}

test('ignores stale intents and selects only trades resting in the canonical order book', () => {
  const stale = { tradeId: 'cont-stale', createdAt: new Date('2026-07-19T12:00:00Z') };
  const live = { tradeId: 'cont-live', createdAt: new Date('2026-07-19T12:05:00Z') };
  const result = canonicalPendingIntentCycles([stale, live], {
    orders: [
      {
        trade_id: 'cont-live',
        status: 'PENDING',
        limit_price: 64_500,
      },
    ],
  });
  assert.deepEqual(result, [live]);
});

test('rejects terminal or price-less canonical orders', () => {
  const cycles = [
    { tradeId: 'cont-filled', createdAt: new Date('2026-07-19T12:00:00Z') },
    { tradeId: 'cont-no-price', createdAt: new Date('2026-07-19T12:01:00Z') },
  ];
  const result = canonicalPendingIntentCycles(cycles, {
    orders: [
      { trade_id: 'cont-filled', status: 'FILLED', limit_price: 64_500 },
      { trade_id: 'cont-no-price', status: 'PENDING', limit_price: 0 },
    ],
  });
  assert.deepEqual(result, []);
});

test('preserves canonical book order when more than one live intent is ready', () => {
  const firstCreated = { tradeId: 'cont-a', createdAt: new Date('2026-07-19T12:00:00Z') };
  const secondCreated = { tradeId: 'cont-b', createdAt: new Date('2026-07-19T12:01:00Z') };
  const result = canonicalPendingIntentCycles([firstCreated, secondCreated], {
    orders: [
      { trade_id: 'cont-b', status: 'ORDERED', limit_price: 64_600 },
      { trade_id: 'cont-a', status: 'PENDING', limit_price: 64_500 },
    ],
  });
  assert.deepEqual(result, [secondCreated, firstCreated]);
});

test('accepts a fresh HMAC-verified exact showcase resting limit', () => {
  const now = Date.parse('2026-07-20T01:02:03.000Z');
  assert.deepEqual(
    readFreshSignedShowcaseExactLimit(
      'cont-fast',
      {
        schema: 'dcf-signal-intent/v1',
        action: 'ENTER',
        direction: 'SHORT',
        entry: { exact_limit_price: 64_555.25 },
        context: {
          signed_showcase_event: true,
          showcase_event: 'ORDER_PLACED',
          platform_received_at: '2026-07-20T01:02:02.250Z',
        },
      },
      now,
    ),
    {
      tradeId: 'cont-fast',
      direction: 'SHORT',
      limitPrice: 64_555.25,
      receivedAtMs: Date.parse('2026-07-20T01:02:02.250Z'),
    },
  );
});

test('rejects unsigned, stale, and non-resting exact-limit events', () => {
  const now = Date.parse('2026-07-20T01:02:30.000Z');
  const base = {
    schema: 'dcf-signal-intent/v1',
    action: 'ENTER',
    direction: 'LONG',
    entry: { exact_limit_price: 64_500 },
    context: {
      signed_showcase_event: true,
      showcase_event: 'ORDER_PLACED',
      platform_received_at: '2026-07-20T01:02:29.000Z',
    },
  };
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-unsigned', {
      ...base,
      context: { ...base.context, signed_showcase_event: false },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-stale', {
      ...base,
      context: {
        ...base.context,
        platform_received_at: '2026-07-20T01:02:00.000Z',
      },
    }, now),
    null,
  );
  assert.equal(
    readFreshSignedShowcaseExactLimit('cont-approve', {
      ...base,
      context: { ...base.context, showcase_event: 'APPROVE_PENDING' },
    }, now),
    null,
  );
});

test('reads a signed showcase close without another canonical-state fetch', () => {
  assert.deepEqual(
    readSignedShowcaseClose({
      context: {
        signed_showcase_event: true,
        showcase_event: 'POSITION_CLOSED',
        showcase_exit_price: 64_444.25,
        showcase_exit_reason: 'PROFIT_LOCK_LADDER',
      },
    }),
    {
      exitPrice: 64_444.25,
      exitReason: 'PROFIT_LOCK_LADDER',
    },
  );
  assert.equal(
    readSignedShowcaseClose({
      context: {
        signed_showcase_event: false,
        showcase_event: 'POSITION_CLOSED',
      },
    }),
    null,
  );
});
