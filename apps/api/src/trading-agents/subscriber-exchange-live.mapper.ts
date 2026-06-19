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
  limitPrice?: number | null;
  qty?: number | null;
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

const ACTIVE_CYCLE_STATUSES = new Set<SignalCycleStatus>([
  'INTENT',
  'PENDING_ENTRY',
  'OPEN',
]);

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

  const exchangePending = input.orders
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

  const pendingOrders = [...exchangePending];
  const seenPending = new Set(
    exchangePending.map((o) => `${o.side}:${o.limitPrice}:${o.qty}`),
  );

  const expiredOrders: TradingAgentDashboardState['liveBook']['expiredOrders'] = [];
  const activeSignals: TradingAgentDashboardState['liveBook']['activeSignals'] = [];
  const trades: TradingAgentDashboardState['liveBook']['trades'] = [];

  for (const row of input.participants) {
    const intent = parseIntent(row.cycle.intentEnvelope);
    const direction = String(intent.direction ?? '—').toUpperCase();
    const limitPrice = Number(
      row.limitPrice ?? intent.limit_price ?? intent.signal_price ?? row.fillPrice ?? 0,
    );
    const qty = Number(row.qty ?? 0);
    const cycleLive = ACTIVE_CYCLE_STATUSES.has(row.cycle.status);

    if (row.status === 'EXPIRED' || (row.status === 'PENDING_ENTRY' && !cycleLive)) {
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

    if (row.status === 'OPEN') {
      const entry = Number(row.fillPrice ?? limitPrice);
      positions.push({
        leg: row.cycle.tradeId.slice(0, 10),
        side: direction === 'LONG' || direction === 'SHORT' ? direction : 'LONG',
        qty: qty > 0 ? qty : 0,
        entry,
        current: mark > 0 ? mark : entry,
        stopLoss: 0,
        takeProfit: 0,
        pnlUsd: row.pnlUsd ?? 0,
      });
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

    if (row.status === 'PENDING_ENTRY' && cycleLive) {
      const ageMin = Math.max(
        0,
        Math.round((Date.now() - row.createdAt.getTime()) / 60_000),
      );
      const pendingKey = `${direction}:${limitPrice}:${qty}`;
      if (limitPrice > 0 && !seenPending.has(pendingKey)) {
        pendingOrders.push({
          ageMin,
          side: direction === 'LONG' || direction === 'SHORT' ? direction : 'SHORT',
          status: 'PENDING',
          qty: qty > 0 ? qty : 0,
          limitPrice,
          signalPrice: limitPrice,
        });
        seenPending.add(pendingKey);
      }
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
    trades: trades.slice(0, 30),
  };
}
