'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { formatUsd, extractPoolAddressFromDexUrl, LIFECYCLE_STAGES } from '@dcf/utils';
import {
  allocateToRaise,
  fetchProjectRoom,
  followProject,
  ProjectRoom,
  unfollowProject,
  voteDemandPoll,
} from '@/lib/api';
import { FounderPresenceBadge } from '@/components/founder-presence';
import { ProjectLifecycleBar } from '@/components/lifecycle-bar';
import { StartupGenomePanel } from '@/components/startup-genome';
import { ProjectMetricsGrid } from '@/components/project-card';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';

const TABS = ['Overview', 'Community', 'Demand', 'Build log', 'Trade'] as const;

function stageLabel(key: string) {
  return LIFECYCLE_STAGES.find((s) => s.key === key)?.label ?? key.replace(/_/g, ' ');
}

export function ProjectRoomPanel({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const [room, setRoom] = useState<ProjectRoom | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>('Overview');
  const [allocAmount, setAllocAmount] = useState('500');
  const [communityChannel, setCommunityChannel] = useState('GENERAL');
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRoom(await fetchProjectRoom(slug, session?.accessToken));
    } catch {
      setRoom(null);
    }
  }, [slug, session?.accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  if (!room) return null;

  const poolAddress = room.dexscreenerUrl ? extractPoolAddressFromDexUrl(room.dexscreenerUrl) : null;
  const demandPct = room.activeRaise
    ? Math.min(100, Math.round((room.activeRaise.totalAllocated / room.activeRaise.goalUsd) * 100))
    : 0;

  async function handleAllocate() {
    if (!session?.accessToken || !room?.activeRaise) return;
    try {
      await allocateToRaise(room.activeRaise.id, Number(allocAmount), session.accessToken);
      setMsg(`Allocated ${formatUsd(Number(allocAmount))} virtual capital`);
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Allocation failed');
    }
  }

  async function handleVote(pollId: string, key: string) {
    if (!session?.accessToken) return;
    try {
      await voteDemandPoll(pollId, key, session.accessToken);
      setMsg('Vote recorded — you shaped this startup');
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Vote failed');
    }
  }

  async function toggleFollow() {
    if (!session?.accessToken || !room) return;
    try {
      if (room.isFollowing) {
        await unfollowProject(room.id, session.accessToken);
      } else {
        await followProject(room.id, session.accessToken);
      }
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Follow failed');
    }
  }

  const channelThreads = room.communityThreads.filter((t) => t.channel === communityChannel);

  return (
    <div className="mt-8 space-y-6">
      <ProjectLifecycleBar currentStage={room.lifecycleStage} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Stage', stageLabel(room.lifecycleStage)],
          ['Followers', room.followerCount.toLocaleString()],
          ['Founder score', String(room.founderScore)],
          ['Launch ready', `${room.launchReadiness}%`],
          ['Demand', formatUsd(room.demandAnalytics.totalDemand, 0)],
          ['Build streak', `${room.buildStreakDays} days`],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-zinc-800 bg-zinc-900/30 px-4 py-3">
            <p className="text-[10px] uppercase text-zinc-500">{label}</p>
            <p className="mt-1 font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <StartupGenomePanel genome={room.genome} />
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
          <p className="text-xs uppercase tracking-widest text-zinc-500">Trader demand analytics</p>
          <dl className="mt-4 space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-zinc-500">Interested users</dt><dd>{room.demandAnalytics.interestedUsers.toLocaleString()}</dd></div>
            <div className="flex justify-between"><dt className="text-zinc-500">Avg commitment</dt><dd>{formatUsd(room.demandAnalytics.averageCommitment, 0)}</dd></div>
            <div className="flex justify-between"><dt className="text-zinc-500">Largest commitment</dt><dd>{formatUsd(room.demandAnalytics.largestCommitment, 0)}</dd></div>
            <div className="flex justify-between"><dt className="text-zinc-500">Demand rank</dt><dd>{room.demandAnalytics.demandRank ? `#${room.demandAnalytics.demandRank} this week` : '—'}</dd></div>
          </dl>
          {session && (
            <button type="button" onClick={toggleFollow} className="mt-4 rounded-lg border border-emerald-500/40 px-4 py-2 text-sm text-emerald-200 hover:bg-emerald-950/30">
              {room.isFollowing ? 'Unfollow project' : 'Follow project'}
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 border-b border-zinc-800 pb-3">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`rounded-lg px-3 py-1.5 text-sm transition ${
              tab === t ? 'bg-emerald-500/20 font-semibold text-emerald-200 ring-1 ring-emerald-500/40' : 'text-zinc-500 hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {msg && <p className="text-sm text-emerald-300">{msg}</p>}

      {tab === 'Overview' && (
        <div className="space-y-6">
          {room.founder && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-5">
              <div className="flex flex-wrap items-center gap-3">
                <Link href={`/founder/${room.founder.slug}`} className="text-lg font-semibold hover:text-emerald-400">
                  {room.founder.name}
                </Link>
                <FounderPresenceBadge level={room.founder.presenceLevel} />
              </div>
            </div>
          )}
          {room.isLiveToken && room.metrics && <ProjectMetricsGrid metrics={room.metrics} />}
          {room.summary && <p className="text-zinc-400">{room.summary}</p>}
          {poolAddress && (
            <GeckoTerminalChart chainSlug={room.chain.slug} poolAddress={poolAddress} dexscreenerUrl={room.dexscreenerUrl ?? undefined} />
          )}
        </div>
      )}

      {tab === 'Community' && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {room.communityChannels.map((ch) => (
              <button
                key={ch}
                type="button"
                onClick={() => setCommunityChannel(ch)}
                className={`rounded-lg px-3 py-1 text-xs ${communityChannel === ch ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
              >
                {ch.replace(/_/g, ' ')}
              </button>
            ))}
          </div>
          {channelThreads.length === 0 ? (
            <p className="text-sm text-zinc-500">No threads yet in this channel. Founders can post announcements; everyone can discuss.</p>
          ) : (
            <ul className="space-y-3">
              {channelThreads.map((t) => (
                <li key={t.id} className={`rounded-xl border p-4 ${t.pinned ? 'border-emerald-500/30 bg-emerald-950/10' : 'border-zinc-800'}`}>
                  {t.pinned && <span className="text-[10px] font-semibold uppercase text-emerald-400">Pinned</span>}
                  <p className="font-medium text-white">{t.title}</p>
                  <p className="mt-2 text-sm text-zinc-400">{t.body}</p>
                  <p className="mt-2 text-xs text-zinc-600">{t.commentCount} replies</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === 'Demand' && (
        <div className="space-y-6">
          {room.activeRaise ? (
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-5">
              <h3 className="font-semibold text-emerald-200">Simulated raise</h3>
              <p className="mt-2 text-sm text-zinc-400">
                Goal {formatUsd(room.activeRaise.goalUsd, 0)} · {room.activeRaise.tokenAllocation ?? '—'} allocation ·{' '}
                {room.activeRaise.durationDays} days
              </p>
              <div className="mt-4">
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>Current demand</span>
                  <span>{formatUsd(room.activeRaise.totalAllocated, 0)} ({demandPct}%)</span>
                </div>
                <div className="mt-1 h-3 overflow-hidden rounded-full bg-zinc-800">
                  <div className="h-full bg-emerald-500 transition-all" style={{ width: `${demandPct}%` }} />
                </div>
                <p className="mt-2 text-xs text-zinc-500">
                  {room.activeRaise.allocatorCount} investors · Conviction {room.activeRaise.convictionScore}%
                </p>
              </div>
              {session ? (
                <div className="mt-4 flex gap-2">
                  <input type="number" value={allocAmount} onChange={(e) => setAllocAmount(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" />
                  <button type="button" onClick={handleAllocate} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">
                    Allocate paper $
                  </button>
                </div>
              ) : (
                <Link href="/login" className="mt-3 inline-block text-sm text-emerald-400 hover:underline">
                  Sign in — $10,000 virtual cash to allocate
                </Link>
              )}
            </div>
          ) : (
            <p className="text-sm text-zinc-500">No active simulated raise. Founder can launch one from Founder Den.</p>
          )}
          {room.demandPolls.map((poll) => (
            <div key={poll.id} className="rounded-xl border border-zinc-800 p-5">
              <p className="font-medium">{poll.question}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(poll.voteCounts).map(([key, count]) => (
                  <button key={key} type="button" onClick={() => handleVote(poll.id, key)} className="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500/50">
                    {key} ({count})
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'Build log' && (
        <ul className="space-y-4">
          {room.buildPosts.length === 0 && <p className="text-sm text-zinc-500">No build updates yet.</p>}
          {room.buildPosts.map((p) => (
            <li key={p.id} className="rounded-xl border border-zinc-800 bg-zinc-900/20 p-4">
              {p.dayNumber != null && <span className="text-xs font-medium text-emerald-500">Day {p.dayNumber}</span>}
              <p className="font-medium text-white">{p.headline}</p>
              <p className="mt-2 text-sm text-zinc-400">{p.body}</p>
            </li>
          ))}
        </ul>
      )}

      {tab === 'Trade' && (
        <Link href={`/paper-trading?dex=${encodeURIComponent(room.dexscreenerUrl ?? '')}`} className="inline-block rounded-xl bg-emerald-600 px-6 py-3 font-semibold text-white hover:bg-emerald-500">
          Paper trade {room.ticker}
        </Link>
      )}
    </div>
  );
}
