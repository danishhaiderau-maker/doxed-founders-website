export type TwoLedgerState = {
  spendable: number;
  lifetime: number;
};

/** Pure two-ledger transitions — unit-tested invariant holder. */
export function applyAward(state: TwoLedgerState, amount: number): TwoLedgerState {
  if (amount <= 0) return state;
  return {
    spendable: state.spendable + amount,
    lifetime: state.lifetime + amount,
  };
}

export function applySpend(state: TwoLedgerState, amount: number): TwoLedgerState {
  if (amount <= 0) return state;
  if (state.spendable < amount) {
    throw new Error('Insufficient spendable DDollar balance');
  }
  return {
    spendable: state.spendable - amount,
    lifetime: state.lifetime,
  };
}

export function assertLifetimeMonotonic(before: TwoLedgerState, after: TwoLedgerState): void {
  if (after.lifetime < before.lifetime) {
    throw new Error('Lifetime contribution must never decrease');
  }
}

export function assertSpendPreservesLifetime(before: TwoLedgerState, after: TwoLedgerState): void {
  if (after.lifetime !== before.lifetime) {
    throw new Error('Spend must not change lifetime contribution');
  }
}
