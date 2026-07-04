'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { formatUsd, type CopyRelaySimState } from '@dcf/utils';
import {
  fetchAnalyzerSessionSummary,
  type AnalyzerSessionSummary,
  type TradingAgentSummary,
} from '@/lib/api';

/**
 * Compact global showcase bot strip — shown above live copy / relay sim desks.
 *
 * Source of truth: the analyzer-summary endpoint (:9001 via bot proxy) which
 * reports the FULL session since the last Fresh Collection wipeout — the same
 * numbers rendered by AgentAnalyzerPanel below. This keeps the reference bar
 * consistent with the full-session panel (e.g. 18 trades, +$10.20, $510, 2.0%)
 * instead of the short per-restart window on `showcaseAgent` (5 trades, +$6.63).
 *
 * When a relay sim is armed, the Session P&L / Return / Trades RESET to the
 * showcase delta since `copyRelaySim.startedAt` — i.e. baseline the showcase
 * full-session numbers to their value at the sim-start moment, then show
 * (current - baseline). The baseline is captured in localStorage keyed by the
 * sim-start timestamp so it survives refreshes; if the bot hasn't traded since
 * the sim started, the delta is $0 (the intended "reset to $0" behaviour).
 */
const ANALYZER_POLL_MS = 60_000;
const BASELINE_PREFIX = 'dcf:showcase-baseline:';

type ShowcaseBaseline = {
  pnlUsd: number;
  tradeCount: number;
  capturedAt: string;
};

function readBaseline(startedAt: string): ShowcaseBaseline | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(BASELINE_PREFIX + startedAt);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ShowcaseBaseline>;
    if (
      typeof parsed.pnlUsd === 'number' &&
      typeof parsed.tradeCount === 'number' &&
      typeof parsed.capturedAt === 'string'
    ) {
      return parsed as ShowcaseBaseline;
    }
    return null;
  } catch {
    return null;
  }
}

function writeBaseline(startedAt: string, baseline: ShowcaseBaseline): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(BASELINE_PREFIX + startedAt, JSON.stringify(baseline));
  } catch {
    /* storage quota / privacy mode — non-fatal */
  }
}

function fmtPct(n: number, digits = 1): string {
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(digits)}%`;
}

export function ShowcaseReferenceBar({
  showcaseAgent,
  botConnected,
  copyRelaySim,
}: {
  showcaseAgent: TradingAgentSummary;
  botConnected?: boolean;
  copyRelaySim?: CopyRelaySimState | null;
}) {
  const slug = showcaseAgent.slug;
  const runway = showcaseAgent.startingBalance || 500;
  const online = botConnected !== false;

  const simActive = Boolean(copyRelaySim?.active && copyRelaySim?.startedAt);
  const simStartedAt = simActive ? (copyRelaySim?.startedAt as string) : null;

  const [summary, setSummary] = useState<AnalyzerSessionSummary | null>(null);
  // Tracks the startedAt we've already captured a baseline for, so we capture
  // exactly once per sim session (the first time we have both a startedAt and
  // a valid analyzer summary).
  const capturedStartedAtRef = useRef<string | null>(null);

  const loadSummary = useCallback(async () => {
    if (!slug) return;
    try {
      const s = await fetchAnalyzerSessionSummary(slug);
      setSummary(s);
    } catch {
      setSummary((prev) => prev ?? { ok: false, error: 'summary fetch failed' });
    }
  }, [slug]);

  useEffect(() => {
    void loadSummary();
    const id = setInterval(() => void loadSummary(), ANALYZER_POLL_MS);
    return () => clearInterval(id);
  }, [loadSummary]);

  // Capture the showcase full-session baseline the first time we see a sim
  // armed with a valid analyzer summary. Stored in localStorage keyed by the
  // sim-start timestamp so it survives reloads and never collides between
  // distinct sim sessions.
  useEffect(() => {
    if (!simActive || !simStartedAt) return;
    if (capturedStartedAtRef.current === simStartedAt) return;
    if (
      !summary?.ok ||
      typeof summary.total_pnl_usd !== 'number' ||
      typeof summary.trade_count !== 'number' ||
      // Do not baseline on fabricated $500 / 0 / $0 envelopes.
      !(
        summary.trade_count > 0 ||
        (typeof summary.current_balance === 'number' && summary.current_balance !== runway) ||
        summary.total_pnl_usd !== 0
      )
    ) {
      return;
    }
    const existing = readBaseline(simStartedAt);
    if (existing) {
      capturedStartedAtRef.current = simStartedAt;
      return;
    }
    writeBaseline(simStartedAt, {
      pnlUsd: summary.total_pnl_usd,
      tradeCount: summary.trade_count,
      capturedAt: new Date().toISOString(),
    });
    capturedStartedAtRef.current = simStartedAt;
  }, [simActive, simStartedAt, summary]);

  // Prefer analyzer / full-session numbers only when they carry real data.
  // Reject fabricated $500 / 0 trades / $0 PnL (intermittent analyzer + slim fallback).
  const summaryUsable =
    summary?.ok === true &&
    ((typeof summary.trade_count === 'number' && summary.trade_count > 0) ||
      (typeof summary.current_balance === 'number' && summary.current_balance !== runway) ||
      (typeof summary.total_pnl_usd === 'number' && summary.total_pnl_usd !== 0));
  const fullEquity =
    summaryUsable && typeof summary?.current_balance === 'number'
      ? summary.current_balance
      : (showcaseAgent.equityUsd ?? runway);
  const fullPnlUsd =
    summaryUsable && typeof summary?.total_pnl_usd === 'number'
      ? summary.total_pnl_usd
      : (showcaseAgent.sessionPnlUsd ?? fullEquity - runway);
  const fullReturnPct =
    summaryUsable && typeof summary?.total_pnl_pct === 'number'
      ? summary.total_pnl_pct
      : (showcaseAgent.netReturnPct ?? 0);
  const fullTradeCount =
    summaryUsable && typeof summary?.trade_count === 'number'
      ? summary.trade_count
      : (showcaseAgent.tradeCount ?? 0);

  // Sim-start delta: baseline the showcase full-session values to their value
  // at sim-start, then show (current - baseline). Falls back to the full
  // session if the baseline was never captured (e.g. analyzer was down at the
  // exact start moment) — better than showing $0 with no context.
  const baseline = simStartedAt ? readBaseline(simStartedAt) : null;
  const usingDelta = simActive && baseline != null;
  const deltaPnl = usingDelta && baseline ? fullPnlUsd - baseline.pnlUsd : fullPnlUsd;
  const deltaTrades = usingDelta && baseline ? Math.max(0, fullTradeCount - baseline.tradeCount) : fullTradeCount;
  const deltaReturnPct = usingDelta ? (deltaPnl / runway) * 100 : fullReturnPct;

  // Prefer the API-authoritative sim-start delta (copyRelaySim.showcasePnlUsd)
  // when a sim has been started: it is anchored at Start, resets to 0 on Stop,
  // and is computed from the bot's stable session_pnl_usd — so it does not
  // flicker with the mark price. Only fall back to the analyzer/localStorage
  // delta when the API value is unavailable (e.g. tick hasn't landed yet).
  const apiDeltaPnl =
    typeof copyRelaySim?.showcasePnlUsd === 'number' ? copyRelaySim.showcasePnlUsd : null;
  const simHasStarted = Boolean(copyRelaySim?.startedAt);
  const usingApiDelta = simHasStarted && apiDeltaPnl != null;

  const equity = usingApiDelta ? runway + apiDeltaPnl! : usingDelta ? runway + deltaPnl : fullEquity;
  const sessionPnl = usingApiDelta ? apiDeltaPnl! : deltaPnl;
  const returnPct = usingApiDelta ? (apiDeltaPnl! / runway) * 100 : deltaReturnPct;
  const trades = deltaTrades;

  const sessionPnlHint = usingApiDelta
    ? 'Showcase P&L since your sim started'
    : usingDelta
      ? 'Showcase P&L since your sim started'
      : simActive
        ? 'Baseline capturing…'
        : 'Showcase full session · since fresh wipeout';

  return (
    <section className="rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-950/25 to-zinc-950/60 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-violet-300">
            Global showcase bot · :7002
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            {usingDelta
              ? 'Reference resets at sim start — showcase delta since your sim armed'
              : 'Admin research bot reference — same signals your copy or sim mirrors'}
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
            online
              ? 'bg-emerald-500/20 text-emerald-200'
              : 'bg-red-500/20 text-red-200'
          }`}
        >
          {online ? 'Online' : 'Offline'}
        </span>
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-4">
        <Metric label="Equity" value={formatUsd(equity, 0)} hint={usingDelta ? 'Runway + sim-start delta' : undefined} />
        <Metric
          label="Session P&L"
          value={`${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`}
          accent={sessionPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}
          hint={sessionPnlHint}
        />
        <Metric label="Return" value={fmtPct(returnPct)} hint={usingDelta ? 'Since sim start' : undefined} />
        <Metric label="Trades" value={String(trades)} hint={usingDelta ? 'Since sim start' : undefined} />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  accent = 'text-white',
  hint,
}: {
  label: string;
  value: string;
  accent?: string;
  hint?: string;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${accent}`}>{value}</p>
      {hint ? <p className="mt-0.5 text-[10px] text-zinc-600">{hint}</p> : null}
    </div>
  );
}
