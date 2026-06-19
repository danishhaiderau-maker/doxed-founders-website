import type { TradingAgentDashboardState } from '@dcf/utils';
import type { BotActivityEntry } from './bot-state.mapper';

function parseLiveBookTime(raw: string): string {
  if (!raw) return new Date().toISOString();
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  const ms = Date.parse(normalized.endsWith('Z') ? normalized : `${normalized}Z`);
  return Number.isFinite(ms) ? new Date(ms).toISOString() : new Date().toISOString();
}

export type LiveBookActivityMode = 'full' | 'positions-only';

/** Drop limit-order churn — keep closed trades + open positions only. */
export function filterLiveExchangeActivity(items: BotActivityEntry[]): BotActivityEntry[] {
  return items.filter((item) => {
    const t = item.type.toUpperCase();
    const title = item.title.toUpperCase();
    if (t.includes('ORDER') || t.includes('PENDING') || t.includes('EXPIRED') || t.includes('SIGNAL')) {
      return false;
    }
    if (title.includes('LIMIT') || title.includes('PENDING')) return false;
    return (
      t.includes('POSITION') ||
      t.includes('CLOSE') ||
      t.includes('EXIT') ||
      t.includes('FILLED') ||
      item.profitPct != null ||
      (item.entryPrice != null && item.exitPrice != null)
    );
  });
}

/** Turn liveBook rows into activity feed + trade-journey entries. */
export function mapLiveBookToActivity(
  book: TradingAgentDashboardState['liveBook'] | null | undefined,
  prefix = 'livebook',
  mode: LiveBookActivityMode = 'full',
): BotActivityEntry[] {
  if (!book) return [];

  const items: BotActivityEntry[] = [];

  for (const t of book.trades ?? []) {
    const won = (t.pnlPct ?? 0) >= 0;
    items.push({
      id: `${prefix}-trade-${t.tradeId}`,
      type: 'POSITION_CLOSED',
      title: `${t.direction} · ${won ? 'Win' : 'Loss'}`,
      reason: null,
      outcome: won ? 'Profit' : 'Loss',
      profitPct: t.pnlPct ?? null,
      edgeScore: null,
      edgeRequired: null,
      marketRegime: null,
      shareText: null,
      createdAt: parseLiveBookTime(t.time),
      entryPrice: t.entry ?? null,
      exitPrice: t.exit ?? null,
      balanceUsd: null,
      netPnlUsd: t.netUsd ?? null,
    });
  }

  if (mode === 'positions-only') {
    for (const p of book.positions ?? []) {
      items.push({
        id: `${prefix}-position-${p.side}-${p.entry}`,
        type: 'POSITION_OPEN',
        title: `${p.side} open · ${p.leg}`,
        reason: `Entry ${p.entry}`,
        outcome: 'Open',
        profitPct: null,
        edgeScore: null,
        edgeRequired: null,
        marketRegime: null,
        shareText: null,
        createdAt: new Date().toISOString(),
        entryPrice: p.entry ?? null,
        exitPrice: p.current ?? null,
        balanceUsd: null,
        netPnlUsd: p.pnlUsd ?? null,
      });
    }
    return items.sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  }

  for (const s of book.activeSignals ?? []) {
    items.push({
      id: `${prefix}-signal-${s.time}-${s.direction}`,
      type: 'SIGNAL_ACTIVE',
      title: `${s.direction} signal · ${s.outcome ?? 'ACTIVE'}`,
      reason: s.trigger ? `Trigger: ${s.trigger}` : null,
      outcome: s.outcome ?? null,
      profitPct: null,
      edgeScore: s.confidence ?? null,
      edgeRequired: null,
      marketRegime: s.regime ?? null,
      shareText: null,
      createdAt: parseLiveBookTime(s.time),
      entryPrice: s.fillPrice ?? s.signalPrice ?? null,
      exitPrice: null,
      balanceUsd: null,
      netPnlUsd: null,
    });
  }

  for (const o of book.pendingOrders ?? []) {
    items.push({
      id: `${prefix}-pending-${o.limitPrice}-${o.side}`,
      type: 'ORDER_PENDING',
      title: `${o.side} limit · ${o.status}`,
      reason: `Limit ${o.limitPrice}`,
      outcome: o.status,
      profitPct: null,
      edgeScore: null,
      edgeRequired: null,
      marketRegime: null,
      shareText: null,
      createdAt: new Date(Date.now() - (o.ageMin ?? 0) * 60_000).toISOString(),
      entryPrice: o.limitPrice ?? null,
      exitPrice: null,
      balanceUsd: null,
      netPnlUsd: null,
    });
  }

  for (const e of book.expiredOrders ?? []) {
    items.push({
      id: `${prefix}-expired-${e.time}-${e.direction}`,
      type: 'ORDER_EXPIRED',
      title: `${e.direction} limit expired`,
      reason: e.reason ?? 'EXPIRED',
      outcome: 'Expired',
      profitPct: null,
      edgeScore: e.confidence ?? null,
      edgeRequired: null,
      marketRegime: null,
      shareText: null,
      createdAt: parseLiveBookTime(e.time),
      entryPrice: e.limitPrice ?? null,
      exitPrice: null,
      balanceUsd: null,
      netPnlUsd: null,
    });
  }

  for (const p of book.positions ?? []) {
    items.push({
      id: `${prefix}-position-${p.side}-${p.entry}`,
      type: 'POSITION_OPEN',
      title: `${p.side} open · ${p.leg}`,
      reason: `Entry ${p.entry}`,
      outcome: 'Open',
      profitPct: null,
      edgeScore: null,
      edgeRequired: null,
      marketRegime: null,
      shareText: null,
      createdAt: new Date().toISOString(),
      entryPrice: p.entry ?? null,
      exitPrice: p.current ?? null,
      balanceUsd: null,
      netPnlUsd: p.pnlUsd ?? null,
    });
  }

  return items.sort(
    (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
  );
}

export function mergeActivityFeeds(
  ...feeds: BotActivityEntry[][]
): BotActivityEntry[] {
  const seen = new Set<string>();
  const out: BotActivityEntry[] = [];
  for (const feed of feeds) {
    for (const item of feed) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
    }
  }
  return out.sort(
    (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
  );
}
