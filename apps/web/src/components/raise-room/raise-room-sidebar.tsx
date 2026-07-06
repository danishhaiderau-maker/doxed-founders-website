'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import type { RaiseRoomDashboard } from '@/lib/api';

type Props = {
  dashboard: RaiseRoomDashboard;
};

function LeaderList<T>({
  title,
  rows,
  render,
}: {
  title: string;
  rows: T[];
  render: (row: T, i: number) => ReactNode;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
      <h3 className="text-sm font-semibold text-white">{title}</h3>
      <ul className="mt-3 space-y-2">{rows.map((row, i) => render(row, i))}</ul>
    </div>
  );
}

export function RaiseRoomActivityFeed({ items }: { items: RaiseRoomDashboard['activityFeed'] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
      <h3 className="text-sm font-semibold text-white">Live activity</h3>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 8).map((e) => (
          <li key={e.id} className="text-xs text-zinc-400">
            <span className="text-zinc-500">{new Date(e.at).toLocaleDateString()}</span>
            {' · '}
            {e.project ? (
              <Link href={`/project/${e.project.slug}`} className="text-amber-300 hover:underline">
                {e.project.name}
              </Link>
            ) : (
              'Platform'
            )}
            {' — '}
            {e.title}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RaiseRoomSidebar({ dashboard }: Props) {
  const { leaderboards, scoutLeaderboardWeek, rewardTiers, communityAllocation, marketplaceNeeds } =
    dashboard;

  return (
    <aside className="space-y-4">
      <RaiseRoomActivityFeed items={dashboard.activityFeed} />

      <LeaderList
        title="Top founders"
        rows={leaderboards.topFounders}
        render={(f, i) => (
          <li key={f.slug} className="flex items-center justify-between text-xs">
            <Link href={`/founder/${f.slug}`} className="text-zinc-200 hover:text-amber-300">
              #{i + 1} {f.name}
            </Link>
            <span className="text-zinc-500">{f.score} rep</span>
          </li>
        )}
      />

      <LeaderList
        title="Scouts this week"
        rows={scoutLeaderboardWeek}
        render={(s, i) => (
          <li key={`${s.handle ?? s.name}-${i}`} className="flex justify-between text-xs text-zinc-300">
            <span>
              #{s.rank} {s.name ?? s.handle ?? 'Scout'}
            </span>
            <span className="text-zinc-500">{s.weeklyPoints} pts</span>
          </li>
        )}
      />

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
        <h3 className="text-sm font-semibold text-white">Reward tiers</h3>
        <ul className="mt-3 space-y-2 text-xs text-zinc-400">
          {rewardTiers.map((t) => (
            <li key={t.tier}>
              <span className="font-semibold text-zinc-200">{t.tier}</span> — {t.communityPercent}%
              community · {t.feePercent}% fee · LQ ≥ {t.minScore}
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
        <h3 className="text-sm font-semibold text-white">Community allocation mix</h3>
        <ul className="mt-3 space-y-1 text-xs text-zinc-400">
          <li>Paper contributors — {communityAllocation.paperContributors}%</li>
          <li>Reviewers — {communityAllocation.reviewers}%</li>
          <li>Scouts — {communityAllocation.scouts}%</li>
          <li>Builders — {communityAllocation.builders}%</li>
        </ul>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
        <h3 className="text-sm font-semibold text-white">Marketplace needs</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          {marketplaceNeeds.map((n) => (
            <Link
              key={n.slug}
              href={n.href}
              className="rounded-full border border-zinc-700 px-2.5 py-1 text-[11px] text-zinc-300 hover:border-amber-500/40 hover:text-amber-200"
            >
              {n.label}
            </Link>
          ))}
        </div>
      </div>
    </aside>
  );
}
