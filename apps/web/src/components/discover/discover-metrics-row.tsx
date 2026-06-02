'use client';

import { formatUsd } from '@dcf/utils';
import type { DiscoverUniverseResponse } from '@/lib/api';

export function DiscoverMetricsRow({ metrics }: { metrics: DiscoverUniverseResponse['metrics'] }) {
  const cards = [
    {
      label: 'Active Projects',
      value: metrics.activeProjects.toLocaleString(),
      sub: 'In ecosystem',
      accent: 'text-emerald-400',
    },
    {
      label: 'DDollar Inflow',
      value: formatUsd(metrics.ddInflow24h, 0),
      sub: 'Last 24h',
      accent: 'text-sky-400',
    },
    {
      label: 'New Builders',
      value: String(metrics.newBuilders7d),
      sub: 'Last 7 days',
      accent: 'text-violet-400',
    },
    {
      label: 'Avg. Conviction',
      value: `${metrics.avgConviction}`,
      sub: 'Out of 100',
      accent: 'text-amber-400',
    },
    {
      label: 'Scout Reviews',
      value: String(metrics.scoutReviewsAwaiting),
      sub: 'Awaiting vote',
      accent: 'text-rose-400',
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      {cards.map((c) => (
        <div
          key={c.label}
          className="rounded-xl border border-zinc-800/80 bg-zinc-950/60 px-4 py-3 backdrop-blur-sm"
        >
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{c.label}</p>
          <p className={`mt-1 text-xl font-bold tabular-nums ${c.accent}`}>{c.value}</p>
          <p className="mt-0.5 text-[11px] text-zinc-600">{c.sub}</p>
        </div>
      ))}
    </div>
  );
}
