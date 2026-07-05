/** Paper Bitfinex copy-relay simulation — Option B virtual lots, no real orders. */

export const COPY_RELAY_SIM_DEFAULT_BALANCE_USD = 500;
export const COPY_RELAY_SIM_RECONCILE_ALERT_BTC = 0.001;

/** Exchange qty below Bitfinex min lot is treated as flat for reconcile/alerts. */
export const COPY_RELAY_MIN_QTY_BTC = 0.00004;
export function effectiveExchangeQtyBtc(qty: number): number {
  const a = Math.abs(qty);
  return a < COPY_RELAY_MIN_QTY_BTC ? 0 : a;
}

export type CopyRelaySimOrder = {
  id: number;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  qty: number;
  price: number;
  amount: number;
  orderType: 'LIMIT' | 'STOP';
  createdAtMs: number;
};

export type CopyRelaySimPosition = {
  symbol: string;
  amount: number;
  basePrice: number;
};

export type CopyRelaySimLedger = {
  derivativesUsd: number;
  startingUsd: number;
  realizedPnlUsd: number;
  feesUsd: number;
  position: CopyRelaySimPosition | null;
  orders: CopyRelaySimOrder[];
  nextOrderId: number;
};

export type CopyRelayReconcileSnapshot = {
  exchangePositionQty: number;
  ledgerOpenQty: number;
  deltaBtc: number;
  alert: boolean;
  openLots: number;
  pendingLots: number;
  markPrice: number | null;
  updatedAt: string;
};

export type CopyRelaySimState = {
  active: boolean;
  startedAt: string | null;
  stoppedAt: string | null;
  ledger: CopyRelaySimLedger;
  reconcile: CopyRelayReconcileSnapshot | null;
  sessionPnlUsd: number;
  showcasePnlUsd: number | null;
  showcaseTradeCount: number | null;
  /**
   * Showcase bot's cumulative session P&L captured at sim start. Used as the
   * anchor so `showcasePnlUsd` can be reported as the DELTA since sim start
   * (current showcase session P&L - baseline) rather than a raw cumulative
   * that drifts the display away from "since you clicked Start".
   * Cleared (null) when the sim stops so the delta resets to 0.
   */
  showcasePnlBaselineUsd: number | null;
  /**
   * Real Bitfinex Derivatives (margin/USTF0) wallet TOTAL balance, read live
   * from the user's connected Bitfinex API credentials. This is the actual
   * wallet funding the real $20 sim orders — distinct from the paper $500
   * `ledger.derivativesUsd` sim balance. Null when the wallet snapshot could
   * not be read (credentials missing / API error) or sim is inactive.
   */
  realDerivativesWalletUsd: number | null;
  /**
   * Real Bitfinex Derivatives AVAILABLE (free margin) balance — the spendable
   * portion of `realDerivativesWalletUsd` not currently locked in positions.
   */
  realDerivativesAvailableUsd: number | null;
  /** ISO timestamp of the last real-wallet snapshot read. */
  realWalletSnapshotAt: string | null;
};

export function emptyCopyRelaySimLedger(
  startingUsd = COPY_RELAY_SIM_DEFAULT_BALANCE_USD,
): CopyRelaySimLedger {
  return {
    derivativesUsd: startingUsd,
    startingUsd,
    realizedPnlUsd: 0,
    feesUsd: 0,
    position: null,
    orders: [],
    nextOrderId: 900_000_001,
  };
}

export function emptyCopyRelaySimState(
  startingUsd = COPY_RELAY_SIM_DEFAULT_BALANCE_USD,
): CopyRelaySimState {
  return {
    active: false,
    startedAt: null,
    stoppedAt: null,
    ledger: emptyCopyRelaySimLedger(startingUsd),
    reconcile: null,
    sessionPnlUsd: 0,
    showcasePnlUsd: null,
    showcaseTradeCount: null,
    showcasePnlBaselineUsd: null,
    realDerivativesWalletUsd: null,
    realDerivativesAvailableUsd: null,
    realWalletSnapshotAt: null,
  };
}

export function readCopyRelaySimState(dash: unknown): CopyRelaySimState {
  if (!dash || typeof dash !== 'object') return emptyCopyRelaySimState();
  const raw = (dash as Record<string, unknown>).copyRelaySim;
  if (!raw || typeof raw !== 'object') return emptyCopyRelaySimState();
  const s = raw as Partial<CopyRelaySimState>;
  const ledger = s.ledger ?? emptyCopyRelaySimLedger();
  return {
    active: Boolean(s.active),
    startedAt: typeof s.startedAt === 'string' ? s.startedAt : null,
    stoppedAt: typeof s.stoppedAt === 'string' ? s.stoppedAt : null,
    ledger: {
      ...emptyCopyRelaySimLedger(ledger.startingUsd ?? COPY_RELAY_SIM_DEFAULT_BALANCE_USD),
      ...ledger,
      orders: Array.isArray(ledger.orders) ? ledger.orders : [],
    },
    reconcile: s.reconcile ?? null,
    sessionPnlUsd: typeof s.sessionPnlUsd === 'number' ? s.sessionPnlUsd : 0,
    showcasePnlUsd: typeof s.showcasePnlUsd === 'number' ? s.showcasePnlUsd : null,
    showcaseTradeCount: typeof s.showcaseTradeCount === 'number' ? s.showcaseTradeCount : null,
    showcasePnlBaselineUsd:
      typeof s.showcasePnlBaselineUsd === 'number' ? s.showcasePnlBaselineUsd : null,
    realDerivativesWalletUsd:
      typeof s.realDerivativesWalletUsd === 'number' ? s.realDerivativesWalletUsd : null,
    realDerivativesAvailableUsd:
      typeof s.realDerivativesAvailableUsd === 'number' ? s.realDerivativesAvailableUsd : null,
    realWalletSnapshotAt:
      typeof s.realWalletSnapshotAt === 'string' ? s.realWalletSnapshotAt : null,
  };
}

export function isCopyRelaySimActive(dash: unknown): boolean {
  return readCopyRelaySimState(dash).active;
}
