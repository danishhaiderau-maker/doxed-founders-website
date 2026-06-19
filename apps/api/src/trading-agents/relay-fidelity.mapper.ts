import type { BotApiState } from './bot-state.mapper';

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

export function buildRelayFidelitySnapshot(input: {
  bot: BotApiState | null;
  participants: Array<{
    id: string;
    fillPrice: { toNumber?: () => number } | null;
    exitPrice: { toNumber?: () => number } | null;
    updatedAt: Date;
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
}): RelayFidelitySnapshot {
  const limit = input.limit ?? 20;
  const showcaseByTrade = new Map<string, { entry?: number; exit?: number; exitReason?: string }>();

  for (const t of input.bot?.trades ?? []) {
    if (!t.trade_id) continue;
    showcaseByTrade.set(t.trade_id, {
      entry: t.entry ?? undefined,
      exit: t.exit ?? undefined,
      exitReason: t.exit_reason ?? undefined,
    });
  }

  for (const [tid, entry] of Object.entries(input.bot?.trades_map ?? {})) {
    const sig = entry?.signal_ref as Record<string, unknown> | undefined;
    if (!sig) continue;
    const prev = showcaseByTrade.get(tid) ?? {};
    showcaseByTrade.set(tid, {
      entry:
        prev.entry ??
        (typeof sig.fill_price === 'number'
          ? sig.fill_price
          : typeof sig.limit_price === 'number'
            ? sig.limit_price
            : undefined),
      exit:
        prev.exit ??
        (typeof sig.exit_price === 'number'
          ? sig.exit_price
          : (sig.exit_context as Record<string, unknown> | undefined)?.exit_price != null
            ? Number((sig.exit_context as Record<string, unknown>).exit_price)
            : undefined),
      exitReason:
        prev.exitReason ??
        (typeof sig.exit_reason === 'string' ? sig.exit_reason : undefined),
    });
  }

  const rows: RelayFidelityRow[] = [];

  for (const p of input.participants) {
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

    const showcase = showcaseByTrade.get(p.cycle.tradeId) ?? {};
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
    },
    policy: {
      showcaseMirrorOnly: process.env.SUBSCRIBER_SHOWCASE_MIRROR_ONLY !== 'false',
      copyPolicyVersion: Number(process.env.BITFINEX_COPY_POLICY_VERSION ?? 2),
      executionPollMs: Number(process.env.SUBSCRIBER_EXECUTION_POLL_MS ?? 250),
      signalPollMs: Number(process.env.SIGNAL_CYCLE_POLL_MS ?? 250),
    },
  };
}
