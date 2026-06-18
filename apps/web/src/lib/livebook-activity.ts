'use client';

import type { TradingAgentDashboardState } from '@dcf/utils';
import type { TradingAgentActivityEntry } from '@/lib/api';

function parseLiveBookTime(raw: string): string {
  if (!raw) return new Date().toISOString();
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  const ms = Date.parse(normalized.endsWith('Z') ? normalized : `${normalized}Z`);
  return Number.isFinite(ms) ? new Date(ms).toISOString() : new Date().toISOString();
}

/** Client-side fallback when API activity is sparse — built from liveBook tables. */
export function liveBookToActivity(
  book: TradingAgentDashboardState['liveBook'] | null | undefined,
  prefix = 'livebook',
): TradingAgentActivityEntry[] {
  if (!book) return [];

  const items: TradingAgentActivityEntry[] = [];

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
      reason: `Limit @ ${o.limitPrice}`,
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

export function mergeDeskActivity(
  primary: TradingAgentActivityEntry[],
  ...fallbacks: TradingAgentActivityEntry[][]
): TradingAgentActivityEntry[] {
  const seen = new Set<string>();
  const out: TradingAgentActivityEntry[] = [];
  for (const feed of [primary, ...fallbacks]) {
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

export function filterActivitySince(
  items: TradingAgentActivityEntry[],
  minutes: number,
): TradingAgentActivityEntry[] {
  const cutoff = Date.now() - minutes * 60_000;
  return items.filter((item) => Date.parse(item.createdAt) >= cutoff);
}
