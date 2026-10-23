import type { SignalCycleStatus } from '@prisma/client';
import { formatMelbourneDateTime } from '@dcf/utils';
import {
  liveRowCountabilityWithLegs,
  liveRowStopEvidenceFromEvents,
  type LiveExitSide,
  type LiveLegStamps,
} from './live-row-countability';

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
  /** HM B4: forced (force-flat / maintenance / admin) close; never a strategy outcome. */
  nonCountable: boolean;
  nonCountableReason: string | null;
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
        : p.bitfinexOrderId != null
          ? String(p.bitfinexOrderId)
          : p.order_id != null
            ? String(p.order_id)
            : null,
    detail: typeof p.event === 'string' ? p.event : null,
  };
}

const secOf = (v: unknown): number | null => {
  if (typeof v === 'number' && Number.isFinite(v) && v > 0) return v > 1e12 ? v / 1000 : v;
  if (typeof v !== 'string' || !v) return null;
  const t = Date.parse(v);
  return Number.isFinite(t) && t > 0 ? t / 1000 : null;
};
const recOf = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/**
 * HM 01:43 item 3: the measured per-leg stamps a Railway live row has. Entry: Fly signal + intent from the signed
 * approval, Railway receive from the envelope, exchange ack from the order/fill payload. Exit, bot-sent (RK (1)):
 * exit trigger, Fly send and Railway receive are carried on the EXIT payload by railwayExitLegStamps (signed
 * POSITION_CLOSED exit_trigger_ts, HMAC-verified X-Fly-Send-Started-At, platform receipt); exit,
 * EXCHANGE_RESIDENT: trade-print trigger -> Bitfinex fill record (residentExitLegStamps). Any missing stamp fails
 * closed (LIVE_LEG_MISSING:<side>.*) (R17).
 */
export function liveLegStamps(
  intentEnvelope: unknown,
  events: Array<{ eventType: string; payload: unknown }>,
  closed: boolean,
): [LiveLegStamps, LiveLegStamps | null, LiveExitSide] {
  const ctx = recOf(recOf(intentEnvelope).context);
  const approval = recOf(ctx.fly_live_approval);
  const pl = (type: string) => recOf(events.find((e) => e.eventType === type)?.payload);
  const placed = pl('ORDER_PLACED');
  const filled = pl('FILLED');
  const exitPl = recOf([...events].reverse().find((e) => e.eventType === 'EXIT')?.payload);
  const entry: LiveLegStamps = {
    fly_signal_at_ts: secOf(approval.signal_at_ts),
    fly_intent_emitted_at_ts: secOf(approval.created_at_ts),
    railway_received_at_ts: secOf(ctx.platform_received_at),
    exchange_ack_at_ts: secOf(placed.entry_exchange_ack_at ?? placed.exchange_ack_at ?? filled.entry_exchange_ack_at),
  };
  if (!closed) return [entry, null, 'exit'];
  if (String(exitPl.exit_order_class ?? '') === 'EXCHANGE_RESIDENT') {
    // RK (1): no send leg; trigger = real trade print, fill = Bitfinex's own fill record.
    return [entry, {
      exit_trigger_print_at_ts: secOf(exitPl.exit_trigger_print_at),
      exit_fill_record_at_ts: secOf(exitPl.exit_fill_record_at ?? exitPl.exchange_exit_fill_first_at),
    }, 'exit_resident'];
  }
  const exit: LiveLegStamps = {
    // HM #842 R3 nit: only the measured, window-bounded exit_trigger_ts counts. The old exit_signal_at fallback was
    // produced by nothing; a row carrying only it stays LIVE_LEG_MISSING:exit.signal (non-countable, R17).
    exit_signal_at_ts: secOf(exitPl.exit_trigger_ts),
    fly_send_started_at_ts: secOf(exitPl.fly_send_started_at),
    railway_received_at_ts: secOf(exitPl.platform_received_at),
    exit_ack_at_ts: secOf(exitPl.close_exchange_ack_at),
    exit_confirmed_at_ts: secOf(exitPl.close_confirmed_at),
  };
  return [entry, exit, 'exit'];
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
    // ONE live-row rule: forced close -> #852 item 4 guardian stop evidence (filled rows only) -> per-leg check.
    ...liveRowCountabilityWithLegs(
      [exitEvent?.exitReason, input.cycle.showcaseExitReason],
      ...liveLegStamps(input.cycle.intentEnvelope, input.participant.events, closedAt != null),
      filledEvent ? liveRowStopEvidenceFromEvents(input.participant.events) : undefined,
    ),
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
  'nonCountable',
  'nonCountableReason',
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
