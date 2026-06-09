'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AGENT_CATEGORY_LABELS,
  AGENT_RUN_CREDITS,
  WORKFORCE_TEMPLATES,
  formatRelativeTime,
} from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AgentCard } from '@/components/agent-card';
import { AgentBubbleMap } from '@/components/agent-hub/agent-bubble-map';
import {
  AgentBetaWarningBanner,
  AgentMarketplaceCard,
} from '@/components/agent-hub/agent-marketplace-card';
import {
  AgentMarketplaceStats,
  AgentMarketplaceTabs,
  AgentRankingTabs,
} from '@/components/agent-hub/agent-marketplace-stats';
import {
  WorkforceTemplateCard,
  workforceTemplateHref,
} from '@/components/workforce-template-card';
import {
  AgentActivityItem,
  TradingAgentSummary,
  fetchAgentActivityRecent,
  fetchAgentHub,
  fetchTradingAgentLeaderboard,
  fetchTradingAgents,
  followTradingAgent,
  FounderAgentSummary,
} from '@/lib/api';

export default function AgentHubPageClient() {
  const { data: session } = useSession();
  const signedIn = Boolean(session?.accessToken);
  const [hub, setHub] = useState<Awaited<ReturnType<typeof fetchAgentHub>> | null>(null);
  const [activity, setActivity] = useState<AgentActivityItem[]>([]);
  const [tradingAgents, setTradingAgents] = useState<TradingAgentSummary[]>([]);
  const [leaderboard, setLeaderboard] = useState<TradingAgentSummary[]>([]);
  const [kind, setKind] = useState('');
  const [founderCategory, setFounderCategory] = useState('');
  const [rankTab, setRankTab] = useState<'pnl' | 'followed' | 'builders'>('pnl');
  const [followBusy, setFollowBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [hubData, recent, trading, board] = await Promise.all([
        fetchAgentHub(founderCategory || undefined),
        fetchAgentActivityRecent(8),
        fetchTradingAgents(kind || undefined),
        fetchTradingAgentLeaderboard(),
      ]);
      setHub(hubData);
      setActivity(recent);
      setTradingAgents(trading.agents);
      setLeaderboard(board);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load Agent Hub');
    }
  }, [kind, founderCategory]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  async function handleFollow(agent: TradingAgentSummary) {
    if (!session?.accessToken) {
      setError('Sign in to follow agents');
      return;
    }
    setFollowBusy(agent.id);
    setError(null);
    try {
      await followTradingAgent(agent.id, session.accessToken);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Follow failed');
    } finally {
      setFollowBusy(null);
    }
  }

  const featured = tradingAgents.find((a) => a.slug === 'conservative-btc' && a.status !== 'PAUSED');
  const otherTrading = tradingAgents.filter((a) => a.slug !== 'conservative-btc' || a.status === 'PAUSED');
  const founderAgents = hub?.agents ?? [];

  const rankedAgents = useMemo(() => {
    const list = [...leaderboard];
    if (rankTab === 'pnl') list.sort((a, b) => b.netReturnPct - a.netReturnPct);
    else if (rankTab === 'followed') list.sort((a, b) => b.followerCount - a.followerCount);
    else list.sort((a, b) => b.tradeCount - a.tradeCount);
    return list;
  }, [leaderboard, rankTab]);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 sm:py-5 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">Agent Marketplace</h1>
            <p className="max-w-2xl text-sm text-zinc-500">
              Discover agents making money. See why. Trust who built them. Hire in under 60 seconds.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-10 px-4 py-8 sm:px-6">
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">{error}</p>
        )}

        <AgentMarketplaceStats agents={tradingAgents} />
        <AgentBetaWarningBanner />
        <AgentMarketplaceTabs active={kind} onChange={setKind} />

        {(kind === '' || kind === 'TRADING') && (
          <>
            <section className="space-y-4">
              <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Agent universe</h2>
              <AgentBubbleMap agents={tradingAgents.filter((a) => a.status !== 'PAUSED')} />
            </section>

            {featured && (
              <section className="space-y-4">
                <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/80">
                  Featured · live now
                </h2>
                <AgentMarketplaceCard
                  agent={featured}
                  featured
                  onFollow={() => handleFollow(featured)}
                  followBusy={followBusy === featured.id}
                />
              </section>
            )}

            {otherTrading.length > 0 && (
              <section className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">All trading agents</h2>
                  <AgentRankingTabs active={rankTab} onChange={setRankTab} />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  {(rankTab === 'pnl' ? rankedAgents : otherTrading).map((agent) => (
                    <AgentMarketplaceCard key={agent.id} agent={agent} />
                  ))}
                </div>
              </section>
            )}
          </>
        )}

        {(kind === '' || kind === 'RESEARCH' || kind === 'FOUNDER') && (
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5 sm:p-6">
            <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">
              {kind === 'RESEARCH' ? 'Research agents' : kind === 'FOUNDER' ? 'Content agents' : 'Founder workforce'}
            </h2>
            <p className="mt-1 text-xs text-zinc-600">Copilot orchestrates research, build, and launch inside Founder OS.</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {WORKFORCE_TEMPLATES.slice(0, kind === 'RESEARCH' ? 3 : 6).map((t) => (
                <WorkforceTemplateCard
                  key={t.key}
                  template={t}
                  href={workforceTemplateHref(t.key, signedIn)}
                />
              ))}
            </div>
            {signedIn ? (
              <Link href="/founder-den?tab=activity" className="mt-4 inline-block text-sm text-violet-300">
                Open Founder Copilot →
              </Link>
            ) : (
              <Link href="/login?callbackUrl=/agent-hub" className="mt-4 inline-block text-sm text-violet-300">
                Sign in to run agents →
              </Link>
            )}
          </section>
        )}

        {(kind === '' || kind === 'FOUNDER' || kind === 'SCOUT') && founderAgents.length > 0 && (
          <section>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setFounderCategory('')}
                className={`rounded-lg px-3 py-1.5 text-xs ${!founderCategory ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                All public
              </button>
              {(hub?.categories ?? []).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setFounderCategory(c)}
                  className={`rounded-lg px-3 py-1.5 text-xs ${founderCategory === c ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
                >
                  {AGENT_CATEGORY_LABELS[c] ?? c}
                </button>
              ))}
            </div>
            <h2 className="mt-6 text-sm font-bold uppercase tracking-widest text-zinc-500">Public agents</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {founderAgents.map((a: FounderAgentSummary) => (
                <AgentCard key={a.id} agent={a} />
              ))}
            </div>
          </section>
        )}

        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5">
          <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Live agent activity</h2>
          {activity.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">Waiting for first trade signals…</p>
          ) : (
            <ul className="mt-4 divide-y divide-zinc-800/80">
              {activity.map((item) => (
                <li key={item.id} className="py-3 first:pt-0">
                  <Link href={`/agents/${item.agentSlug}`} className="text-sm font-medium text-emerald-400">
                    {item.agentName}
                  </Link>
                  <p className="text-sm text-zinc-400">{item.outputTitle}</p>
                  <p className="text-[10px] text-zinc-600">{formatRelativeTime(item.createdAt)}</p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <p className="text-center text-xs text-zinc-600">
          Agent runs · {AGENT_RUN_CREDITS} credits · Observe free · Hire with your own exchange keys
        </p>
      </div>
    </main>
  );
}
