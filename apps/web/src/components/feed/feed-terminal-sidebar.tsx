'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import type { DiscoverUniverseProject, FeedTerminalResponse } from '@/lib/api';

export function FeedTerminalSidebar({
  terminal,
  trending,
  scoutPending,
}: {
  terminal: FeedTerminalResponse;
  trending: DiscoverUniverseProject[];
  scoutPending: number;
}) {
  const { stats, topTraders, projectChats } = terminal;

  return (
    <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Trending Now</h3>
        <div className="mt-2 space-y-1">
          {trending.slice(0, 4).map((p, i) => (
            <Link
              key={p.slug}
              href={`/project/${p.slug}`}
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-zinc-800/50"
            >
              <span className="w-4 text-xs text-zinc-600">{i + 1}</span>
              <span className="flex-1 font-medium text-zinc-200">{p.ticker}</span>
              <span className="text-xs text-orange-400">🔥 {p.activityScore}</span>
            </Link>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Project Conversations
        </h3>
        <div className="mt-2 space-y-2">
          {projectChats.length === 0 && (
            <p className="text-xs text-zinc-600">No active threads</p>
          )}
          {projectChats.map((c) => (
            <Link
              key={c.slug}
              href={`/project/${c.slug}`}
              className="block rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-2.5 hover:border-zinc-700"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-violet-300">${c.ticker}</span>
                <span className="text-[10px] text-zinc-500">{c.activeCount} active</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[11px] text-zinc-500">Latest: {c.latestMessage}</p>
            </Link>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Top Traders Today</h3>
        <div className="mt-2 space-y-1">
          {topTraders.length === 0 && (
            <p className="text-xs text-zinc-600">No closed trades yet</p>
          )}
          {topTraders.map((t, i) => (
            <Link
              key={t.userId}
              href={`/portfolio/${t.userId}`}
              className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-zinc-800/50"
            >
              <span className="w-4 text-xs font-bold text-zinc-600">{i + 1}</span>
              <span className="flex-1 text-sm text-zinc-200">{t.name}</span>
              <span className={`text-xs font-medium ${t.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {t.pnlUsd >= 0 ? '+' : ''}
                {formatUsd(t.pnlUsd, 0)}
              </span>
            </Link>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">24h Stats</h3>
        <div className="mt-3 grid grid-cols-2 gap-2 text-center">
          <StatCell label="Buys" value={String(stats.buys24h)} pct={stats.buysPct} />
          <StatCell label="Sells" value={String(stats.sells24h)} pct={stats.sellsPct} />
          <StatCell
            label="Volume"
            value={formatUsd(stats.volume24h, 0)}
            pct={stats.volumePct}
            colSpan
          />
          <StatCell label="Missed Alpha" value={String(stats.missedAlphaCount)} />
          <StatCell label="Smart Exits" value={String(stats.smartExitCount)} />
        </div>
      </div>

      <Link
        href="/predict"
        className="flex items-center justify-between rounded-xl border border-violet-500/30 bg-violet-950/20 px-4 py-3 hover:bg-violet-950/40"
      >
        <div>
          <p className="text-sm font-semibold text-violet-200">Scout Voting Queue</p>
          <p className="text-xs text-zinc-500">Review &amp; earn DDollar</p>
        </div>
        <span className="rounded-full bg-violet-500/30 px-2.5 py-1 text-sm font-bold text-violet-100">
          {scoutPending}
        </span>
      </Link>
    </aside>
  );
}

function StatCell({
  label,
  value,
  pct,
  colSpan,
}: {
  label: string;
  value: string;
  pct?: number;
  colSpan?: boolean;
}) {
  return (
    <div
      className={`rounded-lg bg-zinc-900/60 px-2 py-2 ${colSpan ? 'col-span-2' : ''}`}
    >
      <p className="text-[10px] uppercase text-zinc-600">{label}</p>
      <p className="text-sm font-bold text-white">{value}</p>
      {pct != null && pct !== 0 && (
        <p className={`text-[10px] ${pct >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
          {pct >= 0 ? '+' : ''}
          {pct}%
        </p>
      )}
    </div>
  );
}
