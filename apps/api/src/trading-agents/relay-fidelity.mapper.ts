import type { BotApiState } from './bot-state.mapper';
import { normalizeBotSessionTrades } from './bot-state.mapper';

export type RelayFidelityRow = {
  tradeId: string;
  cycleId: string;
  direction: string | null;
  showcaseEntry: number | null;
  bitfinexEntry: number | null;
  entryDeltaUsd: number | null;
  entryDeltaPct: number | null;
  showcaseExit: number | null;
  bitfinexExit: number | null;
  exitDeltaUsd: number | null;
  exitDeltaPct: number | null;
  showcaseExitReason: string | null;
  relayExitReason: string | null;
  closedAt: string | null;
};

export type RelayFidelitySnapshot = {
  rows: RelayFidelityRow[];
  summary: {
    tradeCount: number;
    avgEntryDeltaPct: number | null;
    avgExitDeltaPct: number | null;
    maxEntryDeltaPct: number | null;
    maxExitDeltaPct: number | null;
    missingShowcaseEntryCount: number;
    missingShowcaseExitCount: number;
  };
  policy: {
    showcaseMirrorOnly: boolean;
    copyPolicyVersion: number;
    executionPollMs: number;
    signalPollMs: number;
  };
};

function pctDelta(showcase: number | null, relay: number | null): number | null {
  if (showcase == null || relay == null || !Number.isFinite(showcase) || showcase <= 0) return null;
  return ((relay - showcase) / showcase) * 100;
}

function usdDelta(showcase: number | null, relay: number | null, qty: number | null): number | null {
  if (showcase == null || relay == null || qty == null || qty <= 0) return null;
  return (relay - showcase) * qty;
}

function extractShowcaseFromSignalRef(sig: Record<string, unknown>) {
  const exitCtx = sig.exit_context as Record<string, unknown> | undefined;
  const entry =
    typeof sig.fill_price === 'number'
      ? sig.fill_price
      : typeof sig.limit_price === 'number'
        ? sig.limit_price
        : typeof sig.signal_price === 'number'
          ? sig.signal_price
          : undefined;
  const exit =
    typeof sig.exit_price === 'number'
      ? sig.exit_price
      : exitCtx?.exit_price != null
        ? Number(exitCtx.exit_price)
        : undefined;
  return {
    entry,
    exit,
    exitReason: typeof sig.exit_reason === 'string' ? sig.exit_reason : undefined,
  };
}

/** Resolve showcase fill/exit prices from bot state — trades_map keys often differ from cycle tradeId. */
function tradeIdsMatch(a: string, b: string): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.startsWith(b) || b.startsWith(a)) return true;
  const na = a.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  const nb = b.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
  if (na === nb) return true;
  if (na.length >= 8 && nb.length >= 8 && (na.includes(nb) || nb.includes(na))) return true;
  return false;
}

export function resolveShowcaseTradePrices(
  bot: BotApiState | null,
  tradeId: string,
): { entry?: number; exit?: number; exitReason?: string } {
  if (!bot || !tradeId) return {};

  for (const t of bot.trades ?? []) {
    if (t.trade_id && tradeIdsMatch(t.trade_id, tradeId)) {
      return {
        entry: t.entry ?? undefined,
        exit: t.exit ?? undefined,
        exitReason: t.exit_reason ?? undefined,
      };
    }
  }

  const direct = bot.trades_map?.[tradeId];
  if (direct?.signal_ref && typeof direct.signal_ref === 'object') {
    return extractShowcaseFromSignalRef(direct.signal_ref as Record<string, unknown>);
  }

  for (const entry of Object.values(bot.trades_map ?? {})) {
    const sig = entry?.signal_ref as Record<string, unknown> | undefined;
    if (!sig) continue;
    const refId = String(sig.trade_id ?? '');
    if (tradeIdsMatch(refId, tradeId)) {
      return extractShowcaseFromSignalRef(sig);
    }
  }

  for (const t of normalizeBotSessionTrades(bot)) {
    if (t.trade_id && tradeIdsMatch(t.trade_id, tradeId)) {
      return {
        entry: t.entry ?? undefined,
        exit: t.exit ?? undefined,
        exitReason: t.exit_reason ?? undefined,
      };
    }
  }

  for (const o of bot.orders ?? []) {
    if (o.trade_id === tradeId) {
      const px = o.limit_price ?? o.signal_price;
      if (typeof px === 'number') return { entry: px };
    }
  }

  for (const sig of bot.signal_info?.signals ?? []) {
    if (!sig || typeof sig !== 'object') continue;
    const refId = String(sig.trade_id ?? '');
    if (refId !== tradeId && !tradeIdsMatch(refId, tradeId)) continue;
    return extractShowcaseFromSignalRef(sig as Record<string, unknown>);
  }

  for (const pos of bot.positions ?? []) {
    if (pos.trade_id === tradeId && typeof pos.entry === 'number') {
      return { entry: pos.entry };
    }
  }

  return {};
}

export function buildRelayFidelitySnapshot(input: {
  bot: BotApiState | null;
  participants: Array<{
    id: string;
    fillPrice: { toNumber?: () => number } | null;
    exitPrice: { toNumber?: () => number } | null;
    updatedAt: Date;
    createdAt?: Date;
    cycle: {
      id: string;
      tradeId: string;
      showcaseExitReason: string | null;
      closedAt: Date | null;
    };
    events: Array<{
      eventType: string;
      payload: unknown;
      createdAt: Date;
    }>;
  }>;
  limit?: number;
  sessionStartedAt?: Date | null;
}): RelayFidelitySnapshot {
  const limit = input.limit ?? 50;
  const sessionStart = input.sessionStartedAt?.getTime() ?? 0;

  const rows: RelayFidelityRow[] = [];

  for (const p of input.participants) {
    if (sessionStart > 0 && p.createdAt && p.createdAt.getTime() < sessionStart) {
      const hasRelayFill = p.events.some((e) => e.eventType === 'FILLED' || e.eventType === 'EXIT');
      if (!hasRelayFill) continue;
    }

    const filled = p.events.find((e) => e.eventType === 'FILLED');
    const exit = p.events.find((e) => e.eventType === 'EXIT');
    if (!filled && !exit) continue;

    const fillPayload =
      filled?.payload && typeof filled.payload === 'object'
        ? (filled.payload as Record<string, unknown>)
        : {};
    const exitPayload =
      exit?.payload && typeof exit.payload === 'object'
        ? (exit.payload as Record<string, unknown>)
        : {};

    const bitfinexEntry =
      p.fillPrice != null
        ? Number(p.fillPrice)
        : typeof fillPayload.fill_price === 'number'
          ? fillPayload.fill_price
          : null;
    const bitfinexExit =
      p.exitPrice != null
        ? Number(p.exitPrice)
        : typeof exitPayload.exit_price === 'number'
          ? exitPayload.exit_price
          : null;
    const qty = typeof fillPayload.qty === 'number' ? fillPayload.qty : null;

    const showcase = resolveShowcaseTradePrices(input.bot, p.cycle.tradeId);
    const showcaseEntry = showcase.entry ?? null;
    const showcaseExit = showcase.exit ?? null;

    rows.push({
      tradeId: p.cycle.tradeId,
      cycleId: p.cycle.id,
      direction:
        typeof fillPayload.direction === 'string'
          ? fillPayload.direction
          : null,
      showcaseEntry,
      bitfinexEntry,
      entryDeltaUsd: usdDelta(showcaseEntry, bitfinexEntry, qty),
      entryDeltaPct: pctDelta(showcaseEntry, bitfinexEntry),
      showcaseExit,
      bitfinexExit,
      exitDeltaUsd: usdDelta(showcaseExit, bitfinexExit, qty),
      exitDeltaPct: pctDelta(showcaseExit, bitfinexExit),
      showcaseExitReason: p.cycle.showcaseExitReason ?? showcase.exitReason ?? null,
      relayExitReason:
        typeof exitPayload.exit_reason === 'string' ? exitPayload.exit_reason : null,
      closedAt: (p.cycle.closedAt ?? p.updatedAt).toISOString(),
    });
  }

  rows.sort((a, b) => (b.closedAt ?? '').localeCompare(a.closedAt ?? ''));
  const trimmed = rows.slice(0, limit);

  const entryDeltas = trimmed
    .map((r) => r.entryDeltaPct)
    .filter((v): v is number => v != null && Number.isFinite(v));
  const exitDeltas = trimmed
    .map((r) => r.exitDeltaPct)
    .filter((v): v is number => v != null && Number.isFinite(v));

  const avg = (xs: number[]) =>
    xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : null;

  return {
    rows: trimmed,
    summary: {
      tradeCount: trimmed.length,
      avgEntryDeltaPct: avg(entryDeltas) != null ? Number(avg(entryDeltas)!.toFixed(3)) : null,
      avgExitDeltaPct: avg(exitDeltas) != null ? Number(avg(exitDeltas)!.toFixed(3)) : null,
      maxEntryDeltaPct:
        entryDeltas.length > 0
          ? Number(Math.max(...entryDeltas.map(Math.abs)).toFixed(3))
          : null,
      maxExitDeltaPct:
        exitDeltas.length > 0 ? Number(Math.max(...exitDeltas.map(Math.abs)).toFixed(3)) : null,
      missingShowcaseEntryCount: trimmed.filter((r) => r.showcaseEntry == null && r.bitfinexEntry != null)
        .length,
      missingShowcaseExitCount: trimmed.filter((r) => r.showcaseExit == null && r.bitfinexExit != null)
        .length,
    },
    policy: {
      showcaseMirrorOnly: process.env.SUBSCRIBER_SHOWCASE_MIRROR_ONLY !== 'false',
      copyPolicyVersion: Number(process.env.BITFINEX_COPY_POLICY_VERSION ?? 2),
      executionPollMs: Number(process.env.SUBSCRIBER_EXECUTION_POLL_MS ?? 250),
      signalPollMs: Number(process.env.SIGNAL_CYCLE_POLL_MS ?? 250),
    },
  };
}
