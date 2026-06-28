import type { BotApiState } from './bot-state.mapper';
import { normalizeBotSessionTrades } from './bot-state.mapper';
import {
  classifyTradeIdMatch,
  pickCanonicalTradeId,
  tradeIdsMatch,
  DEFAULT_SUBSCRIBER_EXECUTION_POLL_MS,
  DEFAULT_SIGNAL_CYCLE_POLL_MS,
  type TradeIdMatchKind,
} from '@dcf/utils';

export type RelayFidelityRow = {
  tradeId: string;
  localBotTradeId: string | null;
  matchKind: TradeIdMatchKind;
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
  localBotEntryAt: string | null;
  localBotExitAt: string | null;
  relayEntryAt: string | null;
  relayExitAt: string | null;
  entryLagSec: number | null;
  exitLagSec: number | null;
  closedAt: string | null;
};

export type RelayFidelityOrphan = {
  tradeId: string;
  kind:
    | 'relay_without_showcase'
    | 'showcase_without_relay'
    | 'showcase_without_relay_offline';
  detail: string;
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
    avgEntryLagSec: number | null;
    avgExitLagSec: number | null;
    unmatchedRelayCount: number;
    unmatchedShowcaseCount: number;
    unmatchedShowcaseOfflineCount: number;
  };
  audit: {
    orphans: RelayFidelityOrphan[];
    relayTradeIds: string[];
    matchedShowcaseTradeIds: string[];
  };
  policy: {
    showcaseMirrorOnly: boolean;
    copyPolicyVersion: number;
    executionPollMs: number;
    signalPollMs: number;
  };
};

export type ShowcaseTradeDetails = {
  matchedTradeId: string;
  matchKind: TradeIdMatchKind;
  mapKey?: string;
  entry?: number;
  exit?: number;
  exitReason?: string;
  entryAt?: string;
  exitAt?: string;
};

function pctDelta(showcase: number | null, relay: number | null): number | null {
  if (showcase == null || relay == null || !Number.isFinite(showcase) || showcase <= 0) return null;
  return ((relay - showcase) / showcase) * 100;
}

function usdDelta(showcase: number | null, relay: number | null, qty: number | null): number | null {
  if (showcase == null || relay == null || qty == null || qty <= 0) return null;
  return (relay - showcase) * qty;
}

function tsFromUnknown(v: unknown): string | undefined {
  if (v == null || v === '') return undefined;
  if (typeof v === 'number') {
    const ms = v > 1e12 ? v : v * 1000;
    return new Date(ms).toISOString();
  }
  if (typeof v === 'string') {
    const parsed = Date.parse(v);
    if (!Number.isNaN(parsed)) return new Date(parsed).toISOString();
    return v.includes('T') ? v : undefined;
  }
  return undefined;
}

function extractShowcaseFromSignalRef(sig: Record<string, unknown>): Omit<ShowcaseTradeDetails, 'matchedTradeId' | 'matchKind'> {
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
  const entryAt =
    tsFromUnknown(sig.fill_ts) ??
    tsFromUnknown(sig.filled_ts) ??
    tsFromUnknown(sig.entry_ts) ??
    tsFromUnknown(sig.created_ts_ts) ??
    tsFromUnknown(sig.created_ts);
  const exitAt =
    tsFromUnknown(sig.closed_ts) ??
    tsFromUnknown(exitCtx?.closed_ts) ??
    tsFromUnknown(exitCtx?.exit_ts);
  return {
    entry,
    exit,
    exitReason: typeof sig.exit_reason === 'string' ? sig.exit_reason : undefined,
    entryAt,
    exitAt,
  };
}

function lagSec(
  localIso: string | null | undefined,
  relayIso: string | null | undefined,
): number | null {
  if (!localIso || !relayIso) return null;
  const a = Date.parse(localIso);
  const b = Date.parse(relayIso);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((b - a) / 1000);
}

/** Resolve showcase fill/exit + timestamps — trades_map keys often differ from cycle tradeId. */
export function resolveShowcaseTradeDetails(
  bot: BotApiState | null,
  tradeId: string,
): ShowcaseTradeDetails | null {
  if (!bot || !tradeId) return null;

  let best: ShowcaseTradeDetails | null = null;
  let bestRank = -1;

  const consider = (candidateId: string, details: Omit<ShowcaseTradeDetails, 'matchedTradeId' | 'matchKind'>, mapKey?: string) => {
    const kind = classifyTradeIdMatch(tradeId, candidateId);
    if (kind === 'none') return;
    const rank = kind === 'exact' ? 4 : kind === 'normalized' ? 3 : kind === 'prefix' ? 2 : 1;
    const score = [details.entry, details.exit, details.entryAt, details.exitAt].filter((v) => v != null).length;
    const bestScore = best
      ? [best.entry, best.exit, best.entryAt, best.exitAt].filter((v) => v != null).length
      : -1;
    if (rank < bestRank || (rank === bestRank && score <= bestScore)) return;
    bestRank = rank;
    best = {
      matchedTradeId: pickCanonicalTradeId(tradeId, candidateId),
      matchKind: kind,
      mapKey,
      entry: details.entry ?? best?.entry,
      exit: details.exit ?? best?.exit,
      exitReason: details.exitReason ?? best?.exitReason,
      entryAt: details.entryAt ?? best?.entryAt,
      exitAt: details.exitAt ?? best?.exitAt,
    };
  };

  for (const t of bot.trades ?? []) {
    if (!t.trade_id) continue;
    consider(t.trade_id, {
      entry: t.entry ?? undefined,
      exit: t.exit ?? undefined,
      exitReason: t.exit_reason ?? undefined,
      entryAt:
        tsFromUnknown((t as Record<string, unknown>).entry_ts) ??
        tsFromUnknown((t as Record<string, unknown>).fill_ts) ??
        tsFromUnknown(t.ts),
      exitAt: tsFromUnknown(t.ts),
    });
  }

  for (const t of normalizeBotSessionTrades(bot)) {
    if (!t.trade_id) continue;
    consider(t.trade_id, {
      entry: t.entry ?? undefined,
      exit: t.exit ?? undefined,
      exitReason: t.exit_reason ?? undefined,
      entryAt:
        tsFromUnknown((t as Record<string, unknown>).entry_ts) ??
        tsFromUnknown((t as Record<string, unknown>).fill_ts) ??
        tsFromUnknown(t.ts),
      exitAt: tsFromUnknown(t.ts),
    });
  }

  const direct = bot.trades_map?.[tradeId];
  if (direct?.signal_ref && typeof direct.signal_ref === 'object') {
    const sig = direct.signal_ref as Record<string, unknown>;
    consider(String(sig.trade_id ?? tradeId), extractShowcaseFromSignalRef(sig), tradeId);
  }

  for (const [mapKey, entry] of Object.entries(bot.trades_map ?? {})) {
    const sig = entry?.signal_ref as Record<string, unknown> | undefined;
    if (!sig) continue;
    const refId = String(sig.trade_id ?? mapKey);
    consider(refId, extractShowcaseFromSignalRef(sig), mapKey);
    if (tradeIdsMatch(mapKey, tradeId)) {
      consider(refId, extractShowcaseFromSignalRef(sig), mapKey);
    }
  }

  for (const o of bot.orders ?? []) {
    if (!o.trade_id || !tradeIdsMatch(o.trade_id, tradeId)) continue;
    const px = o.limit_price ?? o.signal_price;
    if (typeof px === 'number') {
      consider(o.trade_id, { entry: px });
    }
  }

  for (const sig of bot.signal_info?.signals ?? []) {
    if (!sig || typeof sig !== 'object') continue;
    const refId = String(sig.trade_id ?? '');
    if (!refId || !tradeIdsMatch(refId, tradeId)) continue;
    consider(refId, extractShowcaseFromSignalRef(sig as Record<string, unknown>));
  }

  for (const pos of bot.positions ?? []) {
    if (!pos.trade_id || !tradeIdsMatch(pos.trade_id, tradeId)) continue;
    if (typeof pos.entry === 'number') {
      consider(pos.trade_id, { entry: pos.entry });
    }
  }

  return best;
}

/** @deprecated use resolveShowcaseTradeDetails */
export function resolveShowcaseTradePrices(
  bot: BotApiState | null,
  tradeId: string,
): { entry?: number; exit?: number; exitReason?: string } {
  const d = resolveShowcaseTradeDetails(bot, tradeId);
  if (!d) return {};
  return { entry: d.entry, exit: d.exit, exitReason: d.exitReason };
}

function showcaseTradeClosedAtMs(trade: Record<string, unknown>): number {
  const closed = Number(trade.closed_ts ?? 0);
  if (closed > 0) return closed * 1000;
  if (typeof trade.ts === 'string') {
    const parsed = Date.parse(trade.ts);
    if (Number.isFinite(parsed)) return parsed;
  }
  const created = Number(trade.created_ts_ts ?? 0);
  if (created > 0) return created * 1000;
  return 0;
}

function collectShowcaseSessionTradeIds(
  bot: BotApiState | null,
  relaySessionStartedAt?: Date | null,
): Array<{ tradeId: string; closedAtMs: number }> {
  if (!bot) return [];
  const relayStartMs = relaySessionStartedAt?.getTime() ?? 0;
  const includeForRelayAudit = (closedAtMs: number) =>
    relayStartMs <= 0 || closedAtMs <= 0 || closedAtMs >= relayStartMs - 2000;

  const out: Array<{ tradeId: string; closedAtMs: number }> = [];
  const seen = new Set<string>();
  const push = (id: string, closedAtMs: number) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({ tradeId: id, closedAtMs });
  };
  for (const t of normalizeBotSessionTrades(bot)) {
    if (!t.trade_id) continue;
    const closedMs = showcaseTradeClosedAtMs(t as Record<string, unknown>);
    if (!includeForRelayAudit(closedMs)) continue;
    push(String(t.trade_id), closedMs);
  }
  for (const [mapKey, entry] of Object.entries(bot.trades_map ?? {})) {
    const sig = entry?.signal_ref as Record<string, unknown> | undefined;
    if (!sig) continue;
    if (String(sig.status ?? '') === 'CLOSED' || sig.exit_price != null || sig.closed_ts != null) {
      const closedMs = showcaseTradeClosedAtMs(sig);
      if (!includeForRelayAudit(closedMs)) continue;
      push(String(sig.trade_id ?? mapKey), closedMs);
    }
  }
  return out;
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
  const relayTradeIds: string[] = [];

  for (const p of input.participants) {
    if (sessionStart > 0 && p.createdAt && p.createdAt.getTime() < sessionStart) {
      continue;
    }

    const filled = p.events.find((e) => e.eventType === 'FILLED');
    const exit = p.events.find((e) => e.eventType === 'EXIT');
    if (!filled && !exit) continue;

    relayTradeIds.push(p.cycle.tradeId);

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

    const showcase = resolveShowcaseTradeDetails(input.bot, p.cycle.tradeId);
    const showcaseEntry = showcase?.entry ?? null;
    const showcaseExit = showcase?.exit ?? null;
    const relayEntryAt = filled?.createdAt.toISOString() ?? null;
    const relayExitAt = exit?.createdAt.toISOString() ?? null;

    rows.push({
      tradeId: p.cycle.tradeId,
      localBotTradeId: showcase?.matchedTradeId ?? null,
      matchKind: showcase?.matchKind ?? 'none',
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
      showcaseExitReason: p.cycle.showcaseExitReason ?? showcase?.exitReason ?? null,
      relayExitReason:
        typeof exitPayload.exit_reason === 'string' ? exitPayload.exit_reason : null,
      localBotEntryAt: showcase?.entryAt ?? null,
      localBotExitAt: showcase?.exitAt ?? null,
      relayEntryAt,
      relayExitAt,
      entryLagSec: lagSec(showcase?.entryAt, relayEntryAt),
      exitLagSec: lagSec(showcase?.exitAt, relayExitAt),
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
  const entryLags = trimmed
    .map((r) => r.entryLagSec)
    .filter((v): v is number => v != null && Number.isFinite(v));
  const exitLags = trimmed
    .map((r) => r.exitLagSec)
    .filter((v): v is number => v != null && Number.isFinite(v));

  const avg = (xs: number[]) =>
    xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : null;

  const matchedShowcaseTradeIds = trimmed
    .map((r) => r.localBotTradeId)
    .filter((v): v is string => Boolean(v));

  const orphans: RelayFidelityOrphan[] = [];
  for (const row of trimmed) {
    if (row.bitfinexEntry != null && row.showcaseEntry == null) {
      orphans.push({
        tradeId: row.tradeId,
        kind: 'relay_without_showcase',
        detail: `Relay filled but no global showcase bot :7002 entry matched (match=${row.matchKind})`,
      });
    }
  }

  const showcaseIds = collectShowcaseSessionTradeIds(input.bot, input.sessionStartedAt);
  // Relay fill timestamps (sorted) — used to infer whether the relay was active
  // around the time a showcase orphan closed. If the relay had no fills within a
  // reasonable window of an orphan's close, the relay was almost certainly
  // offline/disconnected and could not have copied that trade — so we exempt it
  // from the sync score instead of permanently penalizing fidelity.
  const relayFillMs = rows
    .map((r) => r.relayEntryAt)
    .filter((v): v is string => Boolean(v))
    .map((iso) => Date.parse(iso))
    .filter((ms) => Number.isFinite(ms))
    .sort((a, b) => a - b);
  const RELAY_OFFLINE_GAP_MS = 30 * 60 * 1000; // 30 min with no relay fill => considered offline
  const nearestRelayActivityGapMs = (t: number): number | null => {
    if (relayFillMs.length === 0) return null;
    let best = Infinity;
    for (const ms of relayFillMs) {
      const d = Math.abs(ms - t);
      if (d < best) best = d;
      if (ms > t && d > best) break;
    }
    return best;
  };

  let unmatchedShowcaseOffline = 0;
  for (const sidEntry of showcaseIds) {
    const sid = sidEntry.tradeId;
    const hasRelay = relayTradeIds.some((rid) => tradeIdsMatch(rid, sid));
    if (hasRelay) continue;
    const closedMs = sidEntry.closedAtMs;
    const nearestGap = Number.isFinite(closedMs) ? nearestRelayActivityGapMs(closedMs) : null;
    const relayLikelyOffline =
      relayFillMs.length === 0 || (nearestGap != null && nearestGap > RELAY_OFFLINE_GAP_MS);
    if (relayLikelyOffline) {
      unmatchedShowcaseOffline += 1;
      orphans.push({
        tradeId: sid,
        kind: 'showcase_without_relay_offline',
        detail:
          input.sessionStartedAt != null
            ? 'Showcase trade closed while relay sim was offline — not counted against sync score'
            : 'Showcase trade closed with relay sim offline — not counted against sync score',
      });
    } else {
      orphans.push({
        tradeId: sid,
        kind: 'showcase_without_relay',
        detail:
          input.sessionStartedAt != null
            ? 'Showcase trade closed after relay sim started with no matching relay fill'
            : 'Local bot closed trade with no relay participant in this session',
      });
    }
  }

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
      avgEntryLagSec: avg(entryLags) != null ? Number(avg(entryLags)!.toFixed(1)) : null,
      avgExitLagSec: avg(exitLags) != null ? Number(avg(exitLags)!.toFixed(1)) : null,
      unmatchedRelayCount: trimmed.filter((r) => r.matchKind === 'none').length,
      unmatchedShowcaseCount: orphans.filter((o) => o.kind === 'showcase_without_relay').length,
      unmatchedShowcaseOfflineCount: unmatchedShowcaseOffline,
    },
    audit: {
      orphans: orphans.slice(0, 20),
      relayTradeIds,
      matchedShowcaseTradeIds,
    },
    policy: {
      showcaseMirrorOnly: process.env.SUBSCRIBER_SHOWCASE_MIRROR_ONLY !== 'false',
      copyPolicyVersion: Number(process.env.BITFINEX_COPY_POLICY_VERSION ?? 2),
      executionPollMs: Number(process.env.SUBSCRIBER_EXECUTION_POLL_MS ?? DEFAULT_SUBSCRIBER_EXECUTION_POLL_MS),
      signalPollMs: Number(process.env.SIGNAL_CYCLE_POLL_MS ?? DEFAULT_SIGNAL_CYCLE_POLL_MS),
    },
  };
}

// Re-export for API consumers that import from mapper
export { tradeIdsMatch };
