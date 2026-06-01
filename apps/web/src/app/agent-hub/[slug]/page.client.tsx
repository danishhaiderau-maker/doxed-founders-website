'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { buildTradingAgentFollowShareText } from '@dcf/utils';
import { LiveMissionControl } from '@/components/agent-hub/live-mission-control';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import {
  fetchTradingAgent,
  fetchTradingAgentActivity,
  fetchTradingAgentDashboard,
  followTradingAgent,
  unfollowTradingAgent,
} from '@/lib/api';

export default function AgentHubDashboardClient({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const origin = useShareOrigin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTradingAgentDashboard>> | null>(null);
  const [activity, setActivity] = useState<Awaited<ReturnType<typeof fetchTradingAgentActivity>>>([]);
  const [following, setFollowing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [dash, act, meta] = await Promise.all([
        fetchTradingAgentDashboard(slug),
        fetchTradingAgentActivity(slug, 20),
        fetchTradingAgent(slug, session?.accessToken),
      ]);
      setData(dash);
      setActivity(act);
      setFollowing(Boolean(meta.following));
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
            <div className="mb-6 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={toggleFollow}
                disabled={followBusy}
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
              >
                {following ? 'Following' : 'Follow Agent'}
              </button>
              {shareFollowText && (
                <ShareOnXButton text={shareFollowText} label="Share to X" />
              )}
              <span className="self-center text-xs text-zinc-600">
                Alerts: trade opened · closed · new high · strategy change
              </span>
            </div>
            <LiveMissionControl
              agent={data.agent}
              dashboard={data.dashboard}
              activity={activity}
              botConnected={data.botConnected}
              botSource={data.botSource}
              strategyMode={data.strategyMode}
              executionPaused={data.executionPaused}
              executionReason={data.executionReason}
            />
          </>
        )}
      </div>
    </main>
  );
}
