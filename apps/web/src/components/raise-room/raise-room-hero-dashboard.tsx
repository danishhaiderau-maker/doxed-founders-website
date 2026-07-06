'use client';

import { formatUsd } from '@dcf/utils';
import type { RaiseRoomDashboard } from '@/lib/api';

type Props = {
  stats: RaiseRoomDashboard['stats'];
  demoMode: boolean;
};

const STAT_CARDS: { key: keyof RaiseRoomDashboard['stats']; label: string; format?: 'usd' }[] = [
  { key: 'paperConvictionTotal', label: 'Paper conviction', format: 'usd' },
  { key: 'activeFounders', label: 'Active founders' },
  { key: 'activeRaises', label: 'Active raises' },
  { key: 'launchesWaiting', label: 'Near graduation' },
  { key: 'trendingCount', label: 'Trending' },
  { key: 'communityScore', label: 'Avg launch quality' },
];

export function RaiseRoomHeroDashboard({ stats, demoMode }: Props) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-amber-400">Discovery hub</p>
          <h1 className="mt-1 text-3xl font-bold text-white">Raise Room</h1>
          <p className="mt-2 max-w-2xl text-sm text-zinc-400">
            Paper conviction, community validation, and launch quality — not a token launchpad.
          </p>
        </div>
        {demoMode && (
          <span className="rounded-full border border-violet-500/40 bg-violet-950/30 px-3 py-1 text-[11px] font-semibold text-violet-200">
            Demo mode — seed from Admin → Demo if empty
          </span>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {STAT_CARDS.map(({ key, label, format }) => {
          const value = stats[key];
          const display = format === 'usd' ? formatUsd(value, 0) : String(value);
          return (
            <div
              key={key}
              className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3"
            >
              <p className="text-[11px] uppercase tracking-wider text-zinc-500">{label}</p>
              <p className="mt-1 text-xl font-semibold text-white">{display}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
