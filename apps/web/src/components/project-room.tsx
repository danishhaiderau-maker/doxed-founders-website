'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useState } from 'react';
import { formatUsd, extractPoolAddressFromDexUrl } from '@dcf/utils';
import {
  allocateToRaise,
  fetchProjectRoom,
  ProjectRoom,
  voteDemandPoll,
} from '@/lib/api';
import { FounderPresenceBadge } from '@/components/founder-presence';
import { ProjectMetricsGrid } from '@/components/project-card';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';

const TABS = ['Overview', 'Videos', 'Build log', 'Roadmap', 'Demand', 'Trade'] as const;

export function ProjectRoomPanel({ slug }: { slug: string }) {
  const { data: session } = useSession();
  const [room, setRoom] = useState<ProjectRoom | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>('Overview');
  const [allocAmount, setAllocAmount] = useState('500');
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRoom(await fetchProjectRoom(slug));
    } catch {
      setRoom(null);
    }
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  if (!room) return null;

  const poolAddress = room.dexscreenerUrl ? extractPoolAddressFromDexUrl(room.dexscreenerUrl) : null;

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
      setMsg('Vote recorded');
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Vote failed');
    }
  }

  return (
    <div className="mt-8 space-y-6">
      <div className="flex flex-wrap gap-2 border-b border-zinc-800 pb-3">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`rounded-lg px-3 py-1.5 text-sm transition ${
              tab === t
                ? 'bg-emerald-500/20 font-semibold text-emerald-200 ring-1 ring-emerald-500/40'
                : 'text-zinc-500 hover:text-white'
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
                <span className="text-xs text-zinc-500">Trust: {room.founder.reputationScore}</span>
              </div>
            </div>
          )}
          {room.metrics && <ProjectMetricsGrid metrics={room.metrics} />}
          {room.summary && <p className="text-zinc-400">{room.summary}</p>}
          {poolAddress && (
            <GeckoTerminalChart
              chainSlug={room.chain.slug}
              poolAddress={poolAddress}
              dexscreenerUrl={room.dexscreenerUrl ?? undefined}
            />
          )}
        </div>
      )}

      {tab === 'Videos' && (
        <ul className="space-y-3">
          {room.videos.length === 0 && (
            <p className="text-sm text-zinc-500">No public videos yet — founders verify through video, not documents.</p>
          )}
          {room.videos.map((v) => (
            <li key={v.id} className="rounded-xl border border-zinc-800 p-4">
              <a href={v.url} target="_blank" rel="noopener noreferrer" className="font-medium text-emerald-400 hover:underline">
                ▶ {v.title}
              </a>
              <p className="mt-1 text-xs text-zinc-500">{v.type.replace(/_/g, ' ')}</p>
            </li>
          ))}
        </ul>
      )}

      {tab === 'Build log' && (
        <ul className="space-y-4">
          {room.buildPosts.length === 0 && (
            <p className="text-sm text-zinc-500">No build updates yet.</p>
          )}
          {room.buildPosts.map((p) => (
            <li key={p.id} className="rounded-xl border border-zinc-800 bg-zinc-900/20 p-4">
              {p.dayNumber != null && (
                <span className="text-xs font-medium text-emerald-500">Day {p.dayNumber}</span>
              )}
              <p className="font-medium text-white">{p.headline}</p>
              <p className="mt-2 text-sm text-zinc-400">{p.body}</p>
            </li>
          ))}
        </ul>
      )}

      {tab === 'Roadmap' && (
        <ul className="space-y-2">
          {room.roadmap.length === 0 && (
            <p className="text-sm text-zinc-500">Roadmap coming soon.</p>
          )}
          {room.roadmap.map((item) => (
            <li
              key={item.id}
              className="flex items-center justify-between rounded-lg border border-zinc-800 px-4 py-3 text-sm"
            >
              <span>{item.title}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  item.status === 'DONE'
                    ? 'bg-emerald-950 text-emerald-300'
                    : item.status === 'IN_PROGRESS'
                      ? 'bg-amber-950 text-amber-300'
                      : 'bg-zinc-800 text-zinc-500'
                }`}
              >
                {item.status.replace(/_/g, ' ')}
              </span>
            </li>
          ))}
        </ul>
      )}

      {tab === 'Demand' && (
        <div className="space-y-6">
          {room.activeRaise ? (
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-5">
              <h3 className="font-semibold text-emerald-200">Simulated raise (Proof of Demand)</h3>
              <p className="mt-2 text-sm text-zinc-400">
                Goal: {formatUsd(room.activeRaise.goalUsd, 0)} · Allocated:{' '}
                {formatUsd(room.activeRaise.totalAllocated, 0)} · {room.activeRaise.allocatorCount}{' '}
                supporters
              </p>
              {session ? (
                <div className="mt-4 flex gap-2">
                  <input
                    type="number"
                    value={allocAmount}
                    onChange={(e) => setAllocAmount(e.target.value)}
                    className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    onClick={handleAllocate}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white"
                  >
                    Allocate virtual $
                  </button>
                </div>
              ) : (
                <Link href="/login" className="mt-3 inline-block text-sm text-emerald-400 hover:underline">
                  Sign in to allocate virtual capital →
                </Link>
              )}
            </div>
          ) : (
            <p className="text-sm text-zinc-500">No active simulated raise for this project.</p>
          )}
          {room.demandPolls.map((poll) => (
            <div key={poll.id} className="rounded-xl border border-zinc-800 p-5">
              <p className="font-medium">{poll.question}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(poll.voteCounts).map(([key, count]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => handleVote(poll.id, key)}
                    className="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500/50"
                  >
                    {key} ({count})
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'Trade' && (
        <Link
          href={`/paper-trading?dex=${encodeURIComponent(room.dexscreenerUrl ?? '')}`}
          className="inline-block rounded-xl bg-emerald-600 px-6 py-3 font-semibold text-white hover:bg-emerald-500"
        >
          Paper trade {room.ticker}
        </Link>
      )}
    </div>
  );
}
