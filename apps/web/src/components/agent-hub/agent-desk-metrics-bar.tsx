'use client';

import { useEffect, useRef, useState } from 'react';
import { formatPercent, formatUsd } from '@dcf/utils';
import type { CopyRelaySimState } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-300';
}

type MetricCell = { label: string; value: string; hint?: string; accent?: string };

/**
 * Showcase bot session-P&L delta since an anchor moment (live-copy start).
 * Captures the showcase bot's cumulative session P&L at the first moment we
 * see a valid `anchorKey` (the live session's `userSessionStartedAt`) and
 * persists it in localStorage keyed by that timestamp so it survives reloads.
 * Returns the delta = current showcase session P&L − baseline, or null when
 * no live session is active / baseline not yet captured. Mirrors the
 * sim-start anchoring the backend does for relay sim, but lives in the
 * frontend because the live copy has no backend sim-state to store it in.
 */
const LIVE_BASELINE_PREFIX = 'dcf:showcase-live-baseline:';

function useShowcaseDeltaSinceLiveStart(
  anchorKey: string | null,
  currentShowcasePnl: number,
): number | null {
  const [baseline, setBaseline] = useState<number | null>(null);
  const capturedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!anchorKey) {
      setBaseline(null);
      capturedRef.current = null;
      return;
    }
    if (capturedRef.current === anchorKey) return;
    try {
      const raw = window.localStorage.getItem(LIVE_BASELINE_PREFIX + anchorKey);
      if (raw) {
        const parsed = JSON.parse(raw) as { pnlUsd?: unknown };
        if (typeof parsed.pnlUsd === 'number') {
          setBaseline(parsed.pnlUsd);
          capturedRef.current = anchorKey;
          return;
        }
      }
      window.localStorage.setItem(
        LIVE_BASELINE_PREFIX + anchorKey,
        JSON.stringify({ pnlUsd: currentShowcasePnl, capturedAt: new Date().toISOString() }),
      );
      setBaseline(currentShowcasePnl);
      capturedRef.current = anchorKey;
    } catch {
      // localStorage unavailable (privacy mode / quota) — anchor in-memory only.
      setBaseline(currentShowcasePnl);
      capturedRef.current = anchorKey;
    }
  }, [anchorKey, currentShowcasePnl]);

  if (baseline == null) return null;
  return Number((currentShowcasePnl - baseline).toFixed(2));
}

export function AgentDeskMetricsBar({
  activeDesk,
  userAgent,
  showcaseAgent,
  copyRelaySim,
  exchangeLabel,
  isLiveSession,
  instanceStatus,
}: {
  activeDesk: AgentDeskId;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  copyRelaySim?: CopyRelaySimState | null;
  exchangeLabel?: string | null;
  isLiveSession: boolean;
  instanceStatus?: string | null;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  let title = '';
  let borderClass = '';
  let badgeClass = '';
  let cells: MetricCell[] = [];

  // Live-copy showcase baseline (anchored to live-copy start). Hook must be
  // called unconditionally; only consumed by the isLiveSession branch below.
  const liveAnchorKey = isLiveSession ? (userAgent.userSessionStartedAt ?? null) : null;
  const showcaseDeltaSinceLiveStart = useShowcaseDeltaSinceLiveStart(
    liveAnchorKey,
    showcaseAgent.sessionPnlUsd ?? 0,
  );

  if (activeDesk === 'showcase') {
    const runway = showcaseAgent.startingBalance || 500;
    const equity = showcaseAgent.equityUsd ?? runway;
    const sessionPnl = showcaseAgent.sessionPnlUsd ?? equity - runway;
    const dailyPnl = showcaseAgent.dailyPnlUsd ?? sessionPnl;
    title = 'Global showcase bot · :7002';
    borderClass = 'border-violet-500/30 from-violet-950/20';
    badgeClass = 'text-violet-300';
    cells = [
      { label: 'Paper runway', value: formatUsd(runway, 0), hint: 'Admin research session' },
      { label: 'Current equity', value: formatUsd(equity, 0), hint: 'Cash + mark-to-market' },
      {
        label: "Today's P&L",
        value: `${dailyPnl >= 0 ? '+' : ''}${formatUsd(dailyPnl, 2)}`,
        accent: pnlColor(dailyPnl),
        hint: 'UTC session day',
      },
      {
        label: 'Session P&L',
        value: `${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`,
        accent: pnlColor(sessionPnl),
        hint: formatPercent(showcaseAgent.netReturnPct ?? 0),
      },
    ];
  } else if (activeDesk === 'relay-sim') {
    const sim = copyRelaySim;
    // `sim.showcasePnlUsd` is the showcase bot's session P&L DELTA since sim
    // start (anchored at Start, reset to 0 on Stop). The "ref" cell is the
    // DRIFT between the showcase bot and the sim's own P&L — positive means the
    // sim is lagging a winning bot, negative means the sim lost while the bot
    // was flat. NOT the same as Session P&L.
    const showcaseDelta = sim?.showcasePnlUsd ?? 0;
    const startingUsd = sim?.ledger?.startingUsd ?? 500;
    const simPnl = sim?.sessionPnlUsd ?? 0;
    const drift = showcaseDelta - simPnl;
    const cashWallet = Math.max(0, sim?.ledger?.derivativesUsd ?? startingUsd);
    const paperEquity = startingUsd + simPnl;
    title = `${exchange} relay simulation`;
    borderClass = 'border-sky-500/30 from-sky-950/20';
    badgeClass = 'text-sky-300';
    cells = [
      { label: 'Paper balance', value: formatUsd(cashWallet, 2), hint: 'Sim cash after fills' },
      { label: 'Sim equity', value: formatUsd(paperEquity, 2), hint: '$500 start + session P&L' },
      {
        label: 'Sim session P&L',
        value: `${simPnl >= 0 ? '+' : ''}${formatUsd(simPnl, 2)}`,
        accent: pnlColor(simPnl),
        hint: sim?.active ? 'Simulation running' : 'Start sim to track',
      },
      {
        label: 'Showcase P&L (ref)',
        value: `${drift >= 0 ? '+' : ''}${formatUsd(drift, 2)}`,
        accent: pnlColor(drift),
        hint: 'Drift vs admin bot · 0 = perfect mirror',
      },
    ];
  } else if (isLiveSession) {
    const freeMargin = userAgent.exchangeBalanceUsd ?? 0;
    const equity = userAgent.equityUsd ?? freeMargin;
    const sessionPnl = userAgent.sessionPnlUsd ?? 0;
    const unrealized = userAgent.unrealizedPnlUsd ?? 0;
    const paused = instanceStatus === 'PAUSED';
    // Drift = showcase session P&L (since live start) − live copy P&L.
    // Both sides reset to 0 when a new live session starts (new anchor key),
    // so drift starts at 0 and stays ~0 if the copy mirrors the bot perfectly.
    const showcaseDelta = showcaseDeltaSinceLiveStart ?? 0;
    const drift = showcaseDelta - sessionPnl;
    const sessionHint = paused
      ? 'Paused — open positions remain'
      : userAgent.openPositionSide
        ? `${userAgent.openPositionSide} open · unreal ${unrealized >= 0 ? '+' : ''}${formatUsd(unrealized, 2)}`
        : 'Since live start';
    title = paused ? `${exchange} live relay · paused` : `${exchange} live copy`;
    borderClass = paused ? 'border-amber-500/30 from-amber-950/15' : 'border-emerald-500/30 from-emerald-950/20';
    badgeClass = paused ? 'text-amber-300' : 'text-emerald-300';
    cells = [
      { label: 'Derivatives free', value: formatUsd(freeMargin, 2), hint: 'USDT available for copy' },
      { label: 'Account equity', value: formatUsd(equity, 2), hint: 'Live exchange account' },
      {
        label: 'Session P&L',
        value: `${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`,
        accent: pnlColor(sessionPnl),
        hint: `${sessionHint} · ${formatPercent(userAgent.netReturnPct ?? 0)}`,
      },
      {
        label: 'Showcase P&L (ref)',
        value: `${drift >= 0 ? '+' : ''}${formatUsd(drift, 2)}`,
        accent: pnlColor(drift),
        hint: 'Drift vs admin bot · 0 = perfect mirror',
      },
    ];
  } else {
    title = `Connect ${exchange} to copy`;
    borderClass = 'border-zinc-800 from-zinc-950/40';
    badgeClass = 'text-emerald-300';
    cells = [
      { label: 'Showcase equity', value: formatUsd(showcaseAgent.equityUsd ?? 500, 0), hint: 'Global bot :7002 reference' },
      { label: 'Showcase P&L', value: formatUsd(showcaseAgent.sessionPnlUsd ?? 0, 2), hint: 'Current global session' },
      { label: 'Your copy', value: '—', hint: 'Connect API to start' },
      { label: 'Return', value: '—', hint: 'Live relay after hire' },
    ];
  }

  return (
    <section
      className={`rounded-xl border bg-gradient-to-br to-zinc-950/60 px-4 py-3 ${borderClass}`}
    >
      <p className={`text-[10px] font-bold uppercase tracking-[0.15em] ${badgeClass}`}>{title}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cells.map((cell) => (
          <div key={cell.label}>
            <p className="text-[10px] uppercase tracking-widest text-zinc-500">{cell.label}</p>
            <p className={`mt-0.5 text-lg font-bold ${cell.accent ?? 'text-white'}`}>{cell.value}</p>
            {cell.hint ? <p className="mt-0.5 text-[10px] text-zinc-600">{cell.hint}</p> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
