import type { SignalCycleStatus } from '@prisma/client';
import type { TradingAgentDashboardState } from '@dcf/utils';
import { formatMelbourneDateTime } from '@dcf/utils';
import type {
  BitfinexActiveOrder,
  BitfinexPositionCloseLedgerRow,
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
  stopLoss?: number | null;
  takeProfit?: number | null;
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

function lotUnrealizedPnlUsd(
  entry: number,
  mark: number,
  direction: string,
  qty: number,
): number {
  if (!Number.isFinite(entry) || entry <= 0 || !Number.isFinite(mark) || mark <= 0 || qty <= 0) {
    return 0;
  }
  return direction === 'SHORT' ? (entry - mark) * qty : (mark - entry) * qty;
}

const ACTIVE_CYCLE_STATUSES = new Set<SignalCycleStatus>([
  'INTENT',
  'PENDING_ENTRY',
  'OPEN',
]);

function fmtTime(d: Date): string {
  return formatMelbourneDateTime(d);
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
  ledgerCloses?: BitfinexPositionCloseLedgerRow[];
}): TradingAgentDashboardState['liveBook'] {
  const mark = input.markPrice ?? 0;
  const positions: TradingAgentDashboardState['liveBook']['positions'] = [];
  const exchangeHasPosition =
    input.position != null && Math.abs(Number(input.position.amount ?? 0)) > 1e-8;

  if (exchangeHasPosition && input.position) {
    const p = input.position;
    // F7c (2026-07-07 incident) — sanity-check the mark price before rendering.
    // The showcase bot's `bot.price` (which feeds markPrice here) can glitch
    // and return a stale or wrong-instrument value (observed: 73,642 when
    // real BTC was 63,2xx — a 16% phantom move). If the mark deviates >15%
    // from the entry, fall back to the entry price (PnL shows ~0 instead of
    // a wildly wrong number) so the dashboard never prints a phantom P&L.
    // The next tick with a fresh mark will restore correct display.
    const entryAbs = Math.abs(Number(p.basePrice ?? 0));
    let displayMark = mark;
    if (
      mark > 0 &&
      entryAbs > 0 &&
      Math.abs(mark - entryAbs) / entryAbs > 0.15
    ) {
      displayMark = 0; // fall back to entry below
    }
    positions.push({
      leg: 'Bitfinex',
      side: p.direction,
      qty: Math.abs(p.amount),
      entry: p.basePrice,
      current: displayMark > 0 ? displayMark : p.basePrice,
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
      const legQty = qty > 0 ? qty : 0;
      if (legQty <= 0) continue;
      // Stale relay ledger: OPEN virtual lots with no exchange position are not real fills.
      if (!exchangeHasPosition) continue;
      const side = direction === 'LONG' || direction === 'SHORT' ? direction : 'LONG';
      const current = mark > 0 ? mark : entry;
      positions.push({
        leg: row.cycle.tradeId.slice(0, 10),
        side,
        qty: legQty,
        entry,
        current,
        stopLoss: row.stopLoss ?? 0,
        takeProfit: row.takeProfit ?? 0,
        pnlUsd:
          row.pnlUsd != null && Number.isFinite(row.pnlUsd)
            ? row.pnlUsd
            : lotUnrealizedPnlUsd(entry, current, side, legQty),
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
      // F7b (2026-07-07 incident) — suppress never-filled cancelled orders.
      // A CLOSED participant with no fillPrice, no exitPrice, and zero PnL is
      // an order that was placed then cancelled/expired without ever trading
      // (RECONCILE_CANCEL_BY_EXCHANGE, DUPLICATE_LIMIT_SKIPPED, SIGNAL_TTL_EXPIRED,
      // etc.). Rendering these as "$entry → $entry +0.00% Win" tiles in the
      // trade journey misled the user into thinking fake trades were being
      // generated. They belong in an "expired/abandoned" view, not the journey.
      const isUnfilledNoPnl =
        row.fillPrice == null &&
        row.exitPrice == null &&
        Math.abs(Number(row.pnlUsd ?? 0)) < 0.01;
      if (isUnfilledNoPnl) continue;

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

  const participantCloseMs = trades.map((t) => Date.parse(String(t.time).replace(' AEST', '+10:00'))).filter(Number.isFinite);
  for (const row of input.ledgerCloses ?? []) {
    const closedMs = row.closedAt.getTime();
    const alreadyCovered = participantCloseMs.some(
      (ms) => Number.isFinite(ms) && Math.abs(ms - closedMs) <= 120_000,
    );
    if (alreadyCovered) continue;
    trades.push({
      time: fmtTime(row.closedAt),
      tradeId: `bfx-${row.ledgerId}`,
      direction: row.pnlUsd >= 0 ? 'LONG' : 'SHORT',
      entry: 0,
      exit: 0,
      durationMin: 1,
      pnlPct: 0,
      netUsd: row.pnlUsd,
      grossUsd: row.pnlUsd,
      tradeFeesUsd: 0,
      fundingUsd: 0,
      aiBand: 'EXCHANGE',
    });
  }

  trades.sort((a, b) => {
    const ta = Date.parse(String(a.time).replace(' AEST', '+10:00'));
    const tb = Date.parse(String(b.time).replace(' AEST', '+10:00'));
    return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
  });

  // Exchange-truth pending limits when Neon participant rows lag or omit qty/price.
  if (activeSignals.length === 0 && exchangePending.length > 0) {
    for (const o of exchangePending) {
      activeSignals.push({
        time: fmtTime(new Date()),
        direction: o.side,
        confidence: 0,
        regime: '—',
        strategy: 'EXCHANGE',
        trigger: 'BITFINEX',
        pullRequiredPct: 0,
        signalPrice: o.limitPrice,
        maxPullPct: 0,
        outcome: o.status,
        fillPrice: null,
        exitReason: null,
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
