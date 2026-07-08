'use client';

import { useEffect, useRef, useState } from 'react';
import { formatPercent, formatUsd } from '@dcf/utils';
import type { CopyRelaySimState, TradingAgentDashboardState } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-300';
}

type MetricCell = { label: string; value: string; hint?: string; accent?: string };

/**
 * Showcase bot session-P&L delta since an anchor moment (the user's explicit
 * "Start Live Copy" / "Resume" arm). Captures the showcase bot's cumulative
 * equity-based session P&L the first time it sees a valid `anchorKey` and
 * persists it in localStorage keyed by that anchor so it survives reloads.
 *
 * Semantics:
 * - `anchorKey` MUST be null when the live copy is not actively armed (e.g.
 *   PAUSED-with-stale-session, or not hired). Returning null drives the drift
 *   cell to render "—".
 * - On a fresh Start (new anchorKey), a fresh baseline is captured, so the
 *   drift reads 0 immediately. A new key has no persisted entry, so capture
 *   always overwrites stale data for that key.
 * - On Stop (anchorKey transitions non-null → null), the persisted baseline
 *   for the previous anchor is removed so the next Start begins at 0.
 * - On mount, any persisted `dcf:showcase-live-baseline:*` keys that don't
 *   match the current anchor are garbage-collected (one-time sweep).
 *
 * Returns the delta = current showcase session P&L − baseline, or null when
 * no active arm / baseline not yet captured. Lives in the frontend because
 * the live copy has no backend sim-state to store it in (unlike relay sim).
 */
const LIVE_BASELINE_PREFIX = 'dcf:showcase-live-baseline:';

function useShowcaseDeltaSinceLiveStart(
  anchorKey: string | null,
  currentShowcasePnl: number,
): number | null {
  const [baseline, setBaseline] = useState<number | null>(null);
  const capturedRef = useRef<string | null>(null);
  const prevAnchorRef = useRef<string | null>(null);
  const gcRanRef = useRef(false);

  useEffect(() => {
    // One-time garbage collection of stale persisted baselines on mount.
    if (!gcRanRef.current) {
      gcRanRef.current = true;
      try {
        const keep = anchorKey ? LIVE_BASELINE_PREFIX + anchorKey : null;
        for (let i = window.localStorage.length - 1; i >= 0; i--) {
          const k = window.localStorage.key(i);
          if (k && k.startsWith(LIVE_BASELINE_PREFIX) && k !== keep) {
            window.localStorage.removeItem(k);
          }
        }
      } catch {
        // localStorage unavailable — nothing to GC.
      }
    }

    if (!anchorKey) {
      // Stop / not armed: clear the persisted baseline for the previous anchor
      // so the next Start begins fresh at 0.
      const prev = prevAnchorRef.current;
      if (prev) {
        try {
          window.localStorage.removeItem(LIVE_BASELINE_PREFIX + prev);
        } catch {
          // ignore
        }
      }
      setBaseline(null);
      capturedRef.current = null;
      prevAnchorRef.current = null;
      return;
    }

    prevAnchorRef.current = anchorKey;
    if (capturedRef.current === anchorKey) return;
    // Wait for the showcase bot's session P&L to load before anchoring. On a
    // fresh arm (page load / resume), the first poll may report
    // currentShowcasePnl = 0 before the showcase agent summary arrives.
    // Anchoring at 0 makes the drift = currentShowcasePnl − 0 = the showcase's
    // full session P&L, which looks like unreset drift after a restart. Skip
    // capture until a non-zero value arrives; the showcase bot is essentially
    // never at exactly 0 session P&L, so this only delays anchoring by one
    // poll. A genuine 0 (brand-new showcase session) will anchor on the next
    // non-zero poll, and if it stays 0 the drift stays 0 — which is correct.
    if (currentShowcasePnl === 0) return;
    try {
      // Always capture fresh for a new anchor (overwrite any stale entry for
      // this key). A new key has no entry; an existing key only re-occurs if
      // the same session was re-armed, in which case re-anchoring is correct.
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
  liveBook,
}: {
  activeDesk: AgentDeskId;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  copyRelaySim?: CopyRelaySimState | null;
  exchangeLabel?: string | null;
  isLiveSession: boolean;
  instanceStatus?: string | null;
  /** Live-copy exchange book — used to recover real-trade realized P&L when the
   *  backend `sessionPnlUsd` hasn't captured it yet (already-flat reconcile race). */
  liveBook?: TradingAgentDashboardState['liveBook'] | null;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  let title = '';
  let borderClass = '';
  let badgeClass = '';
  let cells: MetricCell[] = [];

  // Live-copy showcase baseline (anchored to an EXPLICIT live-copy arm). The
  // hook must be called unconditionally; only the isLiveSession branch below
  // consumes its result. We only pass a non-null anchor when the relay is
  // ACTIVELY running (instanceStatus === 'ACTIVE') — a PAUSED relay carries a
  // stale `userSessionStartedAt` from days ago and must NOT anchor a drift
  // baseline (it would produce phantom drift with zero input from the copy).
  const liveArmed = isLiveSession && instanceStatus === 'ACTIVE';
  const liveAnchorKey = liveArmed ? (userAgent.userSessionStartedAt ?? null) : null;
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
    // start (anchored at Start server-side, reset to 0 on Stop). The "ref"
    // cell is the DRIFT between the showcase bot and the sim's own session
    // P&L — positive means the sim is lagging a winning bot, negative means
    // the sim lost while the bot was flat. NOT the same as Session P&L.
    //
    // Both sides are equity-based: the sim's `sessionPnlUsd` is computed
    // server-side as realized + unrealized − fees (see BitfinexSimTradingClient
    // .sessionPnlUsd), and the showcase `showcasePnlUsd` delta is derived from
    // the bot's equity-based session P&L, so the comparison is a true
    // mirror-fidelity measure including open positions.
    const showcaseDelta = sim?.showcasePnlUsd ?? 0;
    const simPnl = sim?.sessionPnlUsd ?? 0;
    const simActive = Boolean(sim?.active);
    const drift = simActive ? showcaseDelta - simPnl : null;
    const startingUsd = sim?.ledger?.startingUsd ?? 500;
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
        hint: simActive ? 'Simulation running' : 'Start sim to track',
      },
      {
        label: 'Showcase P&L (ref)',
        value: drift == null ? '—' : `${drift >= 0 ? '+' : ''}${formatUsd(drift, 2)}`,
        accent: drift == null ? 'text-zinc-300' : pnlColor(drift),
        hint: drift == null ? 'Start sim to track' : 'Drift vs showcase · 0 = perfect mirror',
      },
    ];
  } else if (isLiveSession) {
    const freeMargin = userAgent.exchangeBalanceUsd ?? 0;
    const equity = userAgent.equityUsd ?? freeMargin;
    const paused = instanceStatus === 'PAUSED';
    // Real-trade realized P&L recovered from the live book when the backend
    // `sessionPnlUsd` hasn't captured it yet. The already-flat reconcile path
    // can lag the dashboard poll, leaving sessionPnlUsd at 0 while closed trades
    // with real netUsd exist in the book. Sum those trades' Net USD so the
    // Session P&L reflects actual exchange results. The book is session-scoped
    // (buildSubscriberExchangeLiveBook filters participants by createdAt >=
    // sessionStart), so this sum is the session's realized P&L.
    const realizedFromBook = (liveBook?.trades ?? []).reduce(
      (sum, t) => sum + (Number.isFinite(t.netUsd) ? t.netUsd : 0),
      0,
    );
    const backendSessionPnl = userAgent.sessionPnlUsd ?? 0;
    const usedBookFallback = backendSessionPnl === 0 && Math.abs(realizedFromBook) > 0.0001;
    const sessionPnl = usedBookFallback ? realizedFromBook : backendSessionPnl;
    const unrealized = userAgent.unrealizedPnlUsd ?? 0;
    // Drift = showcase session P&L (since the user's explicit live-copy arm)
    // − live copy P&L, on the SAME equity basis. The showcase side
    // (`showcaseDeltaSinceLiveStart`) is the bot's equity-based session P&L
    // delta (realized + unrealized, since the bot reports session P&L that
    // way). The live side must match that basis, so we add unrealized P&L of
    // the user's open exchange position to the realized-only `sessionPnlUsd`
    // — otherwise drift swings purely from the showcase's unrealized while
    // the live copy is flat. `null` when no arm is active (PAUSED or no
    // baseline yet) → the cell renders "—".
    const showcaseDelta = showcaseDeltaSinceLiveStart;
    const liveEquityPnl = sessionPnl + unrealized;
    const drift = showcaseDelta == null ? null : showcaseDelta - liveEquityPnl;
    const sessionHint = paused
      ? 'Paused — open positions remain'
      : userAgent.openPositionSide
        ? `${userAgent.openPositionSide} open · unreal ${unrealized >= 0 ? '+' : ''}${formatUsd(unrealized, 2)}`
        : usedBookFallback
          ? 'Realized from closed trades'
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
        label: 'Drift vs showcase',
        value: drift == null ? '—' : `${drift >= 0 ? '+' : ''}${formatUsd(drift, 2)}`,
        accent: drift == null ? 'text-zinc-300' : pnlColor(drift),
        hint: drift == null ? 'Start Live Copy to track' : 'Drift vs showcase · 0 = perfect mirror',
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
