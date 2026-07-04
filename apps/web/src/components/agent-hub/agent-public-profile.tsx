'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  formatPercent,
  formatUsd,
  buildTradingAgentActionShareText,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
  type RelaySimParticipantStats,
  type TradingAgentDashboardState,
  type TradingAgentSessionStats,
} from '@dcf/utils';
import { AgentRentalCountdown, LiveCopyRentalBadge } from '@/components/agent-hub/agent-rental-countdown';
import { AgentAdminShowcaseControl } from '@/components/agent-hub/agent-admin-showcase-control';
import { AgentHubBottomBanner } from '@/components/agent-hub/agent-hub-bottom-banner';
import { AgentPerformanceChart } from '@/components/agent-hub/agent-performance-chart';
import { AgentDeskView } from '@/components/agent-hub/agent-dual-desk-panels';
import { EMPTY_LIVE_BOOK } from '@/components/agent-hub/agent-transparency-tables';
import { AgentAnalyzerPanel } from '@/components/agent-hub/agent-analyzer-panel';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { CopyTradeDetailsStrip, CopyTradeHub } from '@/components/agent-hub/copy-trade-hub';
import type { RelayFidelitySnapshot } from '@/components/agent-hub/agent-relay-fidelity-panel';
import { ExchangeHirePanel } from '@/components/agent-hub/exchange-hire-panel';
import { AgentActivityFeed } from '@/components/agent-hub/live-mission-control';
import { ShareOnXButton } from '@/components/share-on-x-button';
import { mergeDeskActivity, liveBookToActivity, filterLiveExchangeActivity } from '@/lib/livebook-activity';

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

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

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
                {verdict.decision} · {verdict.direction} · {verdict.winProbability}% confidence
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
        <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
          Confidence: {verdict?.winProbability ?? dashboard.aiWinProbability ?? 0}%
        </span>
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

function LiveRelayReasoningPanel({
  agent,
  exchangeLabel,
  liveBook,
}: {
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  liveBook?: TradingAgentDashboardState['liveBook'] | null;
}) {
  const openPos = liveBook?.positions?.[0];
  const pending = liveBook?.pendingOrders?.length ?? 0;
  const lastSignal = liveBook?.activeSignals?.[0];
  const exchange = exchangeLabel ?? 'Bitfinex';

  return (
    <section className="rounded-2xl border border-emerald-500/25 bg-gradient-to-br from-emerald-950/20 to-zinc-950/50 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-300">
        Your {exchange} relay status
      </h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-black/20 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Open position</p>
          <p className="mt-1 text-sm font-semibold text-white">
            {openPos ? `${openPos.side} · ${openPos.qty.toFixed(4)} BTC` : agent.openPositionSide ?? 'None'}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/20 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Pending limits</p>
          <p className="mt-1 text-sm font-semibold text-white">{pending}</p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/20 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Session P&amp;L</p>
          <p className={`mt-1 text-sm font-semibold ${(agent.sessionPnlUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {formatUsd(agent.sessionPnlUsd ?? 0, 2)}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-black/20 px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-widest text-zinc-500">Exchange balance</p>
          <p className="mt-1 text-sm font-semibold text-white">
            {formatUsd(agent.exchangeBalanceUsd ?? agent.balanceUsd ?? 0, 2)}
          </p>
        </div>
      </div>
      {lastSignal && (
        <p className="mt-4 text-sm text-zinc-300">
          Last relay signal: <strong>{lastSignal.direction}</strong> · {lastSignal.confidence}% conf ·{' '}
          {lastSignal.outcome}
        </p>
      )}
      {agent.walletStatusHint && (
        <p className="mt-2 text-xs text-amber-200/80">{agent.walletStatusHint}</p>
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
            Paper book only — no rental, no real USDT. Connect API once to validate sync with the
            showcase bot before going live.
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

function AiHistoryDetail({ activity }: { activity: TradingAgentActivityEntry[] }) {
  const aiItems = (activity ?? [])
    .filter((a) => a.type === 'AI_APPROVED' || a.type === 'AI_REJECTED')
    .slice(0, 5);

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-widest text-violet-300">
          Last 5 AI decisions in detail
        </h3>
        <span className="text-[10px] text-zinc-600">showcase pipeline</span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-500">
        How the AI thinks: decision, direction, edge vs required, regime band, and the AI comment that
        drove the call.
      </p>
      {aiItems.length === 0 ? (
        <p className="mt-4 text-xs text-zinc-600">No AI decisions yet this session.</p>
      ) : (
        <ul className="mt-4 space-y-2">
          {aiItems.map((a, i) => {
            const approved = a.type === 'AI_APPROVED';
            return (
              <li
                key={a.id ?? i}
                className="rounded-xl border border-zinc-800/70 bg-black/25 px-3 py-2.5 text-xs"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                        approved
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-zinc-700 text-zinc-300'
                      }`}
                    >
                      {approved ? 'APPROVE' : 'REJECT'}
                    </span>
                    <span className="text-zinc-200">{a.outcome ?? '—'}</span>
                  </span>
                  <span className="text-[10px] text-zinc-600">{timeAgo(a.createdAt)}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-zinc-500">
                  <span>
                    edge{' '}
                    <span className="text-zinc-300">
                      {a.edgeScore ?? '—'}/{a.edgeRequired ?? '—'}
                    </span>
                  </span>
                  <span>
                    band/regime <span className="text-zinc-300">{a.marketRegime ?? '—'}</span>
                  </span>
                  {a.profitPct != null && (
                    <span>
                      outcome{' '}
                      <span className={a.profitPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {a.profitPct >= 0 ? '+' : ''}
                        {a.profitPct.toFixed(2)}%
                      </span>
                    </span>
                  )}
                </div>
                {a.reason && (
                  <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-zinc-400">
                    {a.reason}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function StateIntegrityHeader({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const si = dashboard.stateIntegrity;
  if (!si) {
    return (
      <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-950/20 px-4 py-3 text-xs text-amber-200/90">
        <strong>Live bot snapshot unavailable.</strong> Showing cached/database numbers.
        Start the home bot and refresh, or click Start in the command center.
      </div>
    );
  }
  const uptimeH = dashboard.botUptimeHours ?? 0;
  const windowH = dashboard.dataWindowHours ?? uptimeH;
  const ageSec = si.snapshot_age_sec ?? 0;
  const source = dashboard.snapshotSource ?? 'live_bot';
  const wsOk = si.ws_connected && si.rest_healthy;
  // REST_FALLBACK (ws down) is NOT "offline" — if REST is healthy and the snapshot is
  // fresh, the bot is up and serving data via REST. Only a stale snapshot / unhealthy
  // REST means the bot is truly unreachable. Without this, a bot in long REST_FALLBACK
  // (ws disconnected for hours) showed a red "Bot offline" dot while still trading fine.
  const restUp = !wsOk && si.rest_healthy && ageSec < 30;
  const dot = wsOk ? 'bg-emerald-400' : restUp ? 'bg-amber-400' : 'bg-rose-400';
  return (
    <div className="mb-4 rounded-xl border border-zinc-700/60 bg-zinc-900/50 px-4 py-3 text-xs text-zinc-300">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5 font-semibold text-zinc-100">
          <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
          {wsOk ? 'Live bot connected' : restUp ? 'Bot up · REST stale' : 'Bot offline'}
        </span>
        <span>Data from last {windowH > 0 ? `${windowH.toFixed(1)}h` : '—'}</span>
        <span>Running for {uptimeH > 0 ? `${uptimeH.toFixed(1)}h` : '—'}</span>
        <span>source: <span className="text-zinc-100">{source}</span></span>
        <span>seq: <span className="text-zinc-100">{si.snapshot_seq}</span></span>
        <span>age: <span className="text-zinc-100">{Math.round(ageSec)}s</span></span>
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
  executionPaused,
  publicStatus,
  instanceStatus,
  instanceMode,
  exchangeProvider,
  exchangeLabel,
  exchangeConnected,
  viewScope = 'showcase',
  showcaseNote,
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
  executionPaused?: boolean;
  publicStatus: PublicAgentStatus;
  instanceStatus?: string | null;
  instanceMode?: 'copy' | 'live' | null;
  exchangeProvider?: string | null;
  exchangeLabel?: string | null;
  exchangeConnected?: boolean;
  viewScope?: 'showcase' | 'user';
  showcaseNote?: string | null;
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
  const [activeDesk, setActiveDesk] = useState<AgentDeskId>(() => readStoredDesk(slug) ?? 'showcase');

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

  const isUserSession = viewScope === 'user' || isCopySession || isLiveSession;
  const isLive = !isUserSession && botConnected && !executionPaused && publicStatus === 'online';
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
      : publicStatus === 'updating'
        ? { label: 'Updating', className: 'bg-amber-500/20 text-amber-200 ring-1 ring-amber-500/40' }
        : { label: 'Offline', className: 'bg-zinc-800 text-zinc-400' };
  const statusLabel = isLiveSession
    ? instanceStatus === 'PAUSED'
      ? `Relay stopped · ${exchangeLabel ?? 'Exchange'} protected`
      : relaySimActive
        ? `Relay simulation on ${exchangeLabel ?? 'Bitfinex'}`
        : `Live copy on ${exchangeLabel ?? 'your exchange'}`
    : isCopySession
      ? 'Legacy DDollar paper — connect Bitfinex for real copy'
    : isLive
      ? 'Admin showcase (observe only)'
      : publicStatus === 'offline' && !botConnected
        ? 'Showcase stopped'
        : executionPaused
          ? 'Showcase paused'
          : 'Showcase offline';
  const statusColor = isUserSession
    ? 'text-violet-300'
    : isLive
      ? 'text-emerald-400'
      : executionPaused && publicStatus !== 'offline'
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

  const resolvedDesk: AgentDeskId =
    activeDesk === 'relay-sim' && !relaySimDeskAvailable
      ? 'live'
      : activeDesk === 'relay-sim' && relaySimDeskAvailable
        ? 'relay-sim'
        : activeDesk;

  const deskHeroBadge =
    resolvedDesk === 'showcase'
      ? isLive
        ? { label: 'Showcase live', className: 'bg-violet-500/20 text-violet-200 ring-1 ring-violet-500/40' }
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
      {showcaseNote && !(slug === 'conservative-btc' && resolvedDesk === 'showcase') && (
        <p className="mb-4 rounded-xl border border-violet-500/25 bg-violet-950/20 px-4 py-3 text-sm text-violet-100/90">
          {showcaseNote}
        </p>
      )}

      <StateIntegrityHeader dashboard={dashboard} />

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
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-xl border border-zinc-600 bg-transparent px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-violet-500/50"
              >
                Observe showcase
              </button>
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

          {slug === 'conservative-btc' && (
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
                botConnected={botConnected}
              />
            ) : null}
            {resolvedDesk === 'relay-sim' ? null : resolvedDesk === 'live' && isLiveSession ? (
              <LiveRelayReasoningPanel
                agent={agent}
                exchangeLabel={exchangeLabel}
                liveBook={exchangeLiveBook}
              />
            ) : resolvedDesk === 'showcase' ? (
              isAdmin ? (
                <PublicReasoningPanel dashboard={dashboard} agentName={agent.name} slug={slug} />
              ) : null
            ) : null}
            <AiHistoryDetail activity={deskActivity} />
            {resolvedDesk !== 'relay-sim' && (
              <div className="grid gap-6 lg:grid-cols-2">
                <AgentActivityFeed
                  items={deskActivity.slice(0, 12)}
                  title={
                    resolvedDesk === 'live' && isLiveSession
                      ? `Your ${exchangeLabel ?? 'Bitfinex'} feed`
                      : 'Showcase bot feed'
                  }
                />
                <AgentPerformanceChart
                  agentReturnPct={
                    resolvedDesk === 'live' && isLiveSession
                      ? agent.netReturnPct
                      : (deskShowcaseAgent.netReturnPct ?? agent.netReturnPct)
                  }
                  label={
                    resolvedDesk === 'live' && isLiveSession ? 'Your session' : 'Showcase bot'
                  }
                />
              </div>
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
        />
      </div>
    </div>
  );
}
