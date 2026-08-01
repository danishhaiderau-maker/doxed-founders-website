import assert from 'node:assert/strict';
import test from 'node:test';
import {
  describeOwnerFetchError,
  hasFullOwnerOrderState,
  isStrictExchangeOrderAuditFlat,
  isStrictRawFlatReconcileSnapshot,
} from './check-relay-flat.mjs';

const now = Date.parse('2026-07-24T05:45:00.000Z');

function rawFlat(overrides = {}) {
  return {
    rawExchangePositionQty: 0,
    dustPositionQty: 0,
    signedExchangePositionQty: 0,
    ledgerOpenQty: 0,
    signedLedgerOpenQty: 0,
    deltaBtc: 0,
    openLots: 0,
    pendingLots: 0,
    updatedAt: '2026-07-24T05:44:50.000Z',
    ...overrides,
  };
}

test('strict flat proof accepts a fresh complete raw-zero snapshot', () => {
  assert.equal(isStrictRawFlatReconcileSnapshot(rawFlat(), now), true);
});

test('strict flat proof rejects legacy effective-zero snapshots', () => {
  const legacy = rawFlat();
  delete legacy.rawExchangePositionQty;
  delete legacy.dustPositionQty;
  delete legacy.signedExchangePositionQty;
  delete legacy.signedLedgerOpenQty;
  legacy.exchangePositionQty = 0;
  assert.equal(isStrictRawFlatReconcileSnapshot(legacy, now), false);
});

test('strict flat proof rejects one satoshi, dust, and stale observations', () => {
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ rawExchangePositionQty: 0.00000001, signedExchangePositionQty: -0.00000001 }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ dustPositionQty: 0.00003999 }), now),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(
      rawFlat({ updatedAt: '2026-07-24T05:43:59.999Z' }),
      now,
    ),
    false,
  );
  assert.equal(
    isStrictRawFlatReconcileSnapshot(rawFlat({ rawExchangePositionQty: null }), now),
    false,
  );
});

test('authenticated source proof rejects a sanitized state without an order book', () => {
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, positions: [] }),
    false,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, pending_orders: [] }),
    true,
  );
  assert.equal(
    hasFullOwnerOrderState({ dashboard_owner: true, orders: [] }),
    true,
  );
});

test('owner timeout explains Fly routing and health-check failure', () => {
  const error = new Error('The operation was aborted due to timeout');
  error.name = 'TimeoutError';
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/state',
    15_000,
  );
  assert.match(described.message, /timed out after 15000ms/);
  assert.match(described.message, /doxed-btc-bot\.fly\.dev\/api\/state/);
  assert.match(described.message, /critical service check removed public routing/);
});

test('owner non-timeout failure retains URL and original diagnosis', () => {
  const error = new Error('showcase HTTP 503');
  const described = describeOwnerFetchError(
    error,
    'https://doxed-btc-bot.fly.dev/api/state',
    15_000,
  );
  assert.match(described.message, /request failed/);
  assert.match(described.message, /showcase HTTP 503/);
});

test('strict exchange order proof requires a fresh known zero-order snapshot', () => {
  const flatAudit = {
    known: true,
    activeOrderCount: 0,
    managedActiveOrderCount: 0,
    foreignActiveOrderCount: 0,
    checkedAt: '2026-07-24T05:44:50.000Z',
  };
  assert.equal(isStrictExchangeOrderAuditFlat(flatAudit, now), true);
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, activeOrderCount: 1 },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, known: false },
      now,
    ),
    false,
  );
  assert.equal(
    isStrictExchangeOrderAuditFlat(
      { ...flatAudit, checkedAt: '2026-07-24T05:43:59.999Z' },
      now,
    ),
    false,
  );
});
