'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { buildTradingAgentFollowShareText } from '@dcf/utils';
import { AgentPublicProfile } from '@/components/agent-hub/agent-public-profile';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { useShareOrigin } from '@/components/share-on-x-button';
import {
  fetchPublicAgentStatus,
  fetchShowcaseDefaultSettings,
  fetchTradingAgent,
  fetchTradingAgentActivity,
  fetchTradingAgentDashboard,
  followTradingAgent,
  paperTrackAgent,
  pauseMyAgentInstance,
  resumeMyAgentInstance,
  unfollowTradingAgent,
  type PublicAgentStatus,
} from '@/lib/api';

export default function AgentHubDashboardClient({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const signedIn = Boolean(session?.accessToken);
  const origin = useShareOrigin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [paperBusy, setPaperBusy] = useState(false);
  const [instanceBusy, setInstanceBusy] = useState(false);
  const [instanceStatus, setInstanceStatus] = useState<string | null>(null);
  const [instanceMode, setInstanceMode] = useState<'copy' | 'live' | null>(null);
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTradingAgentDashboard>> | null>(null);
  const [activity, setActivity] = useState<Awaited<ReturnType<typeof fetchTradingAgentActivity>>>([]);
  const [defaultSettings, setDefaultSettings] = useState<string | null>(null);
  const [following, setFollowing] = useState(false);
  const [hired, setHired] = useState(false);
  const [publicStatus, setPublicStatus] = useState<{ status: PublicAgentStatus; label: string }>({
    status: 'offline',
    label: 'Agent offline',
  });

  const load = useCallback(async () => {
    try {
      const [dash, act, meta, statusRes, defaults] = await Promise.all([
        fetchTradingAgentDashboard(slug, session?.accessToken),
        fetchTradingAgentActivity(slug, 20),
        fetchTradingAgent(slug, session?.accessToken),
        fetchPublicAgentStatus(),
        fetchShowcaseDefaultSettings(),
      ]);
      setData(dash);
      setActivity(act);
      setFollowing(Boolean(meta.following));
      setHired(Boolean(meta.hired));
      setInstanceStatus(meta.instanceStatus ?? null);
      setInstanceMode(meta.instanceMode ?? null);
      setPublicStatus(statusRes);
      setDefaultSettings(defaults.message);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15_000);
    return () => clearInterval(interval);
  }, [load]);

  async function toggleFollow() {
    if (!session?.accessToken || !data) {
      setError('Sign in to follow this agent');
      return;
    }
    setFollowBusy(true);
    setError(null);
    try {
      if (following) {
        await unfollowTradingAgent(data.agent.id, session.accessToken);
        setFollowing(false);
      } else {
        await followTradingAgent(data.agent.id, session.accessToken);
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
      setError('Sign in to start copy tracking');
      return;
    }
    setPaperBusy(true);
    setError(null);
    try {
      await paperTrackAgent(slug, session.accessToken);
      setHired(true);
      setInstanceStatus('ACTIVE');
      setInstanceMode('copy');
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Copy track failed');
    } finally {
      setPaperBusy(false);
    }
  }

  async function handlePauseInstance() {
    if (!session?.accessToken) return;
    setInstanceBusy(true);
    setError(null);
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
    setError(null);
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
    data &&
    buildTradingAgentFollowShareText({
      agentName: data.agent.name,
      netReturnPct: data.agent.netReturnPct,
      winRatePct: data.agent.winRatePct,
      hubUrl: `${origin}/agent-hub/${slug}`,
    });

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <Link href="/agent-hub" className="mt-1 block text-xs text-violet-400 hover:text-violet-300">
              ← Agent Marketplace
            </Link>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        {loading || !data ? (
          <p className="text-zinc-500">Loading agent profile…</p>
        ) : data.agent.status === 'PAUSED' ? (
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-8 text-center">
            <h1 className="text-xl font-bold">{data.agent.name}</h1>
            <p className="mt-2 text-zinc-500">This agent is coming soon.</p>
          </div>
        ) : (
          <AgentPublicProfile
            slug={slug}
            agent={data.agent}
            dashboard={data.dashboard}
            activity={activity}
            following={following}
            hired={hired}
            signedIn={signedIn}
            botConnected={data.botConnected}
            executionPaused={data.executionPaused}
            publicStatus={publicStatus.status}
            shareText={shareFollowText ?? undefined}
            defaultSettings={defaultSettings}
            instanceStatus={instanceStatus}
            instanceMode={instanceMode}
            onFollow={toggleFollow}
            followBusy={followBusy}
            onCopyAllocate={handleCopyTrack}
            onPauseInstance={handlePauseInstance}
            onResumeInstance={handleResumeInstance}
            instanceBusy={instanceBusy}
            copyBusy={paperBusy}
          />
        )}
      </div>
    </main>
  );
}
