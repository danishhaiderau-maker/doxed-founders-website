'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import type { DiscoverUniverseResponse } from '@/lib/api';

function ProjectMini({
  project,
  rank,
  showScore = true,
}: {
  project: { slug: string; name: string; ticker: string; activityScore?: number; followerCount?: number };
  rank?: number;
  showScore?: boolean;
}) {
  return (
    <Link
      href={`/project/${project.slug}`}
      className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition hover:bg-zinc-800/50"
    >
      {rank != null && (
        <span className="w-4 text-xs font-bold text-zinc-600">{rank}</span>
      )}
      <span className="min-w-0 flex-1 truncate text-sm text-zinc-200">{project.ticker}</span>
      {showScore && project.activityScore != null && (
        <span className="text-xs font-semibold text-emerald-400">{project.activityScore}</span>
      )}
      {!showScore && project.followerCount != null && (
        <span className="text-xs text-zinc-500">{project.followerCount.toLocaleString()}</span>
      )}
    </Link>
  );
}

export function DiscoverSidebar({ sidebar, scoutCount }: { sidebar: DiscoverUniverseResponse['sidebar']; scoutCount: number }) {
  return (
    <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Trending Now</h3>
        <div className="mt-2 space-y-0.5">
          {sidebar.trending.map((p, i) => (
            <ProjectMini key={p.slug} project={p} rank={i + 1} />
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">DDollar Flow</h3>
        <p className="mt-3 text-[10px] font-medium uppercase text-emerald-500/80">Top Inflow</p>
        <div className="mt-1 space-y-1">
          {sidebar.topInflow.length === 0 && (
            <p className="text-xs text-zinc-600">No inflow yet</p>
          )}
          {sidebar.topInflow.map((p) => (
            <Link
              key={p.slug}
              href={`/project/${p.slug}`}
              className="flex justify-between text-sm hover:text-emerald-300"
            >
              <span className="text-zinc-300">{p.ticker}</span>
              <span className="font-medium text-emerald-400">+{formatUsd(p.ddInflow24h, 0)}</span>
            </Link>
          ))}
        </div>
        {sidebar.topOutflow.length > 0 && (
          <>
            <p className="mt-3 text-[10px] font-medium uppercase text-red-500/80">Top Outflow</p>
            <div className="mt-1 space-y-1">
              {sidebar.topOutflow.map((p) => (
                <Link
                  key={p.slug}
                  href={`/project/${p.slug}`}
                  className="flex justify-between text-sm hover:text-red-300"
                >
                  <span className="text-zinc-300">{p.ticker}</span>
                  <span className="font-medium text-red-400">
                    -{formatUsd(p.ddOutflow ?? 0, 0)}
                  </span>
                </Link>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Most Followed</h3>
        <div className="mt-2 space-y-0.5">
          {sidebar.mostFollowed.map((p) => (
            <ProjectMini key={p.slug} project={p} showScore={false} />
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Active Conversations</h3>
        <div className="mt-2 space-y-2">
          {sidebar.activeConversations.length === 0 && (
            <p className="text-xs text-zinc-600">No recent updates</p>
          )}
          {sidebar.activeConversations.map((p) => (
            <Link
              key={p.slug}
              href={`/project/${p.slug}`}
              className="block rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-2 transition hover:border-zinc-700"
            >
              <p className="text-xs font-semibold text-white">{p.ticker}</p>
              <p className="mt-0.5 line-clamp-2 text-[11px] text-zinc-500">
                {p.lastActivityPreview?.text ?? p.summary ?? 'Building in public'}
              </p>
            </Link>
          ))}
        </div>
      </div>

      <Link
        href="/predict"
        className="flex items-center justify-between rounded-xl border border-violet-500/30 bg-violet-950/20 px-4 py-3 transition hover:bg-violet-950/40"
      >
        <span className="text-sm font-semibold text-violet-200">Scout Voting</span>
        <span className="rounded-full bg-violet-500/30 px-2 py-0.5 text-xs font-bold text-violet-100">
          {scoutCount}
        </span>
      </Link>
    </aside>
  );
}
