'use client';

import Link from 'next/link';
import { useState } from 'react';
import {
  formatPercent,
  formatUsd,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentMarketplaceStats } from '@/components/agent-hub/agent-marketplace-stats';
import { AgentRentalCountdown, LiveCopyRentalBadge } from '@/components/agent-hub/agent-rental-countdown';
import { AgentAdminShowcaseControl } from '@/components/agent-hub/agent-admin-showcase-control';
import { AgentHubBottomBanner } from '@/components/agent-hub/agent-hub-bottom-banner';
import { AgentPerformanceChart } from '@/components/agent-hub/agent-performance-chart';
import { AgentDeskView } from '@/components/agent-hub/agent-dual-desk-panels';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';
import { CopyTradeDetailsStrip, CopyTradeHub } from '@/components/agent-hub/copy-trade-hub';
import type { RelayFidelitySnapshot } from '@/components/agent-hub/agent-relay-fidelity-panel';
import { ExchangeHirePanel } from '@/components/agent-hub/exchange-hire-panel';
import { AgentActivityFeed } from '@/components/agent-hub/live-mission-control';
import { mergeDeskActivity, liveBookToActivity, filterLiveExchangeActivity } from '@/lib/livebook-activity';
import type {
  PublicAgentStatus,
  TradingAgentActivityEntry,
  TradingAgentSummary,
} from '@/lib/api';

const TABS = ['Overview', 'Performance', 'Trade Journey', 'Reasoning', 'Activity', 'Followers'] as const;
const HIGHLIGHT_TABS = ['Performance', 'Trade Journey', 'Reasoning', 'Activity'] as const;
type Tab = (typeof TABS)[number];

const STRATEGY_TAGS = ['BTC Markets', 'Low Risk', 'Trend Following', 'Long Bias'];

function MetricPill({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5 text-center">
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${accent ?? 'text-white'}`}>{value}</p>
    </div>
  );
}

function PublicReasoningPanel({ dashboard }: { dashboard: TradingAgentDashboardState }) {
  const t = dashboard.currentThinking;
  const reasoning =
    dashboard.aiReasoning?.trim() ||
    t.conclusion?.trim() ||
    dashboard.noTradeReason?.trim() ||
    null;
  const bias =
    dashboard.regime && dashboard.regime !== 'RANGE'
      ? dashboard.regime
      : t.market || 'Assessing';
  return (
    <section className="rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/30 to-zinc-950/50 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-violet-300">Latest reasoning</h2>
      {reasoning ? (
        <p className="mt-4 text-sm italic leading-relaxed text-zinc-200">&ldquo;{reasoning}&rdquo;</p>
      ) : (
        <p className="mt-4 text-sm text-zinc-500">Waiting for the bot&apos;s next market assessment…</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border border-emerald-500/40 bg-emerald-950/40 px-3 py-1 font-semibold text-emerald-200">
          Bias: {bias}
        </span>
        <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-violet-200">
          Confidence: {dashboard.aiWinProbability || 0}%
        </span>
        <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
          Decision: {dashboard.aiDecision}
        </span>
        <span className="rounded-full border border-zinc-600 px-3 py-1 text-zinc-400">
          Edge {dashboard.currentEdge}/{dashboard.requiredEdge}
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
}) {
  const isLiveHired = hired && instanceMode === 'live';

  return (
    <aside className="space-y-4 xl:sticky xl:top-28">
      {isLiveHired && rentalExpiresAt && (
        <AgentRentalCountdown expiresAt={rentalExpiresAt} />
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
        rentalExpiresAt={rentalExpiresAt}
        onStopRelay={onPauseInstance}
        onStartRelay={onResumeInstance}
        relayBusy={instanceBusy}
        copyRelaySim={copyRelaySim}
        onStartRelaySim={onStartRelaySim}
        onStopRelaySim={onStopRelaySim}
        relaySimBusy={relaySimBusy}
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

export function AgentPublicProfile({
  slug,
  agent,
  dashboard,
  activity,
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
}) {
  const [tab, setTab] = useState<Tab>('Overview');
  const isCopySession = hired && instanceMode === 'copy';
  const isLiveSession = hired && instanceMode === 'live';
  const relaySimActive = Boolean(copyRelaySim?.active);
  const relaySimDeskAvailable = isLiveSession && exchangeProvider === 'bitfinex';
  const [activeDesk, setActiveDesk] = useState<AgentDeskId>('live');
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
  const showcaseLive = showcaseLiveBook ?? dashboard.liveBook;
  const dualDeskMode = isLiveSession ? 'live' : isCopySession ? 'copy' : 'showcase';
  const showcaseAct = mergeDeskActivity(
    showcaseActivityProp ?? activity,
    liveBookToActivity(showcaseLive, 'showcase-ui'),
  );
  const simAct = mergeDeskActivity(
    userActivityProp ?? activity,
    liveBookToActivity(relaySimLiveBook, 'relay-sim-ui'),
  );
  const userAct = isLiveSession
    ? filterLiveExchangeActivity(
        liveBookToActivity(exchangeLiveBook, 'user-ui', 'positions-only'),
      )
    : mergeDeskActivity(
        userActivityProp ?? activity,
        liveBookToActivity(exchangeLiveBook, 'user-ui'),
      );
  const deskActivity =
    activeDesk === 'relay-sim' ? simAct : activeDesk === 'live' ? userAct : showcaseAct;

  const resolvedDesk: AgentDeskId =
    activeDesk === 'relay-sim' && relaySimDeskAvailable
      ? 'relay-sim'
      : activeDesk;

  const hireHref = signedIn
    ? `/agent-hub/${slug}/hire?exchange=bitfinex`
    : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=bitfinex`)}`;

  const copyDetailsMode: 'live' | null =
    activeDesk === 'live' && isLiveSession ? 'live' : null;

  const deskViewProps = {
    activeDesk: resolvedDesk,
    mode: dualDeskMode,
    exchangeLabel,
    userAgent: agent,
    showcaseAgent: deskShowcaseAgent,
    exchangeLiveBook,
    showcaseLiveBook: showcaseLive,
    relaySimLiveBook,
    copyRelaySim,
    copyRelayReconcile,
    copyRelayLimitChain,
    tradeLifecycleIntegrity,
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
  } as const;

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8">
      {showcaseNote && (
        <p className="mb-4 rounded-xl border border-violet-500/25 bg-violet-950/20 px-4 py-3 text-sm text-violet-100/90">
          {showcaseNote}
        </p>
      )}

      <AgentMarketplaceStats agents={allAgents ?? [deskShowcaseAgent]} builderCount={14} />

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
                      className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${heroBadge.className}`}
                    >
                      {heroBadge.label}
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
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <MetricPill label="Win rate" value={`${heroAgent.winRatePct.toFixed(0)}%`} />
                  <MetricPill
                    label="30D return"
                    value={formatPercent(heroAgent.netReturnPct)}
                    accent={heroAgent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}
                  />
                  <MetricPill label="Max drawdown" value="6.2%" />
                  <MetricPill label="Total trades" value={String(heroAgent.tradeCount)} />
                  <MetricPill label="Followers" value={heroAgent.followerCount.toLocaleString()} />
                  <MetricPill
                    label="Session P&L"
                    value={`${(heroAgent.sessionPnlUsd ?? heroAgent.equityUsd - (heroAgent.startingBalance || 500)) >= 0 ? '+' : ''}${formatUsd(heroAgent.sessionPnlUsd ?? heroAgent.equityUsd - (heroAgent.startingBalance || 500), 0)}`}
                    accent={
                      (heroAgent.sessionPnlUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }
                  />
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
                onClick={() => {
                  setTab('Overview');
                  setActiveDesk('showcase');
                }}
                className="inline-flex items-center gap-2 rounded-xl border border-zinc-600 bg-transparent px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-violet-500/50"
              >
                Observe showcase
              </button>
              {isLiveSession && rentalExpiresAt && (
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

          <div className="sticky top-0 z-20 -mx-4 border-b border-zinc-800 bg-[#050508]/95 px-4 backdrop-blur-md sm:-mx-6 sm:px-6">
            <div className="flex gap-1 overflow-x-auto">
              {TABS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTab(t)}
                  className={`shrink-0 rounded-t-lg px-4 py-2.5 text-sm font-medium transition ${
                    tab === t
                      ? 'border-b-2 border-violet-500 bg-zinc-900/60 text-white'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  {t}
                  {t === 'Followers' ? ` (${(agent.followerCount / 1000).toFixed(1)}K)` : ''}
                  {t === 'Activity' && deskActivity.length ? ` (${deskActivity.length})` : ''}
                </button>
              ))}
            </div>
            <div className="flex gap-2 overflow-x-auto py-3">
              {HIGHLIGHT_TABS.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTab(t)}
                  className={`shrink-0 rounded-full px-3.5 py-1.5 text-xs font-semibold transition ${
                    tab === t
                      ? 'bg-violet-600 text-white shadow-md shadow-violet-900/40'
                      : tab === 'Overview'
                        ? 'border border-zinc-700 bg-zinc-900/80 text-zinc-300 hover:border-violet-500/50 hover:text-white'
                        : 'border border-zinc-800 text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {tab === 'Overview' && (
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
                activeDesk={activeDesk}
                onSelectDesk={setActiveDesk}
                onStartRelaySim={onStartRelaySim}
                onStopRelaySim={onStopRelaySim}
                relaySimBusy={relaySimBusy}
                hireHref={hireHref}
              />
            </div>
          )}

          {tab === 'Overview' && (
            <div className="space-y-4">
              <AgentDeskView {...deskViewProps} />
              {copyDetailsMode ? (
                <CopyTradeDetailsStrip
                  agent={agent}
                  exchangeLabel={exchangeLabel}
                  copyRelayReconcile={copyRelayReconcile}
                  copyRelaySim={copyRelaySim}
                  copyRelayLimitChain={copyRelayLimitChain}
                  tradeLifecycleIntegrity={tradeLifecycleIntegrity}
                  relayFidelity={relayFidelity}
                  instanceStatus={instanceStatus}
                  botConnected={botConnected}
                  mode="live"
                />
              ) : null}
              {activeDesk === 'live' && isLiveSession ? (
                <LiveRelayReasoningPanel
                  agent={agent}
                  exchangeLabel={exchangeLabel}
                  liveBook={exchangeLiveBook}
                />
              ) : (
                <PublicReasoningPanel dashboard={dashboard} />
              )}
              <div className="grid gap-6 lg:grid-cols-2">
                <AgentActivityFeed
                  items={deskActivity.slice(0, 12)}
                  title={
                    activeDesk === 'relay-sim'
                      ? 'Relay sim feed'
                      : activeDesk === 'live' && isLiveSession
                        ? `Your ${exchangeLabel ?? 'Bitfinex'} feed`
                        : 'Showcase bot feed'
                  }
                />
                <AgentPerformanceChart
                  agentReturnPct={
                    activeDesk === 'live' && isLiveSession
                      ? agent.netReturnPct
                      : (deskShowcaseAgent.netReturnPct ?? agent.netReturnPct)
                  }
                  label={
                    activeDesk === 'live' && isLiveSession ? 'Your session' : deskShowcaseAgent.name
                  }
                />
              </div>
            </div>
          )}
          {tab === 'Performance' && (
            <AgentPerformanceChart
              agentReturnPct={
                activeDesk === 'live' && isLiveSession
                  ? agent.netReturnPct
                  : (deskShowcaseAgent.netReturnPct ?? agent.netReturnPct)
              }
              label={
                activeDesk === 'live' && isLiveSession ? 'Your session' : deskShowcaseAgent.name
              }
            />
          )}
          {tab === 'Trade Journey' && (
            <AgentDeskView {...deskViewProps} />
          )}
          {tab === 'Reasoning' &&
            (activeDesk === 'live' && isLiveSession ? (
              <LiveRelayReasoningPanel
                agent={agent}
                exchangeLabel={exchangeLabel}
                liveBook={exchangeLiveBook}
              />
            ) : (
              <PublicReasoningPanel dashboard={dashboard} />
            ))}
          {tab === 'Activity' && (
            <div className="space-y-6">
              <AgentActivityFeed
                items={deskActivity}
                title={
                  activeDesk === 'relay-sim'
                    ? 'Relay sim activity'
                    : activeDesk === 'live' && isLiveSession
                      ? `Your ${exchangeLabel ?? 'Bitfinex'} activity`
                      : 'Conservative BTC showcase activity'
                }
              />
              <AgentDeskView {...deskViewProps} />
            </div>
          )}
          {tab === 'Followers' && (
            <p className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-sm text-zinc-400">
              {agent.followerCount.toLocaleString()} founders follow this agent for trade alerts and bias updates.
            </p>
          )}

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

          <AgentHubBottomBanner agents={allAgents ?? [agent]} />
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
        />
      </div>
    </div>
  );
}
