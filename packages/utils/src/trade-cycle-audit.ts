/** Append-only trade cycle audit trail for copy relay production safety. */

export const TRADE_CYCLE_AUDIT_STAGES = [
  'SIGNAL',
  'APPROVED',
  'CAPACITY_REJECT',
  'PAUSED_ENTRY_BLOCKED',
  'ORDER_PLACED',
  'FILLED',
  'OPEN',
  'PEAK_UPDATE',
  'PROFIT_LOCK',
  'EXIT',
  'CLOSED',
  'RECONCILE',
  'EXPIRED',
] as const;

export type TradeCycleAuditStage = (typeof TRADE_CYCLE_AUDIT_STAGES)[number];

export type TradeCycleAuditEntry = {
  ts: string;
  stage: TradeCycleAuditStage;
  userId: string;
  agentId?: string;
  cycleId?: string;
  participantId?: string;
  tradeId?: string;
  venue?: string;
  detail?: string;
  meta?: Record<string, unknown>;
};

export type TradeCycleAnomaly = {
  kind: string;
  userId: string;
  participantId?: string;
  cycleId?: string;
  detail: string;
};
