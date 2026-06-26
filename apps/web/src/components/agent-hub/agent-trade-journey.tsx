'use client';

import { useMemo, useState } from 'react';
import { formatPercent, formatUsd, type TradingAgentDashboardState } from '@dcf/utils';
import type { TradingAgentActivityEntry } from '@/lib/api';
import { ShareOnXButton } from '@/components/share-on-x-button';
import { filterActivitySince, liveBookToActivity, mergeDeskActivity, filterLiveExchangeActivity } from '@/lib/livebook-activity';

type JourneyPhase = 'open' | 'closed' | 'pending' | 'skip';

type JourneyNode = {
  id: string;
  label: string;
  subtitle?: string;
  phase: JourneyPhase;
};

const CLOSE_FLASH_MS = 120_000;

function isJourneyEvent(item: TradingAgentActivityEntry, liveExchangeOnly = false): boolean {
  const t = item.type.toUpperCase();
  const title = item.title.toUpperCase();
  if (t === 'NO_TRADE' || t === 'AI_REJECTED') return false;
  if (title.includes('NO TRADE') || title.includes('AI REJECTED')) return false;

  if (liveExchangeOnly) {
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
  }

  return (
    t.includes('POSITION') ||
    t.includes('TRADE') ||
    t.includes('OPEN') ||
    t.includes('CLOSE') ||
    t.includes('SIGNAL') ||
    t.includes('ORDER') ||
    t.includes('EXIT') ||
    item.profitPct != null ||
    item.entryPrice != null
  );
}

function classifyJourneyPhase(item: TradingAgentActivityEntry): JourneyPhase {
  const t = item.type.toUpperCase();
  const title = item.title.toUpperCase();
  if (t.includes('EXPIRED') || title.includes('EXPIRED')) return 'skip';
  if (t.includes('PENDING') || title.includes('LIMIT') || title.includes('WAIT')) return 'pending';
  if (
    t.includes('CLOSE') ||
    t.includes('EXIT') ||
    t === 'POSITION_CLOSED' ||
    title.includes('WIN') ||
    title.includes('LOSS') ||
    (item.exitPrice != null && !title.includes('OPEN'))
  ) {
    return 'closed';
  }
  if (t.includes('OPEN') || title.includes('OPEN')) return 'open';
  return 'pending';
}

function resolvedPnl(item: TradingAgentActivityEntry, phase: JourneyPhase): number | null {
  if (item.profitPct != null) return item.profitPct;
  if (phase === 'open' && item.netPnlUsd != null) return item.netPnlUsd >= 0 ? 1 : -1;
  if (phase === 'closed' && item.netPnlUsd != null) return item.netPnlUsd >= 0 ? 1 : -1;
  return null;
}

function activityToNodes(items: TradingAgentActivityEntry[], liveExchangeOnly = false): JourneyNode[] {
  return items.filter((item) => isJourneyEvent(item, liveExchangeOnly)).slice(0, 12).map((item) => {
    const phase = classifyJourneyPhase(item);
    return {
      id: item.id,
      label: item.title,
      subtitle: item.reason ?? item.outcome ?? undefined,
      phase,
    };
  });
}

function tileClasses(phase: JourneyPhase, item: TradingAgentActivityEntry): string {
  if (phase === 'open') {
    return 'border-zinc-600 bg-zinc-900/60 text-zinc-200';
  }
  if (phase === 'pending') {
    return 'border-zinc-600 bg-zinc-900/50 text-zinc-300';
  }
  if (phase === 'skip') {
    return 'border-zinc-700 bg-zinc-950/50 text-zinc-500';
  }
  const pnl = resolvedPnl(item, 'closed');
  const win = pnl == null || pnl >= 0;
  const closedAt = Date.parse(item.createdAt);
  const flash = Number.isFinite(closedAt) && Date.now() - closedAt < CLOSE_FLASH_MS;
  const base = win
    ? 'border-emerald-500 bg-emerald-950/50 text-emerald-200'
    : 'border-red-500 bg-red-950/50 text-red-200';
  return flash ? `${base} animate-pulse ring-2 ${win ? 'ring-emerald-400/50' : 'ring-red-400/50'}` : base;
}

function PnlBar({ pct }: { pct: number }) {
  const width = Math.min(100, Math.abs(pct) * 8);
  const positive = pct >= 0;
  return (
    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div
        className={`h-full rounded-full transition-all ${positive ? 'bg-emerald-400' : 'bg-red-400'}`}
        style={{ width: `${Math.max(width, 8)}%` }}
      />
    </div>
  );
}

function formatJourneyPnl(item: TradingAgentActivityEntry, phase: JourneyPhase): { text: string; tone: 'up' | 'down' | 'muted' } {
  if (phase === 'open') {
    if (item.netPnlUsd != null) {
      return {
        text: `${formatUsd(item.netPnlUsd, 2)} unrealized`,
        tone: item.netPnlUsd >= 0 ? 'up' : 'down',
      };
    }
    return { text: 'Open', tone: 'muted' };
  }
  if (item.profitPct != null) {
    const pctText = formatPercent(item.profitPct);
    const usd =
      item.netPnlUsd != null ? ` · ${formatUsd(item.netPnlUsd, 2)}` : '';
    return {
      text: `${pctText}${usd}`,
      tone: item.profitPct >= 0 ? 'up' : 'down',
    };
  }
  if (item.netPnlUsd != null) {
    return {
      text: formatUsd(item.netPnlUsd, 2),
      tone: item.netPnlUsd >= 0 ? 'up' : 'down',
    };
  }
  return { text: '—', tone: 'muted' };
}

function pnlToneClass(tone: 'up' | 'down' | 'muted'): string {
  if (tone === 'up') return 'text-emerald-300';
  if (tone === 'down') return 'text-red-300';
  return 'text-zinc-400';
}

export function AgentTradeJourney({
  activity,
  liveBook,
  layout = 'vertical',
  showBalance = true,
  windowMinutes = 30,
  liveExchangeOnly = false,
}: {
  activity: TradingAgentActivityEntry[];
  liveBook?: TradingAgentDashboardState['liveBook'] | null;
  layout?: 'vertical' | 'horizontal';
  showBalance?: boolean;
  windowMinutes?: number;
  /** Bitfinex live copy: real fills/positions only — no limit-order churn. */
  liveExchangeOnly?: boolean;
}) {
  const [showAllSession, setShowAllSession] = useState(false);

  const mergedActivity = useMemo(() => {
    if (liveExchangeOnly) {
      return liveBookToActivity(liveBook, 'journey', 'positions-only');
    }
    return mergeDeskActivity(activity, liveBookToActivity(liveBook, 'journey'));
  }, [activity, liveBook, liveExchangeOnly]);

  const windowed = useMemo(
    () =>
      showAllSession
        ? mergedActivity
        : filterActivitySince(mergedActivity, windowMinutes),
    [mergedActivity, showAllSession, windowMinutes],
  );

  const executed = useMemo(
    () =>
      liveExchangeOnly
        ? filterLiveExchangeActivity(windowed)
        : windowed.filter((item) => isJourneyEvent(item, false)),
    [windowed, liveExchangeOnly],
  );
  const nodes = activityToNodes(executed, liveExchangeOnly);
  const [selected, setSelected] = useState<TradingAgentActivityEntry | null>(null);

  const activeSelected = selected ?? executed[0] ?? null;

  if (nodes.length === 0) {
    return (
      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/90">
            Trade journey · last {windowMinutes} min
          </h2>
          <button
            type="button"
            onClick={() => setShowAllSession((v) => !v)}
            className="text-xs text-violet-400 hover:text-violet-300"
          >
            {showAllSession ? 'Show last 30 min' : 'Show full session'}
          </button>
        </div>
        <p className="mt-4 text-sm text-zinc-500">
          {liveExchangeOnly
            ? 'No filled positions or closed trades in this window — real Bitfinex fills and exits appear here.'
            : 'No trades in this window yet — limit fills, closes, and relay events appear here as they happen.'}
        </p>
      </section>
    );
  }

  const isHorizontal = layout === 'horizontal';

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/90">
          Trade journey · {showAllSession ? 'full session' : `last ${windowMinutes} min`}
        </h2>
        <button
          type="button"
          onClick={() => setShowAllSession((v) => !v)}
          className="text-xs text-violet-400 hover:text-violet-300"
        >
          {showAllSession ? 'Show last 30 min' : 'Show full session'}
        </button>
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        {liveExchangeOnly
          ? `Real exchange fills & positions · ${executed.length} in view · closed tiles flash green/red`
          : `Executed trades, fills, and relay events · ${executed.length} in view`}
      </p>

      <div
        className={`mt-6 flex gap-3 ${
          isHorizontal
            ? 'flex-row overflow-x-auto pb-2'
            : 'flex-col items-center sm:flex-row sm:flex-wrap sm:justify-center'
        }`}
      >
        {nodes.map((node) => {
          const item = executed.find((a) => a.id === node.id);
          if (!item) return null;
          const active = activeSelected?.id === node.id;
          const dateStr = new Date(item.createdAt).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
          });
          const pnl = formatJourneyPnl(item, node.phase);
          const entry = item.entryPrice;
          const exit = item.exitPrice;

          if (isHorizontal) {
            return (
              <button
                key={node.id}
                type="button"
                onClick={() => setSelected(item)}
                className={`min-w-[160px] shrink-0 rounded-xl border-2 px-4 py-3 text-left transition ${
                  tileClasses(node.phase, item)
                } ${active ? 'ring-2 ring-white/30' : 'opacity-90 hover:opacity-100'}`}
              >
                <p className="text-[10px] font-bold uppercase leading-tight">{node.label}</p>
                <p className="mt-1 text-xs text-zinc-400">{dateStr}</p>
                {entry != null && (
                  <p className="mt-1 font-mono text-xs text-zinc-300">
                    ${entry.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    {exit != null && node.phase === 'closed'
                      ? ` → $${exit.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                      : ''}
                  </p>
                )}
                <p className={`mt-1 text-sm font-bold ${pnlToneClass(pnl.tone)}`}>{pnl.text}</p>
                {item.profitPct != null && node.phase === 'closed' && <PnlBar pct={item.profitPct} />}
                {showBalance && item.balanceUsd != null && (
                  <p className="mt-2 text-[10px] text-zinc-400">
                    Balance: {formatUsd(item.balanceUsd, 0)}
                  </p>
                )}
                {item.shareText && (
                  <div className="mt-2" onClick={(e) => e.stopPropagation()}>
                    <ShareOnXButton text={item.shareText} label="𝕏" className="text-[10px] px-2 py-1" />
                  </div>
                )}
              </button>
            );
          }

          return (
            <div key={node.id} className="flex flex-col items-center sm:flex-row">
              <button
                type="button"
                onClick={() => setSelected(item)}
                className={`min-w-[120px] rounded-xl border-2 px-4 py-3 text-center text-xs font-bold uppercase transition ${
                  tileClasses(node.phase, item)
                } ${active ? 'ring-2 ring-white/30' : 'opacity-90 hover:opacity-100'}`}
              >
                {node.label}
              </button>
            </div>
          );
        })}
      </div>

      {activeSelected && isHorizontal && activeSelected.shareText && (
        <div className="mt-6 flex justify-end">
          <ShareOnXButton text={activeSelected.shareText} label="Share to X" />
        </div>
      )}

      {activeSelected && !isHorizontal && (
        <div className="mt-8 rounded-xl border border-zinc-700/80 bg-black/30 p-5">
          <p className="text-[10px] text-zinc-500">{new Date(activeSelected.createdAt).toLocaleString()}</p>
          <p className="mt-1 text-lg font-semibold text-white">{activeSelected.title}</p>
          {activeSelected.entryPrice != null && (
            <p className="mt-2 font-mono text-sm text-zinc-300">
              Entry ${activeSelected.entryPrice.toLocaleString()}
              {activeSelected.exitPrice != null ? ` → Exit $${activeSelected.exitPrice.toLocaleString()}` : ''}
            </p>
          )}
          {(activeSelected.profitPct != null || activeSelected.netPnlUsd != null) && (
            <>
              <p
                className={`mt-2 text-lg font-bold ${pnlToneClass(
                  formatJourneyPnl(activeSelected, classifyJourneyPhase(activeSelected)).tone,
                )}`}
              >
                P/L:{' '}
                {formatJourneyPnl(activeSelected, classifyJourneyPhase(activeSelected)).text}
              </p>
              {activeSelected.profitPct != null && (
                <PnlBar pct={activeSelected.profitPct} />
              )}
            </>
          )}
          {showBalance && activeSelected.balanceUsd != null && (
            <p className="mt-3 text-sm text-zinc-400">
              Balance after trade: <span className="font-semibold text-white">{formatUsd(activeSelected.balanceUsd, 0)}</span>
            </p>
          )}
          {activeSelected.reason && (
            <p className="mt-3 text-sm text-zinc-400">{activeSelected.reason}</p>
          )}
          {activeSelected.shareText && (
            <div className="mt-4">
              <ShareOnXButton text={activeSelected.shareText} label="Share to X" />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
