import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyAward,
  applySpend,
  assertLifetimeMonotonic,
  assertSpendPreservesLifetime,
  type TwoLedgerState,
} from './ddollar-ledger.logic';

describe('DDollar two-ledger invariants', () => {
  it('award increments spendable and lifetime equally', () => {
    const before: TwoLedgerState = { spendable: 100, lifetime: 500 };
    const after = applyAward(before, 50);
    assert.equal(after.spendable, 150);
    assert.equal(after.lifetime, 550);
    assertLifetimeMonotonic(before, after);
  });

  it('spend decrements spendable only — lifetime unchanged', () => {
    const before: TwoLedgerState = { spendable: 200, lifetime: 800 };
    const after = applySpend(before, 75);
    assert.equal(after.spendable, 125);
    assert.equal(after.lifetime, 800);
    assertSpendPreservesLifetime(before, after);
  });

  it('spend rejects insufficient spendable balance', () => {
    assert.throws(() => applySpend({ spendable: 10, lifetime: 1000 }, 50));
  });

  it('golden journey: earn then spend preserves lifetime', () => {
    let state: TwoLedgerState = { spendable: 0, lifetime: 0 };
    state = applyAward(state, 500);
    state = applyAward(state, 200);
    const beforeSpend = { ...state };
    state = applySpend(state, 139);
    assert.equal(state.lifetime, 700);
    assert.equal(state.spendable, 561);
    assertSpendPreservesLifetime(beforeSpend, state);
    assert.ok(state.lifetime >= state.spendable);
  });
});
