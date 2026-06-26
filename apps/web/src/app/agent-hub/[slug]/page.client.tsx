'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import {
  buildTradingAgentFollowShareText,
  type AgentShowcaseFlash,
  type CopyRelayReconcileSnapshot,
  type CopyRelaySimState,
  type CopyRelayLimitChainSnapshot,
  type TradeLifecycleIntegritySnapshot,
  type RelaySimParticipantStats,
  type TradingAgentDashboardState,
} from '@dcf/utils';
import { AgentPublicProfile } from '@/components/agent-hub/agent-public-profile';
import { AgentShowcaseFlashBanner } from '@/components/agent-hub/agent-showcase-flash';
import { SignalApiPanel } from '@/components/agent-hub/signal-api-panel';
import { AgentHubShell } from '@/components/agent-hub/agent-hub-shell';
import { useShareOrigin } from '@/components/share-on-x-button';
import {
  AGENT_HUB_POLL_BOT_MS,
  AGENT_HUB_POLL_IDLE_MS,
  AGENT_HUB_POLL_SIM_LIVE_VIEW_MS,
  AGENT_HUB_POLL_SIM_MS,
  useRelaySimLiveView,
} from '@/hooks/use-relay-sim-live-view';
import {
  fetchPublicAgentStatus,
  fetchTradingAgent,
  fetchTradingAgentDashboard,
  fetchTradingAgents,
  followTradingAgent,
  pauseMyAgentInstance,
  triggerSyncProtectionBreach,
  resumeMyAgentInstance,
  renewLiveCopyRental,
  startCopyRelaySim,
  stopCopyRelaySim,
  resetCopyRelaySim,
  unfollowTradingAgent,
  type PublicAgentStatus,
  type TradingAgentSummary,
  type TradingAgentActivityEntry,
} from '@/lib/api';

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out`)), ms),
    ),
  ]);
}

export default function AgentHubDashboardClient({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const signedIn = Boolean(session?.accessToken);
  const isAdmin = session?.user?.role === 'ADMIN';
  const origin = useShareOrigin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [instanceBusy, setInstanceBusy] = useState(false);
  const [instanceStatus, setInstanceStatus] = useState<string | null>(null);
  const [instanceMode, setInstanceMode] = useState<'copy' | 'live' | null>(null);
  const [exchangeProvider, setExchangeProvider] = useState<string | null>(null);
  const [exchangeLabel, setExchangeLabel] = useState<string | null>(null);
  const [exchangeConnected, setExchangeConnected] = useState(false);
  const [rentalExpiresAt, setRentalExpiresAt] = useState<string | null>(null);
  const [agent, setAgent] = useState<TradingAgentSummary | null>(null);
  const [allAgents, setAllAgents] = useState<TradingAgentSummary[]>([]);
  const [dashboard, setDashboard] = useState<TradingAgentDashboardState | null>(null);
  const [botConnected, setBotConnected] = useState(false);
  const [executionPaused, setExecutionPaused] = useState(false);
  const [activity, setActivity] = useState<TradingAgentActivityEntry[]>([]);
  const [showcaseLiveBook, setShowcaseLiveBook] =
    useState<TradingAgentDashboardState['liveBook'] | undefined>();
  const [exchangeLiveBook, setExchangeLiveBook] =
    useState<TradingAgentDashboardState['liveBook'] | null | undefined>();
  const [showcaseActivity, setShowcaseActivity] = useState<TradingAgentActivityEntry[]>([]);
  const [userActivity, setUserActivity] = useState<TradingAgentActivityEntry[]>([]);
  const [following, setFollowing] = useState(false);
  const [hired, setHired] = useState(false);
  const [publicStatus, setPublicStatus] = useState<{ status: PublicAgentStatus; label: string }>({
    status: 'offline',
    label: 'Agent offline',
  });
  const [viewScope, setViewScope] = useState<'showcase' | 'user'>('showcase');
  const [showcaseNote, setShowcaseNote] = useState<string | null>(null);
  const [showcaseFlash, setShowcaseFlash] = useState<AgentShowcaseFlash | null>(null);
  const [showcaseAgent, setShowcaseAgent] = useState<TradingAgentSummary | null>(null);
  const [copyRelaySim, setCopyRelaySim] = useState<CopyRelaySimState | null>(null);
  const [relaySimLiveBook, setRelaySimLiveBook] =
    useState<TradingAgentDashboardState['liveBook'] | null>(null);
  const [copyRelayReconcile, setCopyRelayReconcile] = useState<CopyRelayReconcileSnapshot | null>(
    null,
  );
  const [copyRelayLimitChain, setCopyRelayLimitChain] = useState<CopyRelayLimitChainSnapshot | null>(
    null,
  );
  const [tradeLifecycleIntegrity, setTradeLifecycleIntegrity] =
    useState<TradeLifecycleIntegritySnapshot | null>(null);
  const [relaySimParticipantStats, setRelaySimParticipantStats] =
    useState<RelaySimParticipantStats | null>(null);
  const [relayFidelity, setRelayFidelity] = useState<
    import('@/components/agent-hub/agent-relay-fidelity-panel').RelayFidelitySnapshot | null
  >(null);
  const [relaySimBusy, setRelaySimBusy] = useState(false);
  const [renewBusy, setRenewBusy] = useState(false);
  const [syncProtectionBusy, setSyncProtectionBusy] = useState(false);
  const [liveLoading, setLiveLoading] = useState(true);
  const liveViewUserId = session?.user?.id ?? session?.user?.email ?? undefined;
  const { liveViewEnabled, setLiveViewEnabled } = useRelaySimLiveView(liveViewUserId);

  const applyAgentMeta = useCallback((meta: TradingAgentSummary) => {
    setAgent(meta);
    setFollowing(Boolean(meta.following));
    setHired(Boolean(meta.hired));
    setInstanceStatus(meta.instanceStatus ?? null);
    setInstanceMode(meta.instanceMode ?? null);
    setExchangeProvider(meta.exchangeProvider ?? null);
    setExchangeLabel(meta.exchangeLabel ?? null);
    setExchangeConnected(Boolean(meta.exchangeConnected));
    setRentalExpiresAt(meta.rentalExpiresAt ?? null);
  }, []);

  const loadLive = useCallback(async (opts?: { showLoading?: boolean }) => {
    const token = session?.accessToken;
    if (opts?.showLoading) setLiveLoading(true);
    try {
      const results = await Promise.allSettled([
        withTimeout(fetchTradingAgentDashboard(slug, token), 15000, 'Dashboard'),
        fetchPublicAgentStatus(),
      ]);

      const dashR = results[0];
      const statusR = results[1];

      if (dashR.status === 'fulfilled') {
        setAgent(dashR.value.agent);
        setDashboard(dashR.value.dashboard);
        setBotConnected(Boolean(dashR.value.botConnected));
        setExecutionPaused(Boolean(dashR.value.executionPaused));
        setViewScope(dashR.value.viewScope ?? dashR.value.agent.viewScope ?? 'showcase');
        setShowcaseNote(dashR.value.showcaseNote ?? null);
        setShowcaseFlash((prev) => dashR.value.showcaseFlash ?? prev);
        setShowcaseAgent(dashR.value.showcaseAgent ?? dashR.value.agent);
        setShowcaseLiveBook(dashR.value.showcaseLiveBook ?? dashR.value.dashboard.liveBook);
        setExchangeLiveBook(dashR.value.exchangeLiveBook ?? null);
        setShowcaseActivity(dashR.value.showcaseActivity ?? []);
        setUserActivity(dashR.value.userActivity ?? []);
        setActivity(dashR.value.showcaseActivity ?? []);
        setCopyRelaySim(dashR.value.copyRelaySim ?? null);
        setRelaySimLiveBook(dashR.value.relaySimLiveBook ?? null);
        setCopyRelayReconcile(dashR.value.copyRelayReconcile ?? null);
        setCopyRelayLimitChain(dashR.value.copyRelayLimitChain ?? null);
        setTradeLifecycleIntegrity(dashR.value.tradeLifecycleIntegrity ?? null);
        setRelaySimParticipantStats(dashR.value.relaySimParticipantStats ?? null);
        setRelayFidelity(dashR.value.relayFidelity ?? null);
        if (dashR.value.copyRelaySim?.active) setInstanceStatus('PAUSED');
        setRentalExpiresAt(dashR.value.agent.rentalExpiresAt ?? null);
        setError(null);
      } else {
        setError('Live bot slow — showing cached stats. Refresh in a moment.');
      }

      if (statusR.status === 'fulfilled') setPublicStatus(statusR.value);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load live data');
    } finally {
      if (opts?.showLoading) setLiveLoading(false);
    }
  }, [slug, session?.accessToken]);

  const load = useCallback(async () => {
    try {
      const token = session?.accessToken;
      const meta = await fetchTradingAgent(slug, token);
      applyAgentMeta(meta);
      setLoading(false);

      void Promise.allSettled([loadLive({ showLoading: true }), fetchTradingAgents('TRADING')]).then(([, agentsR]) => {
        if (agentsR.status === 'fulfilled') setAllAgents(agentsR.value.agents);
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agent');
      setLoading(false);
      setLiveLoading(false);
    }
  }, [slug, session?.accessToken, applyAgentMeta, loadLive]);

  useEffect(() => {
    load();
    // Background sync only — no loading banner (avoids layout pulse every tick).
    let pollMs = AGENT_HUB_POLL_IDLE_MS;
    if (copyRelaySim?.active) {
      pollMs = liveViewEnabled ? AGENT_HUB_POLL_SIM_LIVE_VIEW_MS : AGENT_HUB_POLL_SIM_MS;
    } else if (botConnected) {
      pollMs = AGENT_HUB_POLL_BOT_MS;
    }
    const interval = setInterval(() => void loadLive(), pollMs);
    return () => clearInterval(interval);
  }, [load, loadLive, copyRelaySim?.active, botConnected, liveViewEnabled]);

  async function toggleFollow() {
    if (!session?.accessToken || !agent) return;
    setFollowBusy(true);
    try {
      if (following) {
        await unfollowTradingAgent(agent.id, session.accessToken);
        setFollowing(false);
      } else {
        await followTradingAgent(agent.id, session.accessToken);
        setFollowing(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Follow failed');
    } finally {
      setFollowBusy(false);
    }
  }

  async function handlePauseInstance() {
    if (!session?.accessToken) return;
    setInstanceBusy(true);
    try {
      await pauseMyAgentInstance(slug, session.accessToken);
      setInstanceStatus('PAUSED');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pause failed');
    } finally {
      setInstanceBusy(false);
    }
  }

  async function handleSyncProtectionBreach(opts?: { flatten?: boolean }) {
    if (!session?.accessToken) return;
    setSyncProtectionBusy(true);
    try {
      const res = await triggerSyncProtectionBreach(slug, session.accessToken, opts);
      setInstanceStatus('PAUSED');
      if (res.flattened && res.flattened > 0) {
        setError(`Sync protection: flattened ${res.flattened} open position(s) and paused relay.`);
      }
      await loadLive();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sync protection failed');
    } finally {
      setSyncProtectionBusy(false);
    }
  }

  async function handleResumeInstance() {
    if (!session?.accessToken) return;
    if (rentalExpiresAt && new Date(rentalExpiresAt).getTime() <= Date.now()) {
      setError('Live copy rental expired — renew your subscription first.');
      return;
    }
    setInstanceBusy(true);
    try {
      await resumeMyAgentInstance(slug, session.accessToken);
      setInstanceStatus('ACTIVE');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Resume failed');
    } finally {
      setInstanceBusy(false);
    }
  }

  async function handleRenewRental() {
    if (!session?.accessToken) return;
    setRenewBusy(true);
    try {
      const res = await renewLiveCopyRental(slug, session.accessToken);
      setRentalExpiresAt(res.rentalExpiresAt);
      await loadLive();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Renewal failed');
    } finally {
      setRenewBusy(false);
    }
  }

  async function handleStartRelaySim() {
    if (!session?.accessToken) return;
    setRelaySimBusy(true);
    try {
      const res = await startCopyRelaySim(slug, session.accessToken);
      setCopyRelaySim(res.copyRelaySim);
      setInstanceStatus('PAUSED');
      await loadLive();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Relay sim start failed');
    } finally {
      setRelaySimBusy(false);
    }
  }

  async function handleStopRelaySim() {
    if (!session?.accessToken) return;
    setRelaySimBusy(true);
    try {
      const res = await stopCopyRelaySim(slug, session.accessToken);
      setCopyRelaySim(res.copyRelaySim);
      await loadLive();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Relay sim stop failed');
    } finally {
      setRelaySimBusy(false);
    }
  }

  async function handleResetRelaySim() {
    if (!session?.accessToken) return;
    if (!window.confirm('Reset relay sim to $500? Clears paper ledger for this sim session.')) return;
    setRelaySimBusy(true);
    try {
      const res = await resetCopyRelaySim(slug, session.accessToken);
      setCopyRelaySim(res.copyRelaySim);
      setRelaySimLiveBook(null);
      await loadLive();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Relay sim refresh failed');
    } finally {
      setRelaySimBusy(false);
    }
  }

  const shareFollowText =
    agent &&
    buildTradingAgentFollowShareText({
      agentName: agent.name,
      netReturnPct: agent.netReturnPct,
      winRatePct: agent.winRatePct,
      hubUrl: `${origin}/agent-hub/${slug}`,
    });
  void shareFollowText;

  const dashboardView: TradingAgentDashboardState = dashboard ?? {
    currentPrice: agent?.equityUsd ?? 0,
    regime: 'RANGE',
    support: 0,
    resistance: 0,
    distanceToResistancePct: 0,
    distanceToSupportPct: 0,
    currentPosition: agent?.currentPosition ?? 'NONE',
    currentAction: agent?.currentAction ?? 'WAITING',
    aiDecision: 'NO_TRADE',
    aiWinProbability: 0,
    currentEdge: 0,
    requiredEdge: 0,
    noTradeReason: 'Loading live data…',
    currentThinking: { market: 'Loading…', support: 0, resistance: 0, distanceToResistancePct: 0, distanceToSupportPct: 0, conclusion: '' },
    transparency: { currentEdge: 0, requiredEdge: 0, currentState: 'Loading', reason: '' },
    openTrades: [],
    pendingOrders: [],
    recentTrades: [],
    marketStructure: '',
    aiReasoning: 'Connecting to admin showcase bot…',
    riskStatus: 'NORMAL',
    fundingStatus: '',
    dataSource: '',
    wsHealth: '',
    dataQuality: '',
    pnl: { daily: 0, total: 0 },
    leverage: agent?.leverage ?? 100,
    liveBook: {
      activeSignals: [],
      positions: [],
      pendingOrders: [],
      expiredOrders: [],
      trades: [],
    },
  };

  return (
    <AgentHubShell>
      {slug === 'conservative-btc' && (
        <AgentShowcaseFlashBanner flash={showcaseFlash} />
      )}
      {error && (
        <p className="mx-4 mt-4 rounded-lg border border-amber-500/30 bg-amber-950/30 px-4 py-2 text-sm text-amber-200 sm:mx-6">
          {error}
        </p>
      )}
      {liveLoading && agent && (
        <p className="mx-4 mt-2 text-xs text-zinc-500 sm:mx-6">Syncing live bot data…</p>
      )}

      {loading && !agent ? (
        <p className="p-8 text-zinc-500">Loading agent profile…</p>
      ) : !agent ? (
        <div className="p-8 text-center">
          <p className="text-zinc-500">Agent not found.</p>
          <Link href="/agent-hub" className="mt-2 inline-block text-violet-400">
            ← Marketplace
          </Link>
        </div>
      ) : agent.status === 'PAUSED' ? (
        <div className="m-6 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-8 text-center">
          <h1 className="text-xl font-bold">{agent.name}</h1>
          <p className="mt-2 text-zinc-500">This agent is coming soon.</p>
        </div>
      ) : (
        <AgentPublicProfile
          slug={slug}
          agent={agent}
          dashboard={dashboardView}
          activity={activity}
          allAgents={allAgents}
          following={following}
          hired={hired}
          signedIn={signedIn}
          isAdmin={isAdmin}
          accessToken={session?.accessToken}
          adminToken={session?.accessToken}
          botConnected={botConnected}
          executionPaused={executionPaused}
          publicStatus={publicStatus.status}
          instanceStatus={instanceStatus}
          instanceMode={instanceMode}
          exchangeProvider={exchangeProvider ?? agent.exchangeProvider}
          exchangeLabel={exchangeLabel ?? agent.exchangeLabel}
          exchangeConnected={exchangeConnected || Boolean(agent.exchangeConnected)}
          viewScope={viewScope}
          showcaseNote={showcaseNote}
          showcaseAgent={showcaseAgent ?? agent}
          showcaseLiveBook={showcaseLiveBook}
          exchangeLiveBook={exchangeLiveBook}
          relaySimLiveBook={relaySimLiveBook}
          copyRelaySim={copyRelaySim}
          copyRelayReconcile={copyRelayReconcile}
          copyRelayLimitChain={copyRelayLimitChain}
          tradeLifecycleIntegrity={tradeLifecycleIntegrity}
          relaySimParticipantStats={relaySimParticipantStats}
          relayFidelity={relayFidelity}
          showcaseActivity={showcaseActivity}
          userActivity={userActivity}
          onFollow={toggleFollow}
          followBusy={followBusy}
          onPauseInstance={handlePauseInstance}
          onResumeInstance={handleResumeInstance}
          onStartRelaySim={handleStartRelaySim}
          onStopRelaySim={handleStopRelaySim}
          onResetRelaySim={handleResetRelaySim}
          relaySimBusy={relaySimBusy}
          onAdminRefresh={() => void loadLive({ showLoading: true })}
          instanceBusy={instanceBusy}
          rentalExpiresAt={rentalExpiresAt ?? agent.rentalExpiresAt}
          onRenewRental={handleRenewRental}
          renewBusy={renewBusy}
          onSyncProtectionBreach={handleSyncProtectionBreach}
          syncProtectionBusy={syncProtectionBusy}
          relaySimLiveView={liveViewEnabled}
          onRelaySimLiveViewChange={setLiveViewEnabled}
        />
      )}
      {agent && slug === 'conservative-btc' && isAdmin && (
        <SignalApiPanel slug={slug} token={session?.accessToken} signedIn={signedIn} />
      )}
    </AgentHubShell>
  );
}
