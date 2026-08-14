'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  formatPercent,
  buildTradingAgentActionShareText,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
  type RelaySimParticipantStats,
  type AgentShowcaseFlash,
  type TradingAgentDashboardState,
  type TradingAgentSessionStats,
} from '@dcf/utils';
import { AgentRentalCountdown, LiveCopyRentalBadge } from '@/components/agent-hub/agent-rental-countdown';
import { AgentAdminShowcaseControl } from '@/components/agent-hub/agent-admin-showcase-control';
import { AgentHubBottomBanner } from '@/components/agent-hub/agent-hub-bottom-banner';
import { AgentDeskView } from '@/components/agent-hub/agent-dual-desk-panels';
import { EMPTY_LIVE_BOOK } from '@/components/agent-hub/agent-transparency-tables';
import { AgentAnalyzerPanel } from '@/components/agent-hub/agent-analyzer-panel';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import { AgentShowcaseFlashBanner } from '@/components/agent-hub/agent-showcase-flash';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { directionGap } from '@/components/agent-hub/agent-direction-gap';
import { CopyTradeDetailsStrip, CopyTradeHub } from '@/components/agent-hub/copy-trade-hub';
import type { RelayFidelitySnapshot } from '@/components/agent-hub/agent-relay-fidelity-panel';
import { ExchangeHirePanel } from '@/components/agent-hub/exchange-hire-panel';
import { AgentActivityFeed } from '@/components/agent-hub/live-mission-control';
import { ShareOnXButton } from '@/components/share-on-x-button';
import { mergeDeskActivity, liveBookToActivity, filterLiveExchangeActivity } from '@/lib/livebook-activity';
import { resolveInitialAgentDesk } from '@/components/agent-hub/agent-live-execution-view';

const deskStorageKey = (slug: string) => `agent-hub-desk-${slug}`;

function readStoredDesk(slug: string): AgentDeskId | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem(deskStorageKey(slug));
  if (raw === 'showcase' || raw === 'live' || raw === 'relay-sim') return raw;
  return null;
}

function sessionSummaryToStats(
  summary: AnalyzerSessionSummary | null,
): TradingAgentSessionStats {
  if (!summary?.ok) return {};
  // Reject fabricated $500 / 0 trades / $0 PnL envelopes from intermittent analyzer fallbacks.
  const trades = summary.executed_count ?? summary.trade_count;
  const hasReal =
    (typeof trades === 'number' && trades > 0) ||
    (typeof summary.current_balance === 'number' && summary.current_balance !== 500) ||
    (typeof summary.total_pnl_usd === 'number' && summary.total_pnl_usd !== 0);
  if (!hasReal) return {};
  return {
    pnlUsd: summary.total_pnl_usd ?? null,
    pnlPct: summary.total_pnl_pct ?? null,
    winRate: summary.win_rate ?? null,
    trades: summary.executed_count ?? null,
  };
}
import {
  type PublicAgentStatus,
  type TradingAgentActivityEntry,
  type TradingAgentSummary,
  type AnalyzerSessionSummary,
  fetchAnalyzerSessionSummary,
} from '@/lib/api';

const STRATEGY_TAGS = ['BTC Markets', 'Low Risk', 'Trend Following', 'Long Bias'];

function PublicReasoningPanel({
  dashboard,
  agentName,
  slug,
}: {
  dashboard: TradingAgentDashboardState;
  agentName: string;
  slug: string;
}) {
  const verdict = dashboard.latestAiVerdict;
  const t = dashboard.currentThinking;
  const reasoning =
    dashboard.aiReasoning?.trim() ||
    verdict?.comment?.trim() ||
    verdict?.reason?.trim() ||
    t.conclusion?.trim() ||
    dashboard.noTradeReason?.trim() ||
    null;
  const bias =
    dashboard.regime && dashboard.regime !== 'RANGE'
      ? dashboard.regime
      : verdict?.marketRegime || t.market || 'Assessing';
  const shareText = buildTradingAgentActionShareText({
    agentName,
    action: verdict
      ? `${verdict.decision} ${verdict.direction}`.trim()
      : dashboard.aiDecision,
    reason: verdict?.reason ?? dashboard.noTradeReason,
    edgeScore: verdict?.edgeScore ?? dashboard.currentEdge,
    edgeRequired: verdict?.requiredEdge ?? dashboard.requiredEdge,
    marketRegime: bias,
    hubUrl: `https://doxxedcrypto.digital/agent-hub/${slug}`,
  });
  const directionOnly = slug === 'conservative-btc';
  const gap = directionGap(verdict?.rawScoreGap);

  return (
    <section className="rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/30 to-zinc-950/50 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-sm font-bold uppercase tracking-widest text-violet-300">Latest reasoning</h2>
        <ShareOnXButton text={shareText} label="Share to X" className="shrink-0" />
      </div>
      {verdict?.updatedAt && (
        <p className="mt-2 text-[10px] uppercase tracking-widest text-zinc-500">
          Updated {new Date(verdict.updatedAt).toLocaleString()}
        </p>
      )}
      {reasoning ? (
        <div className="mt-4 space-y-3 text-sm leading-relaxed text-zinc-200">
          {verdict ? (
            <>
              <p>
                <span className="font-semibold text-violet-200">Verdict:</span>{' '}
                {verdict.decision} · {verdict.direction}
                {!directionOnly ? ` · ${verdict.winProbability}% confidence` : ''}
              </p>
              {verdict.reason && (
                <p>
                  <span className="font-semibold text-violet-200">Reason:</span> {verdict.reason}
                </p>
              )}
              {verdict.comment && (
                <p className="whitespace-pre-wrap rounded-xl border border-zinc-800/80 bg-black/30 p-4 text-zinc-100">
                  <span className="block text-[10px] font-bold uppercase tracking-widest text-zinc-500">
                    AI output
                  </span>
                  {verdict.comment}
                </p>
              )}
              {verdict.blockReason && verdict.blockReason !== verdict.reason && (
                <p className="text-zinc-400">
                  <span className="font-semibold text-amber-200/90">Pipeline block:</span>{' '}
                  {verdict.blockReason}
                </p>
              )}
            </>
          ) : (
            <p className="italic">&ldquo;{reasoning}&rdquo;</p>
          )}
        </div>
      ) : (
        <p className="mt-4 text-sm text-zinc-500">Waiting for the bot&apos;s next market assessment…</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border border-emerald-500/40 bg-emerald-950/40 px-3 py-1 font-semibold text-emerald-200">
          Bias: {bias}
        </span>
        {directionOnly ? (
          <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
            {gap
              ? `Raw AI gap ${gap.raw}/100 · bucket ${gap.bucketLabel}`
              : 'Direction-only call · probability not requested'}
          </span>
        ) : (
          <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
            Confidence: {verdict?.winProbability ?? dashboard.aiWinProbability ?? 0}%
          </span>
        )}
        <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
          Decision: {dashboard.aiDecision}
        </span>
        <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
          Edge {verdict?.edgeScore ?? dashboard.currentEdge}/{verdict?.requiredEdge ?? dashboard.requiredEdge}
        </span>
      </div>
      {dashboard.transparency?.reason && (
        <p className="mt-3 text-xs text-zinc-500">{dashboard.transparency.reason}</p>
      )}
    </section>
  );
}

function HireSidebar({
  slug,
  agent,
  signedIn,
  hired,
  instanceStatus,
  instanceMode,
  exchangeProvider,
  exchangeLabel,
  exchangeConnected,
  onPauseInstance,
  onResumeInstance,
  onStartRelaySim,
  onStopRelaySim,
  relaySimBusy,
  copyRelaySim,
  instanceBusy,
  rentalExpiresAt,
  accessToken,
  activeDesk,
  onRenewRental,
  renewBusy,
  botConnected,
  copyRelayReconcile,
  relayFidelity,
  tradeLifecycleIntegrity,
  onSyncProtectionBreach,
  syncProtectionBusy,
  relayLastTransition,
}: {
  slug: string;
  agent: TradingAgentSummary;
  signedIn: boolean;
  hired: boolean;
  accessToken?: string;
  instanceStatus?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  exchangeProvider?: string | null;
  exchangeLabel?: string | null;
  exchangeConnected?: boolean;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  relaySimBusy?: boolean;
  copyRelaySim?: CopyRelaySimState | null;
  instanceBusy?: boolean;
  rentalExpiresAt?: string | null;
  activeDesk: AgentDeskId;
  onRenewRental?: () => void;
  renewBusy?: boolean;
  botConnected?: boolean;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  relayFidelity?: import('@/components/agent-hub/agent-relay-fidelity-panel').RelayFidelitySnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  onSyncProtectionBreach?: (opts?: { flatten?: boolean }) => void;
  syncProtectionBusy?: boolean;
  relayLastTransition?: TradingAgentDashboardState['relayLastTransition'];
}) {
  const isLiveHired = hired && instanceMode === 'live';
  const rentalExpired =
    rentalExpiresAt != null && new Date(rentalExpiresAt).getTime() <= Date.now();

  if (activeDesk === 'relay-sim') {
    const simActive = Boolean(copyRelaySim?.active);
    return (
      <aside className="space-y-4 xl:sticky xl:top-28">
        <div className="rounded-2xl border border-sky-500/30 bg-sky-950/15 p-5">
          <p className="text-xs font-bold uppercase text-sky-400">Relay simulation</p>
          <p className="mt-2 text-xs text-zinc-400">
            Live-API dress rehearsal — no rental, but it can place one capped real Bitfinex order.
            Use it only to validate sync with the showcase bot before going live.
          </p>
          <p className="mt-3 rounded-lg border border-zinc-800 bg-black/25 px-3 py-2 text-xs text-zinc-300">
            API:{' '}
            <strong className={isLiveHired ? 'text-emerald-300' : 'text-amber-300'}>
              {isLiveHired ? `${exchangeLabel ?? 'Bitfinex'} connected` : 'Not connected'}
            </strong>
          </p>
          {isLiveHired ? (
            <div className="mt-3">
              {simActive ? (
                <button
                  type="button"
                  disabled={relaySimBusy}
                  onClick={onStopRelaySim}
                  className="w-full rounded-lg border border-red-500/50 bg-red-950/40 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
                >
                  {relaySimBusy ? '…' : 'Stop simulation trading'}
                </button>
              ) : (
                <button
                  type="button"
                  disabled={relaySimBusy}
                  onClick={onStartRelaySim}
                  className="w-full rounded-lg border border-sky-500/50 bg-sky-600 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
                >
                  {relaySimBusy ? '…' : 'Start simulation trading'}
                </button>
              )}
            </div>
          ) : (
            <Link
              href={
                signedIn
                  ? `/agent-hub/${slug}/hire?exchange=bitfinex`
                  : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=bitfinex`)}`
              }
              className="mt-3 block rounded-lg bg-sky-600 py-2.5 text-center text-sm font-bold text-white hover:bg-sky-500"
            >
              Connect API to simulate
            </Link>
          )}
        </div>
      </aside>
    );
  }

  return (
    <aside className="space-y-4 xl:sticky xl:top-28">
      {isLiveHired && rentalExpiresAt && (
        <AgentRentalCountdown
          expiresAt={rentalExpiresAt}
          onRenew={onRenewRental}
          renewBusy={renewBusy}
          costWeek={agent.costDdollarWeek ?? 2000}
        />
      )}

      <ExchangeHirePanel
        slug={slug}
        signedIn={signedIn}
        costWeek={agent.costDdollarWeek ?? 2000}
        hired={hired}
        instanceMode={instanceMode}
        instanceStatus={instanceStatus}
        exchangeProvider={exchangeProvider}
        exchangeLabel={exchangeLabel}
        exchangeConnected={exchangeConnected}
        exchangeBalanceUsd={agent.exchangeBalanceUsd ?? agent.balanceUsd}
        rentalExpired={rentalExpired}
        onStopRelay={onPauseInstance}
        onStartRelay={onResumeInstance}
        relayBusy={instanceBusy}
        syncInput={{
          botConnected,
          reconcile: copyRelayReconcile ?? undefined,
          fidelity: relayFidelity ?? undefined,
          lifecycle: tradeLifecycleIntegrity ?? undefined,
        }}
        onSyncProtectionBreach={onSyncProtectionBreach}
        syncProtectionBusy={syncProtectionBusy}
        relayLastTransition={relayLastTransition}
      />

      {isLiveHired && (
        <AgentLiveTradeExportButton
          slug={slug}
          token={accessToken}
          signedIn={signedIn}
          exchangeLabel={exchangeLabel ?? 'Bitfinex'}
          compact
        />
      )}
    </aside>
  );
}

function StateIntegrityHeader({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const si = dashboard.stateIntegrity;
  if (!si) {
    return (
      <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-950/20 px-4 py-3 text-xs text-amber-200/90">
        <strong>Live bot snapshot unavailable.</strong> Showing cached/database numbers.
        Fly.io is the production owner; refresh the Agent Hub feed. Desktop :7002/:9001 are optional viewers.
      </div>
    );
  }
  const uptimeH = dashboard.botUptimeHours ?? 0;
  const windowH = dashboard.dataWindowHours ?? uptimeH;
  const ageSec = Number.isFinite(Number(si.snapshot_age_sec))
    ? Number(si.snapshot_age_sec)
    : Number.POSITIVE_INFINITY;
  const source = dashboard.snapshotSource ?? 'live_bot';
  const snapshotFresh = ageSec >= 0 && ageSec < 90;
  const wsOk = si.ws_connected && si.rest_healthy && snapshotFresh;
  // REST_FALLBACK (ws down) is NOT "offline" — if REST is healthy and the snapshot is
  // fresh, the bot is up and serving data via REST. Only a stale snapshot / unhealthy
  // REST means the bot is truly unreachable. Without this, a bot in long REST_FALLBACK
  // (ws disconnected for hours) showed a red "Bot offline" dot while still trading fine.
  const restUp = !wsOk && si.rest_healthy && snapshotFresh;
  const dot = wsOk ? 'bg-emerald-400' : restUp ? 'bg-amber-400' : 'bg-rose-400';
  return (
    <div className="mb-4 rounded-xl border border-zinc-700/60 bg-zinc-900/50 px-4 py-3 text-xs text-zinc-300">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5 font-semibold text-zinc-100">
          <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
          {wsOk
            ? 'Fly bot connected'
            : restUp
              ? 'Fly bot online · REST price fallback'
              : !snapshotFresh
                ? 'Fly feed stale · not live'
                : 'Fly bot unavailable'}
        </span>
        <span>Data from last {windowH > 0 ? `${windowH.toFixed(1)}h` : '—'}</span>
        <span>Running for {uptimeH > 0 ? `${uptimeH.toFixed(1)}h` : '—'}</span>
        <span>source: <span className="text-zinc-100">{source}</span></span>
        <span>seq: <span className="text-zinc-100">{si.snapshot_seq}</span></span>
        <span>
          age:{' '}
          <span className="text-zinc-100">
            {Number.isFinite(ageSec) ? `${Math.round(ageSec)}s` : 'unknown'}
          </span>
        </span>
        <span>exchange: <span className="text-zinc-100">{si.exchange}</span></span>
        <span>genome: <span className="text-zinc-100">{si.genome_recorder}</span></span>
        <span>relay: <span className="text-zinc-100">{si.relay_push?.configured ? `on (${si.relay_push.seq ?? 0})` : 'off'}</span></span>
        <span>live: <span className="text-zinc-100">{si.bitfinex_live_enabled ? 'armed' : 'sim'}</span></span>
      </div>
      <div className="mt-1 text-[10px] text-zinc-500">
        generated_at: {si.snapshot_ts} · ws {si.ws_status} · price_age {si.price_age_sec ?? '—'}s ·
        last_fill {si.last_fill_sec_ago != null ? `${Math.round(si.last_fill_sec_ago)}s ago` : '—'} ·
        v{si.bot_version}
      </div>
    </div>
  );
}

export function AgentPublicProfile({
  slug,
  agent,
  dashboard,
  activity: _activity,
  allAgents,
  following,
  hired,
  signedIn,
  isAdmin,
  adminToken,
  accessToken,
  botConnected,
  flyReachable,
  executionPaused,
  publicStatus,
  instanceStatus,
  instanceLastError,
  instanceMode,
  exchangeProvider,
  exchangeLabel,
  exchangeConnected,
  viewScope = 'showcase',
  showcaseNote,
  showcaseFlash,
  showcaseAgent,
  showcaseLiveBook,
  exchangeLiveBook,
  relaySimLiveBook,
  copyRelaySim,
  copyRelayReconcile,
  copyRelayLimitChain,
  tradeLifecycleIntegrity,
  relaySimParticipantStats,
  relayFidelity,
  showcaseActivity: showcaseActivityProp,
  userActivity: userActivityProp,
  onFollow,
  followBusy,
  onPauseInstance,
  onResumeInstance,
  onStartRelaySim,
  onStopRelaySim,
  onResetRelaySim,
  relaySimBusy,
  onAdminRefresh,
  instanceBusy,
  rentalExpiresAt,
  onRenewRental,
  renewBusy,
  onSyncProtectionBreach,
  syncProtectionBusy,
  relaySimLiveView,
  onRelaySimLiveViewChange,
}: {
  slug: string;
  agent: TradingAgentSummary;
  dashboard: TradingAgentDashboardState;
  activity: TradingAgentActivityEntry[];
  allAgents?: TradingAgentSummary[];
  following: boolean;
  hired: boolean;
  signedIn: boolean;
  isAdmin?: boolean;
  adminToken?: string;
  accessToken?: string;
  botConnected?: boolean;
  /** True when the canonical Fly bot responds to lightweight health probes
   *  even if botConnected is false because /api/state is stale. */
  flyReachable?: boolean;
  executionPaused?: boolean;
  publicStatus: PublicAgentStatus;
  instanceStatus?: string | null;
  /** TradingAgentInstance.lastError for the user's hire, surfaced from backend. */
  instanceLastError?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  exchangeProvider?: string | null;
  exchangeLabel?: string | null;
  exchangeConnected?: boolean;
  viewScope?: 'showcase' | 'user';
  showcaseNote?: string | null;
  showcaseFlash?: AgentShowcaseFlash | null;
  showcaseAgent?: TradingAgentSummary;
  showcaseLiveBook?: TradingAgentDashboardState['liveBook'];
  exchangeLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  relaySimLiveBook?: TradingAgentDashboardState['liveBook'] | null;
  copyRelaySim?: CopyRelaySimState | null;
  copyRelayReconcile?: CopyRelayReconcileSnapshot | null;
  copyRelayLimitChain?: CopyRelayLimitChainSnapshot | null;
  tradeLifecycleIntegrity?: TradeLifecycleIntegritySnapshot | null;
  relaySimParticipantStats?: RelaySimParticipantStats | null;
  relayFidelity?: RelayFidelitySnapshot | null;
  showcaseActivity?: TradingAgentActivityEntry[];
  userActivity?: TradingAgentActivityEntry[];
  onFollow?: () => void;
  followBusy?: boolean;
  onPauseInstance?: () => void;
  onResumeInstance?: () => void;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  onResetRelaySim?: () => void;
  relaySimBusy?: boolean;
  onAdminRefresh?: () => void;
  instanceBusy?: boolean;
  rentalExpiresAt?: string | null;
  onRenewRental?: () => void;
  renewBusy?: boolean;
  onSyncProtectionBreach?: (opts?: { flatten?: boolean }) => void;
  syncProtectionBusy?: boolean;
  relaySimLiveView?: boolean;
  onRelaySimLiveViewChange?: (enabled: boolean) => void;
}) {
  const showExecutionPublic = slug === 'conservative-btc' && !isAdmin;
  const isCopySession = hired && instanceMode === 'copy';
  const isLiveSession = hired && instanceMode === 'live';
  const relaySimActive = Boolean(copyRelaySim?.active);
  const relaySimDeskAvailable = isLiveSession && exchangeProvider === 'bitfinex';
  const [activeDesk, setActiveDesk] = useState<AgentDeskId>(() =>
    resolveInitialAgentDesk({
      storedDesk: readStoredDesk(slug),
      isLiveSession,
      relaySimActive,
    }),
  );

  // Full-session stats (Total P&L, P&L %, Win Rate, Trades) — same source the
  // Analyzer panel renders. Reused for the hero "Share to X" text so the tweet
  // matches the dashboard instead of the stale "WAITING / Edge Score: 0/0".
  const [sessionSummary, setSessionSummary] = useState<AnalyzerSessionSummary | null>(null);
  const loadSessionSummary = useCallback(
    async (s: string) => {
      try {
        const value = await fetchAnalyzerSessionSummary(s);
        setSessionSummary(value);
      } catch {
        setSessionSummary((prev) => prev ?? { ok: false, error: 'summary fetch failed' });
      }
    },
    [],
  );
  useEffect(() => {
    void loadSessionSummary(slug);
    const id = setInterval(() => void loadSessionSummary(slug), 60_000);
    return () => clearInterval(id);
  }, [slug, loadSessionSummary]);

  useEffect(() => {
    localStorage.setItem(deskStorageKey(slug), activeDesk);
  }, [activeDesk, slug]);

  // Sim state persists in Neon — restore the relay-sim desk after refresh when sim is still active.
  useEffect(() => {
    if (!relaySimDeskAvailable) return;
    const stored = readStoredDesk(slug);
    if (relaySimActive) {
      setActiveDesk('relay-sim');
    } else if (stored) {
      setActiveDesk(stored);
    }
  }, [slug, relaySimActive, relaySimDeskAvailable]);

  const resolvedDesk: AgentDeskId =
    activeDesk === 'relay-sim' && !relaySimDeskAvailable
      ? 'live'
      : activeDesk === 'relay-sim' && relaySimDeskAvailable
        ? 'relay-sim'
        : activeDesk;
  const isUserSession = resolvedDesk !== 'showcase' && (viewScope === 'user' || isCopySession || isLiveSession);
  const integrity = dashboard.stateIntegrity;
  const canonicalSnapshotFresh =
    integrity == null ||
    (integrity.rest_healthy && Number(integrity.snapshot_age_sec ?? Infinity) < 90);
  const showcaseOnline =
    resolvedDesk === 'showcase' &&
    botConnected &&
    publicStatus !== 'offline' &&
    canonicalSnapshotFresh;
  const isLive = showcaseOnline && !executionPaused;
  const showcasePaused = showcaseOnline && executionPaused;
  const heroBadge = isLiveSession
    ? instanceStatus === 'PAUSED'
      ? { label: 'Relay off', className: 'bg-red-500/20 text-red-200 ring-1 ring-red-500/40' }
      : relaySimActive
        ? { label: 'Sim active', className: 'bg-sky-500/20 text-sky-200 ring-1 ring-sky-500/40' }
        : { label: 'Live copy', className: 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40' }
    : isCopySession
      ? { label: 'Legacy paper', className: 'bg-zinc-800 text-zinc-400 ring-1 ring-zinc-600' }
    : isLive
      ? { label: 'Live', className: 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40' }
      : showcasePaused
        ? { label: 'Online · trading paused', className: 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-500/40' }
      : publicStatus === 'updating'
        ? { label: 'Updating', className: 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-500/40' }
        : { label: 'Offline', className: 'bg-zinc-800 text-zinc-400' };
  const statusLabel = resolvedDesk === 'showcase'
    ? isLive
      ? 'Admin showcase (observe only)'
      : botConnected && !canonicalSnapshotFresh
        ? 'Fly feed stale · awaiting a fresh signed snapshot'
      : publicStatus === 'offline' && !botConnected
        ? 'Showcase stopped'
      : executionPaused && botConnected
          ? 'Fly bot online · trading paused'
          : 'Showcase offline'
    : isLiveSession
    ? instanceStatus === 'PAUSED'
      ? `Relay stopped · ${exchangeLabel ?? 'Exchange'} protected`
      : relaySimActive
        ? `Relay simulation on ${exchangeLabel ?? 'Bitfinex'}`
        : `Live copy on ${exchangeLabel ?? 'your exchange'}`
    : isCopySession
      ? 'Legacy DDollar paper — connect Bitfinex for real copy'
      : 'Personal session unavailable';
  const statusColor = isUserSession
    ? 'text-violet-300'
    : isLive
      ? 'text-emerald-400'
      : showcasePaused || (executionPaused && publicStatus !== 'offline')
        ? 'text-amber-400'
        : 'text-zinc-400';
  const others = (allAgents ?? []).filter((a) => a.slug !== slug && a.status !== 'PAUSED').slice(0, 4);
  const deskShowcaseAgent = showcaseAgent ?? agent;
  const heroAgent =
    activeDesk === 'live' && isLiveSession
      ? agent
      : activeDesk === 'relay-sim' && copyRelaySim
        ? {
            ...deskShowcaseAgent,
            sessionPnlUsd: copyRelaySim.sessionPnlUsd ?? 0,
            equityUsd:
              (copyRelaySim.ledger?.startingUsd ?? 500) + (copyRelaySim.sessionPnlUsd ?? 0),
            netReturnPct:
              ((copyRelaySim.sessionPnlUsd ?? 0) / (copyRelaySim.ledger?.startingUsd ?? 500)) *
              100,
            tradeCount: relaySimLiveBook?.trades?.length ?? deskShowcaseAgent.tradeCount,
            balanceUsd: copyRelaySim.ledger?.derivativesUsd ?? 500,
          }
        : deskShowcaseAgent;
  const heroShareText = buildTradingAgentActionShareText({
    agentName: heroAgent.name,
    action: dashboard.latestAiVerdict
      ? `${dashboard.latestAiVerdict.decision} ${dashboard.latestAiVerdict.direction}`.trim()
      : dashboard.aiDecision,
    reason:
      dashboard.latestAiVerdict?.reason ??
      dashboard.noTradeReason ??
      dashboard.currentThinking.conclusion,
    edgeScore: dashboard.latestAiVerdict?.edgeScore ?? dashboard.currentEdge,
    edgeRequired: dashboard.latestAiVerdict?.requiredEdge ?? dashboard.requiredEdge,
    marketRegime: dashboard.latestAiVerdict?.marketRegime ?? dashboard.currentThinking.market,
    hubUrl: `https://doxxedcrypto.digital/agent-hub/${slug}`,
    sessionStats: sessionSummaryToStats(sessionSummary),
  });
  const dualDeskMode = isLiveSession ? 'live' : isCopySession ? 'copy' : 'showcase';
  const showcaseBook = showcaseLiveBook ?? EMPTY_LIVE_BOOK;
  const showcaseAct = mergeDeskActivity(
    showcaseActivityProp ?? [],
    liveBookToActivity(showcaseBook, 'showcase-ui'),
  );
  const simAct = liveBookToActivity(relaySimLiveBook, 'relay-sim-ui');
  const userAct = isLiveSession
    ? mergeDeskActivity(
        userActivityProp ?? [],
        filterLiveExchangeActivity(
          liveBookToActivity(exchangeLiveBook, 'user-ui', 'positions-only'),
        ),
      )
    : mergeDeskActivity(
        userActivityProp ?? [],
        liveBookToActivity(exchangeLiveBook, 'user-ui'),
      );
  const deskActivity =
    activeDesk === 'relay-sim' ? simAct : activeDesk === 'live' ? userAct : showcaseAct;

  const deskHeroBadge =
    resolvedDesk === 'showcase'
      ? isLive
        ? { label: 'Showcase live', className: 'bg-violet-500/20 text-violet-200 ring-1 ring-violet-500/40' }
        : showcasePaused
          ? { label: 'Fly online · paused', className: 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-500/40' }
        : publicStatus === 'updating'
          ? { label: 'Updating', className: 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-500/40' }
          : { label: 'Showcase', className: 'bg-zinc-800 text-zinc-400' }
      : resolvedDesk === 'relay-sim'
        ? relaySimActive
          ? { label: 'Sim active', className: 'bg-sky-500/20 text-sky-200 ring-1 ring-sky-500/40' }
          : { label: 'Relay sim', className: 'bg-sky-500/20 text-sky-300/80 ring-1 ring-sky-500/30' }
        : heroBadge;

  const copyDetailsMode: 'live' | null =
    resolvedDesk === 'live' && isLiveSession ? 'live' : null;

  const hireHref = signedIn
    ? `/agent-hub/${slug}/hire?exchange=bitfinex`
    : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=bitfinex`)}`;

  const deskViewProps = {
    activeDesk: resolvedDesk,
    mode: dualDeskMode,
    exchangeLabel,
    userAgent: agent,
    showcaseAgent: deskShowcaseAgent,
    exchangeLiveBook,
    showcaseLiveBook: showcaseBook,
    relaySimLiveBook,
    copyRelaySim,
    copyRelayReconcile,
    copyRelayLimitChain,
    tradeLifecycleIntegrity,
    relaySimParticipantStats,
    relayFidelity,
    botConnected,
    flyReachable,
    instanceLastError,
    userActivity: resolvedDesk === 'relay-sim' ? simAct : userAct,
    showcaseActivity: showcaseAct,
    slug,
    accessToken: accessToken ?? adminToken,
    signedIn,
    instanceStatus,
    onStartRelaySim,
    onStopRelaySim,
    onResetRelaySim,
    relaySimBusy,
    executionOnly: showExecutionPublic,
    relaySimLiveView,
    onRelaySimLiveViewChange,
  } as const;

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      {showcaseNote && resolvedDesk === 'showcase' && (
        <p className="mb-4 rounded-xl border border-violet-500/25 bg-violet-950/20 px-4 py-3 text-sm text-violet-100/90">
          {showcaseNote}
        </p>
      )}

      {slug === 'conservative-btc' && resolvedDesk === 'showcase' ? (
        <AgentShowcaseFlashBanner flash={showcaseFlash ?? null} className="mb-4" />
      ) : null}

      {resolvedDesk === 'showcase' ? <StateIntegrityHeader dashboard={dashboard} /> : null}

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_300px]">
        <div className="min-w-0 space-y-6">
          <section className="overflow-hidden rounded-2xl border border-zinc-800 bg-gradient-to-br from-zinc-900/95 via-zinc-950 to-violet-950/25 p-6 sm:p-8">
            <div className="flex flex-col gap-6 lg:flex-row lg:justify-between">
              <div className="flex min-w-0 gap-4">
                <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-amber-500/20 text-3xl ring-1 ring-amber-500/30">
                  ₿
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-2xl font-bold sm:text-3xl">{agent.name}</h1>
                    <span className="text-blue-400" title="Verified">✓</span>
                    {slug === 'conservative-btc' && Number.isFinite(dashboard.currentPrice) && dashboard.currentPrice > 0 ? (
                      <span
                        className="rounded-full bg-amber-500/10 px-2.5 py-0.5 text-xs font-semibold tabular-nums text-amber-200 ring-1 ring-amber-500/30"
                        title="Latest BTC price from the live showcase snapshot"
                      >
                        BTC ${dashboard.currentPrice.toLocaleString('en-US', { maximumFractionDigits: 2 })}
                      </span>
                    ) : null}
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${deskHeroBadge.className}`}
                    >
                      {deskHeroBadge.label}
                    </span>
                    {dashboard.currentAction === 'ORDER PENDING' && (
                      <span className="rounded-full bg-blue-500/20 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-blue-200 ring-1 ring-blue-500/40">
                        Limit pending
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-zinc-400">Low-Risk BTC Trend Following Strategy</p>
                  <p className="mt-2 text-xs text-violet-300">
                    Built by @bitbro4crypto · Verified Founder
                  </p>
                  <dl className="mt-3 grid gap-1 text-xs sm:grid-cols-2">
                    <div>
                      <span className="text-zinc-600">Strategy type </span>
                      <span className="text-zinc-300">Trend following</span>
                    </div>
                    <div>
                      <span className="text-zinc-600">Timeframe </span>
                      <span className="text-zinc-300">Swing · 4H / 1D</span>
                    </div>
                    <div>
                      <span className="text-zinc-600">Hiring fee </span>
                      <span className="text-zinc-300">
                        {(agent.costDdollarWeek ?? 2000).toLocaleString()} DDollar / week
                      </span>
                    </div>
                  </dl>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {STRATEGY_TAGS.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-zinc-700 bg-zinc-900/60 px-2.5 py-0.5 text-[10px] text-zinc-400"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              <div className="shrink-0 lg:w-56">
                <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Status</p>
                <p className={`mt-1 text-sm font-semibold ${statusColor}`}>{statusLabel}</p>
                <div className="mt-3">
                  <ShareOnXButton text={heroShareText} label="Share to X" className="w-full justify-center" />
                </div>
              </div>
            </div>

            {isAdmin && adminToken && (
              <div className="mt-6">
                <AgentAdminShowcaseControl
                  token={adminToken}
                  executionPaused={executionPaused}
                  botConnected={botConnected}
                  flyReachable={flyReachable}
                  onUpdated={onAdminRefresh}
                />
              </div>
            )}

            <div className="mt-6 flex flex-wrap gap-3">
              {!isLiveSession ? (
                <Link
                  href={hireHref}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-bold text-white shadow-md shadow-emerald-900/30 hover:bg-emerald-500"
                >
                  Connect {exchangeLabel ?? 'Bitfinex'} &amp; copy
                </Link>
              ) : null}
              {resolvedDesk === 'showcase' ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-xl border border-zinc-600 bg-transparent px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-violet-500/50"
                >
                  Observe showcase
                </button>
              ) : null}
              {isLiveSession && rentalExpiresAt && resolvedDesk === 'live' && (
                <LiveCopyRentalBadge expiresAt={rentalExpiresAt} />
              )}
              {onFollow && (
                <button
                  type="button"
                  onClick={onFollow}
                  disabled={followBusy}
                  className="rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-300 hover:border-violet-500/50 disabled:opacity-50"
                >
                  {following ? 'Following ✓' : 'Follow'}
                </button>
              )}
            </div>
          </section>

          {slug === 'conservative-btc' && resolvedDesk === 'showcase' && (
            <div className="mt-6">
              <AgentAnalyzerPanel slug={slug} summary={sessionSummary} />
            </div>
          )}

          <div className="mt-6">
            <CopyTradeHub
              slug={slug}
              signedIn={signedIn}
              agent={agent}
              exchangeLabel={exchangeLabel}
              exchangeProvider={exchangeProvider}
              hired={hired}
              instanceMode={instanceMode}
              instanceStatus={instanceStatus}
              copyRelaySim={copyRelaySim}
              showcaseAgent={deskShowcaseAgent}
              activeDesk={resolvedDesk}
              onSelectDesk={setActiveDesk}
              hireHref={hireHref}
              botConnected={botConnected}
              exchangeLiveBook={exchangeLiveBook}
            />
          </div>

          <div className="space-y-4">
            <AgentDeskView {...deskViewProps} executionOnly={false} />
            {copyDetailsMode ? (
              <CopyTradeDetailsStrip
                agent={agent}
                exchangeLabel={exchangeLabel}
                copyRelayReconcile={copyRelayReconcile}
                copyRelayLimitChain={copyRelayLimitChain}
                tradeLifecycleIntegrity={tradeLifecycleIntegrity}
                instanceStatus={instanceStatus}
                instanceLastError={instanceLastError}
                botConnected={botConnected}
                flyReachable={flyReachable}
                liveBook={exchangeLiveBook}
              />
            ) : null}
            {resolvedDesk === 'relay-sim' || (resolvedDesk === 'live' && isLiveSession) ? null : resolvedDesk === 'showcase' ? (
              isAdmin ? (
                <PublicReasoningPanel dashboard={dashboard} agentName={agent.name} slug={slug} />
              ) : null
            ) : null}
            {resolvedDesk === 'showcase' && (
              <AgentActivityFeed
                items={deskActivity.slice(0, 12)}
                title="Showcase bot feed"
              />
            )}
          </div>

          {others.length > 0 && (
            <section>
              <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Top performing agents</h2>
              <div className="mt-4 flex gap-3 overflow-x-auto pb-2">
                {others.map((a) => (
                  <Link
                    key={a.id}
                    href={`/agent-hub/${a.slug}`}
                    className="min-w-[220px] shrink-0 rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 hover:border-violet-500/40"
                  >
                    <p className="font-semibold text-white">{a.name}</p>
                    <p className={`mt-1 text-lg font-bold ${a.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {formatPercent(a.netReturnPct)}
                    </p>
                    <p className="text-xs text-zinc-500">
                      {a.winRatePct.toFixed(0)}% win · {a.followerCount.toLocaleString()} followers
                    </p>
                  </Link>
                ))}
              </div>
            </section>
          )}

          <AgentHubBottomBanner agents={allAgents ?? [agent]} hidden={resolvedDesk === 'relay-sim'} />
        </div>

        <HireSidebar
          slug={slug}
          agent={agent}
          signedIn={signedIn}
          hired={hired}
          instanceStatus={instanceStatus}
          instanceMode={instanceMode}
          exchangeProvider={exchangeProvider}
          exchangeLabel={exchangeLabel}
          exchangeConnected={exchangeConnected}
          onPauseInstance={onPauseInstance}
          onResumeInstance={onResumeInstance}
          onStartRelaySim={onStartRelaySim}
          onStopRelaySim={onStopRelaySim}
          relaySimBusy={relaySimBusy}
          copyRelaySim={copyRelaySim}
          instanceBusy={instanceBusy}
          rentalExpiresAt={rentalExpiresAt}
          accessToken={accessToken ?? adminToken}
          activeDesk={resolvedDesk}
          onRenewRental={onRenewRental}
          renewBusy={renewBusy}
          botConnected={botConnected}
          copyRelayReconcile={copyRelayReconcile}
          relayFidelity={relayFidelity}
          tradeLifecycleIntegrity={tradeLifecycleIntegrity}
          onSyncProtectionBreach={onSyncProtectionBreach}
          syncProtectionBusy={syncProtectionBusy}
          relayLastTransition={dashboard.relayLastTransition}
        />
      </div>
    </div>
  );
}
