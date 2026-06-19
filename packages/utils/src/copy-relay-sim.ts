/** Paper Bitfinex copy-relay simulation — Option B virtual lots, no real orders. */

export const COPY_RELAY_SIM_DEFAULT_BALANCE_USD = 500;
export const COPY_RELAY_SIM_RECONCILE_ALERT_BTC = 0.001;

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
  };
}

export function isCopyRelaySimActive(dash: unknown): boolean {
  return readCopyRelaySimState(dash).active;
}
