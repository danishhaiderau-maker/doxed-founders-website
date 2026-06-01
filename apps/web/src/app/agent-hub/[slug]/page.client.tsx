'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { buildTradingAgentFollowShareText } from '@dcf/utils';
import { AgentPublicStatusBanner } from '@/components/agent-hub/agent-public-status';
import { LiveMissionControl } from '@/components/agent-hub/live-mission-control';
import { ResearchBotDetailDashboard } from '@/components/agent-hub/research-bot-detail-dashboard';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import {
  fetchPublicAgentStatus,
  fetchTradingAgent,
  fetchTradingAgentActivity,
  fetchTradingAgentDashboard,
  followTradingAgent,
  unfollowTradingAgent,
  type PublicAgentStatus,
} from '@/lib/api';

export default function AgentHubDashboardClient({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'ADMIN';
  const origin = useShareOrigin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTradingAgentDashboard>> | null>(null);
  const [activity, setActivity] = useState<Awaited<ReturnType<typeof fetchTradingAgentActivity>>>([]);
  const [following, setFollowing] = useState(false);
  const [hired, setHired] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [viewMode, setViewMode] = useState<'mission' | 'research'>('research');
  const [publicStatus, setPublicStatus] = useState<{ status: PublicAgentStatus; label: string }>({
    status: 'offline',
    label: 'Agent offline',
  });

  const load = useCallback(async () => {
    try {
      const [dash, act, meta, statusRes] = await Promise.all([
        fetchTradingAgentDashboard(slug, session?.accessToken),
        fetchTradingAgentActivity(slug, 20),
        fetchTradingAgent(slug, session?.accessToken),
        fetchPublicAgentStatus(),
      ]);
      setData(dash);
      setActivity(act);
      setFollowing(Boolean(meta.following));
      setHired(Boolean(meta.hired));
      setPublicStatus(statusRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    load();
    const ms = autoRefresh ? 60_000 : 15_000;
    const interval = setInterval(load, ms);
    return () => clearInterval(interval);
  }, [load, autoRefresh]);

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

  const shareFollowText =
    data &&
    buildTradingAgentFollowShareText({
      agentName: data.agent.name,
      netReturnPct: data.agent.netReturnPct,
      winRatePct: data.agent.winRatePct,
      hubUrl: `${origin}/agent-hub/${slug}`,
    });

  const adminInfraDetails = isAdmin && data && (
    <>
      <strong className="text-amber-200">Admin runtime</strong>
      <ul className="mt-2 space-y-1">
        <li>Bridge: {data.botConnected ? 'live' : 'demo fallback'}</li>
        <li>Source: {data.botSource ?? '—'}</li>
        <li>Strategy: {data.strategyMode ?? '—'}</li>
        {data.executionPaused && <li>Paused: {data.executionReason ?? 'yes'}</li>}
        <li>
          <Link href="/admin/control" className="text-amber-300 underline">
            Open Admin Control →
          </Link>
        </li>
      </ul>
    </>
  );

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <Link href="/agent-hub" className="mt-1 block text-xs text-violet-400 hover:text-violet-300">
              ← Agent Hub
            </Link>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        {error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        {loading || !data ? (
          <p className="text-zinc-500">Loading live dashboard…</p>
        ) : data.agent.status === 'PAUSED' ? (
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-8 text-center">
            <h1 className="text-xl font-bold">{data.agent.name}</h1>
            <p className="mt-2 text-zinc-500">This agent is coming soon.</p>
            <Link href="/agent-hub" className="mt-4 inline-block text-violet-400">
              Back to Agent Hub
            </Link>
          </div>
        ) : (
          <>
            <div className="mb-6">
              <AgentPublicStatusBanner
                status={publicStatus.status}
                label={publicStatus.label}
              />
            </div>

            <div className="mb-4 rounded-xl border border-violet-500/20 bg-violet-950/10 px-4 py-3 text-sm text-violet-100/90">
              Public showcase — everyone can watch, nobody trades on the admin account.{' '}
              {hired ? (
                <Link href={`/agent-hub/${slug}/my-dashboard`} className="font-semibold text-violet-300 underline">
                  Open your private dashboard →
                </Link>
              ) : session ? (
                <Link href={`/agent-hub/${slug}/hire`} className="font-semibold text-violet-300 underline">
                  Hire for isolated execution →
                </Link>
              ) : (
                <Link href={`/login?callbackUrl=/agent-hub/${slug}/hire`} className="font-semibold text-violet-300 underline">
                  Sign in to hire →
                </Link>
              )}
            </div>

            <div className="mb-6 flex flex-wrap gap-2">
              {hired ? (
                <Link
                  href={`/agent-hub/${slug}/my-dashboard`}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                >
                  My private dashboard
                </Link>
              ) : (
                <Link
                  href={session ? `/agent-hub/${slug}/hire` : `/login?callbackUrl=/agent-hub/${slug}/hire`}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                >
                  Hire agent · {data.agent.costDdollarDay.toLocaleString()} DDollar/day
                </Link>
              )}
              <button
                type="button"
                onClick={toggleFollow}
                disabled={followBusy}
                className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-4 py-2 text-sm font-semibold text-violet-100 hover:bg-violet-900/40 disabled:opacity-50"
              >
                {following ? 'Following alerts' : 'Follow for alerts'}
              </button>
              {shareFollowText && <ShareOnXButton text={shareFollowText} label="Share to X" />}
              {isAdmin && (
                <div className="flex rounded-lg border border-zinc-800 p-0.5">
                  <button
                    type="button"
                    onClick={() => setViewMode('research')}
                    className={`rounded-md px-3 py-1.5 text-xs ${viewMode === 'research' ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
                  >
                    Research detail
                  </button>
                  <button
                    type="button"
                    onClick={() => setViewMode('mission')}
                    className={`rounded-md px-3 py-1.5 text-xs ${viewMode === 'mission' ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
                  >
                    Mission control
                  </button>
                </div>
              )}
              <span className="self-center text-xs text-zinc-600">
                Hire = your exchange + isolated runtime · Follow = alerts only
              </span>
            </div>

            {isAdmin && viewMode === 'research' && data.rawBotState && data.botConnected ? (
              <ResearchBotDetailDashboard
                raw={data.rawBotState as Record<string, unknown>}
                updatedAt={data.updatedAt}
                onRefresh={() => void load()}
                autoRefresh={autoRefresh}
                onAutoRefreshChange={setAutoRefresh}
              />
            ) : (
              <LiveMissionControl
                agent={data.agent}
                dashboard={data.dashboard}
                activity={activity}
                botConnected={data.botConnected}
                botSource={data.botSource}
                strategyMode={data.strategyMode}
                executionPaused={data.executionPaused}
                executionReason={data.executionReason}
                publicStatus={publicStatus.status}
                publicLabel={publicStatus.label}
                isAdmin={isAdmin}
                adminDetails={adminInfraDetails}
              />
            )}

            {isAdmin && viewMode === 'research' && !data.rawBotState && (
              <p className="mt-4 text-sm text-zinc-400">
                Admin research detail requires live bot bridge. Mission control shows public showcase data.
              </p>
            )}
          </>
        )}
      </div>
    </main>
  );
}
