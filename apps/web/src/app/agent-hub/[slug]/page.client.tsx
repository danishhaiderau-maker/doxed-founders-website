'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { buildTradingAgentFollowShareText, type TradingAgentDashboardState } from '@dcf/utils';
import { AgentPublicProfile } from '@/components/agent-hub/agent-public-profile';
import { AgentHubShell } from '@/components/agent-hub/agent-hub-shell';
import { useShareOrigin } from '@/components/share-on-x-button';
import {
  fetchPublicAgentStatus,
  fetchTradingAgent,
  fetchTradingAgentActivity,
  fetchTradingAgentDashboard,
  fetchTradingAgents,
  followTradingAgent,
  paperTrackAgent,
  pauseMyAgentInstance,
  resumeMyAgentInstance,
  unfollowTradingAgent,
  type PublicAgentStatus,
  type TradingAgentSummary,
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
  const [paperBusy, setPaperBusy] = useState(false);
  const [instanceBusy, setInstanceBusy] = useState(false);
  const [instanceStatus, setInstanceStatus] = useState<string | null>(null);
  const [instanceMode, setInstanceMode] = useState<'copy' | 'live' | null>(null);
  const [agent, setAgent] = useState<TradingAgentSummary | null>(null);
  const [allAgents, setAllAgents] = useState<TradingAgentSummary[]>([]);
  const [dashboard, setDashboard] = useState<TradingAgentDashboardState | null>(null);
  const [botConnected, setBotConnected] = useState(false);
  const [executionPaused, setExecutionPaused] = useState(false);
  const [activity, setActivity] = useState<Awaited<ReturnType<typeof fetchTradingAgentActivity>>>([]);
  const [following, setFollowing] = useState(false);
  const [hired, setHired] = useState(false);
  const [publicStatus, setPublicStatus] = useState<{ status: PublicAgentStatus; label: string }>({
    status: 'offline',
    label: 'Agent offline',
  });
  const [viewScope, setViewScope] = useState<'showcase' | 'user'>('showcase');
  const [showcaseNote, setShowcaseNote] = useState<string | null>(null);
  const [liveLoading, setLiveLoading] = useState(true);

  const applyAgentMeta = useCallback((meta: TradingAgentSummary) => {
    setAgent(meta);
    setFollowing(Boolean(meta.following));
    setHired(Boolean(meta.hired));
    setInstanceStatus(meta.instanceStatus ?? null);
    setInstanceMode(meta.instanceMode ?? null);
  }, []);

  const loadLive = useCallback(async () => {
    const token = session?.accessToken;
    setLiveLoading(true);
    try {
      const results = await Promise.allSettled([
        withTimeout(fetchTradingAgentDashboard(slug, token), 10000, 'Dashboard'),
        fetchTradingAgentActivity(slug, 20, token),
        fetchPublicAgentStatus(),
      ]);

      const dashR = results[0];
      const actR = results[1];
      const statusR = results[2];

      if (dashR.status === 'fulfilled') {
        setAgent(dashR.value.agent);
        setDashboard(dashR.value.dashboard);
        setBotConnected(Boolean(dashR.value.botConnected));
        setExecutionPaused(Boolean(dashR.value.executionPaused));
        setViewScope(dashR.value.viewScope ?? dashR.value.agent.viewScope ?? 'showcase');
        setShowcaseNote(dashR.value.showcaseNote ?? null);
        setError(null);
      } else {
        setError('Live bot slow — showing cached stats. Refresh in a moment.');
      }

      if (actR.status === 'fulfilled') setActivity(actR.value);
      if (statusR.status === 'fulfilled') setPublicStatus(statusR.value);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load live data');
    } finally {
      setLiveLoading(false);
    }
  }, [slug, session?.accessToken]);

  const load = useCallback(async () => {
    try {
      const token = session?.accessToken;
      const meta = await fetchTradingAgent(slug, token);
      applyAgentMeta(meta);
      setLoading(false);

      void Promise.allSettled([loadLive(), fetchTradingAgents('TRADING')]).then(([, agentsR]) => {
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
    const interval = setInterval(loadLive, 20_000);
    return () => clearInterval(interval);
  }, [load, loadLive]);

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

  async function handleCopyTrack() {
    if (!session?.accessToken) {
      setError('Sign in to paper-track this agent');
      return;
    }
    setPaperBusy(true);
    try {
      await paperTrackAgent(slug, session.accessToken);
      setHired(true);
      setInstanceStatus('ACTIVE');
      setInstanceMode('copy');
      setViewScope('user');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Copy track failed');
    } finally {
      setPaperBusy(false);
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

  async function handleResumeInstance() {
    if (!session?.accessToken) return;
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
          adminToken={session?.accessToken}
          botConnected={botConnected}
          executionPaused={executionPaused}
          publicStatus={publicStatus.status}
          instanceStatus={instanceStatus}
          instanceMode={instanceMode}
          viewScope={viewScope}
          showcaseNote={showcaseNote}
          onFollow={toggleFollow}
          followBusy={followBusy}
          onCopyAllocate={handleCopyTrack}
          onPauseInstance={handlePauseInstance}
          onResumeInstance={handleResumeInstance}
          onAdminRefresh={load}
          instanceBusy={instanceBusy}
          copyBusy={paperBusy}
        />
      )}
    </AgentHubShell>
  );
}
