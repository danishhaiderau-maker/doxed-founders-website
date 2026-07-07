'use client';

import type { TradingAgentDashboardState } from '@dcf/utils';
import type { TradingAgentActivityEntry } from '@/lib/api';

function parseLiveBookTime(raw: string): string {
  if (!raw) return new Date().toISOString();
  // F7a (2026-07-07 incident) — handle the bot/API pre-formatted Melbourne
  // timestamp form `2026-07-07 21:37:51 AEST` (or AEDT). The previous parser
  // appended 'Z' to this string and produced an invalid ISO date that V8
  // rejected with NaN, causing every tile to fall through to `new Date()`
  // (i.e. NOW). That made the "last 30 min" window meaningless — trades from
  // hours ago appeared alongside just-closed ones, looking "random".
  //
  // Strategy: strip a trailing timezone word and convert to ISO-8601 with the
  // right fixed offset (Melbourne = +10:00 AEST / +11:00 AEDT). If parsing
  // fails for any reason, fall back to NOW (preserves legacy behavior).
  const trimmed = raw.trim();
  const aestMatch = /^(.*?)(?:\s+(AEST|AEDT))?$/.exec(trimmed);
  const core = aestMatch?.[1] ?? trimmed;
  const tz = aestMatch?.[2];
  const normalized = core.includes('T') ? core : core.replace(' ', 'T');
  if (tz === 'AEST') {
    const iso = `${normalized}+10:00`;
    const ms = Date.parse(iso);
    if (Number.isFinite(ms)) return new Date(ms).toISOString();
  } else if (tz === 'AEDT') {
    const iso = `${normalized}+11:00`;
    const ms = Date.parse(iso);
    if (Number.isFinite(ms)) return new Date(ms).toISOString();
  }
  // No TZ suffix — preserve legacy "assume UTC" behavior for already-ISO strings.
  const ms = Date.parse(normalized.endsWith('Z') ? normalized : `${normalized}Z`);
  return Number.isFinite(ms) ? new Date(ms).toISOString() : new Date().toISOString();
}

export type LiveBookActivityMode = 'full' | 'positions-only';

/** Drop limit-order churn — keep closed trades + open positions only. */
export function filterLiveExchangeActivity(
  items: TradingAgentActivityEntry[],
): TradingAgentActivityEntry[] {
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

/** Client-side fallback when API activity is sparse — built from liveBook tables. */
export function liveBookToActivity(
  book: TradingAgentDashboardState['liveBook'] | null | undefined,
  prefix = 'livebook',
  mode: LiveBookActivityMode = 'full',
): TradingAgentActivityEntry[] {
  if (!book) return [];

  const items: TradingAgentActivityEntry[] = [];

  for (const t of book.trades ?? []) {
    // Net USD (realized cash) is the authoritative win/loss signal. pnlPct can be
    // null/0 for already-flat closes whose margin pct wasn't recorded — treating
    // 0 as a win mislabels real losses as wins. Fall back to pnlPct only when netUsd
    // is absent.
    const winSignal = t.netUsd != null ? t.netUsd : (t.pnlPct ?? 0);
    const won = winSignal >= 0;
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
