import assert from 'node:assert/strict';
import test from 'node:test';
import { isStrictRawFlatReconcileSnapshot } from './check-relay-flat.mjs';

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
