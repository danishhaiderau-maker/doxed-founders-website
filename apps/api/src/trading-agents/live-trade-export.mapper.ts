import type { SignalCycleStatus } from '@prisma/client';
import { formatMelbourneDateTime } from '@dcf/utils';

type IntentEnvelope = {
  direction?: string;
  limit_price?: number;
  signal_price?: number;
  regime?: string;
  strategy?: string;
  risk?: { max_margin_usd?: number; leverage_hint?: number };
  entry?: { offset_pct?: number };
};

export type LiveTradeExportEvent = {
  timestamp: string;
  eventType: string;
  limitPrice: number | null;
  fillPrice: number | null;
  exitPrice: number | null;
  exitReason: string | null;
  pnlUsd: number | null;
  pnlMarginPct: number | null;
  bitfinexOrderId: string | null;
  detail: string | null;
};

export type LiveTradeExportRow = {
  tradeId: string;
  cycleId: string;
  participantId: string;
  status: SignalCycleStatus;
  direction: string;
  venue: string | null;
  symbol: string;
  leverage: number;
  marginUsd: number | null;
  qtyBtc: number | null;
  limitPrice: number | null;
  signalPrice: number | null;
  fillPrice: number | null;
  exitPrice: number | null;
  pnlUsd: number | null;
  pnlMarginPct: number | null;
  feeUsd: number | null;
  exitReason: string | null;
  signalCreatedAt: string;
  participantCreatedAt: string;
  filledAt: string | null;
  closedAt: string | null;
  durationMinutes: number | null;
  bitfinexOrderId: string | null;
  regime: string | null;
  strategy: string | null;
  showcaseExitReason: string | null;
  events: LiveTradeExportEvent[];
};

export type LiveTradeExportPayload = {
  exportedAt: string;
  agentSlug: string;
  agentName: string;
  exchange: string;
  sessionStartedAt: string | null;
  userId: string;
  tradeCount: number;
  closedCount: number;
  totalPnlUsd: number;
  rows: LiveTradeExportRow[];
};

function num(v: unknown): number | null {
  if (v == null) return null;
  if (typeof v === 'object' && v !== null && 'toNumber' in v) {
    return Number((v as { toNumber: () => number }).toNumber());
  }
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function parseIntent(raw: unknown): IntentEnvelope {
  if (!raw || typeof raw !== 'object') return {};
  return raw as IntentEnvelope;
}

function payloadField(payload: unknown, key: string): unknown {
  if (!payload || typeof payload !== 'object') return null;
  return (payload as Record<string, unknown>)[key];
}

function melTs(input: Date | string | null | undefined): string {
  if (input == null) return '';
  return formatMelbourneDateTime(input instanceof Date ? input : input);
}

function mapEvent(payload: unknown, eventType: string, createdAt: Date): LiveTradeExportEvent {
  const p = (payload ?? {}) as Record<string, unknown>;
  return {
    timestamp: melTs(createdAt),
    eventType,
    limitPrice: num(p.limit_price),
    fillPrice: num(p.fill_price),
    exitPrice: num(p.exit_price),
    exitReason: typeof p.exit_reason === 'string' ? p.exit_reason : null,
    pnlUsd: num(p.pnl_usd),
    pnlMarginPct: num(p.pnl_margin_pct),
    bitfinexOrderId:
      p.bitfinex_order_id != null
        ? String(p.bitfinex_order_id)
        : p.order_id != null
          ? String(p.order_id)
          : null,
    detail: typeof p.event === 'string' ? p.event : null,
  };
}

export function mapParticipantToExportRow(input: {
  participant: {
    id: string;
    status: SignalCycleStatus;
    venue: string | null;
    fillPrice: unknown;
    exitPrice: unknown;
    pnlUsd: unknown;
    pnlMarginPct: unknown;
    feeUsd: unknown;
    createdAt: Date;
    updatedAt: Date;
    events: Array<{ eventType: string; payload: unknown; createdAt: Date }>;
  };
  cycle: {
    id: string;
    tradeId: string;
    intentEnvelope: unknown;
    showcaseExitReason: string | null;
    createdAt: Date;
    closedAt: Date | null;
  };
}): LiveTradeExportRow {
  const intent = parseIntent(input.cycle.intentEnvelope);
  const direction = String(intent.direction ?? '—').toUpperCase();
  const events = input.participant.events
    .slice()
    .sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime())
    .map((e) => mapEvent(e.payload, e.eventType, e.createdAt));

  const filledEvent = events.find((e) => e.eventType === 'FILLED');
  const exitEvent = [...events].reverse().find((e) => e.eventType === 'EXIT');
  const orderEvent = events.find((e) => e.eventType === 'ORDER_PLACED');

  const fillPrice = num(input.participant.fillPrice) ?? filledEvent?.fillPrice ?? null;
  const exitPrice = num(input.participant.exitPrice) ?? exitEvent?.exitPrice ?? null;
  const pnlUsd = num(input.participant.pnlUsd) ?? exitEvent?.pnlUsd ?? null;
  const pnlMarginPct = num(input.participant.pnlMarginPct) ?? exitEvent?.pnlMarginPct ?? null;

  const filledAt = filledEvent?.timestamp ?? null;
  const closedAt =
    input.participant.status === 'CLOSED' || input.participant.status === 'EXPIRED'
      ? exitEvent?.timestamp ?? melTs(input.participant.updatedAt)
      : null;

  const startMs = filledAt ? Date.parse(filledAt) : input.participant.createdAt.getTime();
  const endMs = closedAt ? Date.parse(closedAt) : Date.now();
  const durationMinutes =
    closedAt || input.participant.status === 'OPEN'
      ? Math.max(0, Math.round((endMs - startMs) / 60_000))
      : null;

  const orderPayload = orderEvent
    ? (input.participant.events.find((e) => e.eventType === 'ORDER_PLACED')?.payload as Record<
        string,
        unknown
      >)
    : null;

  return {
    tradeId: input.cycle.tradeId,
    cycleId: input.cycle.id,
    participantId: input.participant.id,
    status: input.participant.status,
    direction,
    venue: input.participant.venue,
    symbol: 'tBTCF0:USTF0',
    leverage: num(intent.risk?.leverage_hint) ?? 100,
    marginUsd: num(intent.risk?.max_margin_usd) ?? num(orderPayload?.margin_usd),
    qtyBtc: num(orderPayload?.qty) ?? num(orderPayload?.qty_btc),
    limitPrice: num(intent.limit_price) ?? orderEvent?.limitPrice ?? null,
    signalPrice: num(intent.signal_price) ?? null,
    fillPrice,
    exitPrice,
    pnlUsd,
    pnlMarginPct,
    feeUsd: num(input.participant.feeUsd),
    exitReason: exitEvent?.exitReason ?? input.cycle.showcaseExitReason,
    signalCreatedAt: melTs(input.cycle.createdAt),
    participantCreatedAt: melTs(input.participant.createdAt),
    filledAt,
    closedAt,
    durationMinutes,
    bitfinexOrderId:
      orderEvent?.bitfinexOrderId ??
      events.find((e) => e.bitfinexOrderId)?.bitfinexOrderId ??
      null,
    regime: intent.regime ?? null,
    strategy: intent.strategy ?? null,
    showcaseExitReason: input.cycle.showcaseExitReason,
    events,
  };
}

function csvEscape(value: unknown): string {
  if (value == null) return '';
  const s = String(value);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

const CSV_COLUMNS: Array<keyof LiveTradeExportRow | 'eventsSummary'> = [
  'tradeId',
  'cycleId',
  'participantId',
  'status',
  'direction',
  'venue',
  'symbol',
  'leverage',
  'marginUsd',
  'qtyBtc',
  'limitPrice',
  'signalPrice',
  'fillPrice',
  'exitPrice',
  'pnlUsd',
  'pnlMarginPct',
  'feeUsd',
  'exitReason',
  'signalCreatedAt',
  'participantCreatedAt',
  'filledAt',
  'closedAt',
  'durationMinutes',
  'bitfinexOrderId',
  'regime',
  'strategy',
  'showcaseExitReason',
  'eventsSummary',
];

export function liveTradesToCsv(payload: LiveTradeExportPayload): string {
  const header = CSV_COLUMNS.join(',');
  const lines = payload.rows.map((row) => {
    const flat = {
      ...row,
      eventsSummary: row.events
        .map((e) => `${e.timestamp}|${e.eventType}|${e.detail ?? ''}`)
        .join('; '),
    };
    return CSV_COLUMNS.map((col) => csvEscape(flat[col as keyof typeof flat])).join(',');
  });
  return [header, ...lines].join('\n');
}
