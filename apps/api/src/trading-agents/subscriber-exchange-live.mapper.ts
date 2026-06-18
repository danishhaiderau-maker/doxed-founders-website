import type { SignalCycleStatus } from '@prisma/client';
import type { TradingAgentDashboardState } from '@dcf/utils';
import type {
  BitfinexActiveOrder,
  BitfinexPositionDetail,
} from '../exchanges/bitfinex-api.client';

type IntentEnvelope = {
  direction?: 'LONG' | 'SHORT';
  action?: string;
  limit_price?: number;
  signal_price?: number;
  confidence?: number;
  regime?: string;
  strategy?: string;
  trigger?: string;
};

export type SubscriberCycleRow = {
  status: SignalCycleStatus;
  fillPrice: number | null;
  exitPrice: number | null;
  pnlUsd: number | null;
  pnlMarginPct: number | null;
  updatedAt: Date;
  createdAt: Date;
  cycle: {
    tradeId: string;
    status: SignalCycleStatus;
    intentEnvelope: unknown;
    showcaseExitReason: string | null;
    createdAt: Date;
  };
};

function fmtTime(d: Date): string {
  return d.toISOString().slice(0, 19).replace('T', ' ');
}

function parseIntent(raw: unknown): IntentEnvelope {
  if (!raw || typeof raw !== 'object') return {};
  return raw as IntentEnvelope;
}

function orderSide(amount: number): string {
  return amount > 0 ? 'LONG' : 'SHORT';
}

/** Map Bitfinex REST + signal-cycle DB into the same liveBook shape as the showcase bot. */
export function mapSubscriberExchangeLiveBook(input: {
  orders: BitfinexActiveOrder[];
  position: BitfinexPositionDetail | null;
  markPrice?: number;
  participants: SubscriberCycleRow[];
}): TradingAgentDashboardState['liveBook'] {
  const mark = input.markPrice ?? 0;
  const positions: TradingAgentDashboardState['liveBook']['positions'] = [];

  if (input.position) {
    const p = input.position;
    positions.push({
      leg: 'Bitfinex',
      side: p.direction,
      qty: Math.abs(p.amount),
      entry: p.basePrice,
      current: mark > 0 ? mark : p.basePrice,
      stopLoss: 0,
      takeProfit: 0,
      pnlUsd: p.pnlUsd,
    });
  }

  const pendingOrders = input.orders
    .filter((o) => Math.abs(o.amount) > 0 && o.price > 0)
    .slice(0, 10)
    .map((o) => ({
      ageMin: 0,
      side: orderSide(o.amount),
      status: String(o.status ?? 'ACTIVE').toUpperCase(),
      qty: Math.abs(o.amount),
      limitPrice: o.price,
      signalPrice: o.price,
    }));

  const expiredOrders: TradingAgentDashboardState['liveBook']['expiredOrders'] = [];
  const activeSignals: TradingAgentDashboardState['liveBook']['activeSignals'] = [];
  const trades: TradingAgentDashboardState['liveBook']['trades'] = [];

  for (const row of input.participants) {
    const intent = parseIntent(row.cycle.intentEnvelope);
    const direction = String(intent.direction ?? '—').toUpperCase();
    const limitPrice = Number(intent.limit_price ?? intent.signal_price ?? row.fillPrice ?? 0);

    if (row.status === 'EXPIRED' || (row.status === 'PENDING_ENTRY' && row.cycle.status === 'EXPIRED')) {
      const ageMin = Math.max(
        0,
        Math.round((Date.now() - row.createdAt.getTime()) / 60_000),
      );
      expiredOrders.push({
        time: fmtTime(row.updatedAt),
        direction,
        limitPrice,
        ageMin,
        reason: row.cycle.showcaseExitReason ?? 'EXPIRED',
        confidence: Math.round(Number(intent.confidence ?? 0)),
        mode: 'LIVE_COPY',
      });
      continue;
    }

    if (row.status === 'OPEN' || row.status === 'PENDING_ENTRY') {
      activeSignals.push({
        time: fmtTime(row.createdAt),
        direction,
        confidence: Math.round(Number(intent.confidence ?? 0)),
        regime: String(intent.regime ?? '—'),
        strategy: String(intent.strategy ?? 'COPY'),
        trigger: String(intent.trigger ?? 'RELAY'),
        pullRequiredPct: 0,
        signalPrice: limitPrice,
        maxPullPct: 0,
        outcome: row.status,
        fillPrice: row.fillPrice,
        exitReason: null,
      });
      continue;
    }

    if (row.status === 'CLOSED') {
      const entry = Number(row.fillPrice ?? limitPrice);
      const exit = Number(row.exitPrice ?? entry);
      const pnlPct = Number(row.pnlMarginPct ?? 0);
      const netUsd = Number(row.pnlUsd ?? 0);
      const durMin = Math.max(
        1,
        Math.round((row.updatedAt.getTime() - row.createdAt.getTime()) / 60_000),
      );
      trades.push({
        time: fmtTime(row.updatedAt),
        tradeId: row.cycle.tradeId,
        direction,
        entry,
        exit,
        durationMin: durMin,
        pnlPct,
        netUsd,
        grossUsd: netUsd,
        tradeFeesUsd: 0,
        fundingUsd: 0,
        aiBand: 'LIVE',
      });
    }
  }

  return {
    activeSignals: activeSignals.slice(0, 10),
    positions,
    pendingOrders,
    expiredOrders: expiredOrders.slice(0, 10),
    trades: trades.slice(0, 10),
  };
}
