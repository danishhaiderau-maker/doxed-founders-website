'use client';

import Link from 'next/link';
import { DISCOVER_UNIVERSE_COLORS, formatUsd, type DiscoverUniverseStage } from '@dcf/utils';
import type { DiscoverUniverseProject } from '@/lib/api';

function TrendSpark({ direction }: { direction: 'up' | 'down' | 'flat' }) {
  const color =
    direction === 'up' ? 'text-emerald-400' : direction === 'down' ? 'text-red-400' : 'text-zinc-500';
  const icon = direction === 'up' ? '↗' : direction === 'down' ? '↘' : '→';
  return <span className={`text-sm ${color}`}>{icon}</span>;
}

export function DiscoverTopProjectsTable({ projects }: { projects: DiscoverUniverseProject[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800/80">
      <div className="border-b border-zinc-800/60 px-4 py-3">
        <h2 className="text-sm font-semibold text-white">Top Projects</h2>
        <p className="text-xs text-zinc-500">Ranked by activity — for deep research</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800/60 text-[10px] uppercase tracking-wider text-zinc-500">
              <th className="px-4 py-2 font-medium">#</th>
              <th className="px-4 py-2 font-medium">Project</th>
              <th className="px-4 py-2 font-medium">Stage</th>
              <th className="px-4 py-2 font-medium">Conviction</th>
              <th className="px-4 py-2 font-medium">DD Flow</th>
              <th className="px-4 py-2 font-medium">Trend</th>
              <th className="px-4 py-2 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {projects.slice(0, 15).map((p, i) => {
              const stage = p.universeStage as DiscoverUniverseStage;
              const colors = DISCOVER_UNIVERSE_COLORS[stage] ?? DISCOVER_UNIVERSE_COLORS.building;
              return (
                <tr
                  key={p.slug}
                  className="border-b border-zinc-800/40 transition hover:bg-zinc-900/40"
                >
                  <td className="px-4 py-3 text-zinc-500">{i + 1}</td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/project/${p.slug}`}
                      className="flex items-center gap-2 font-medium text-white hover:text-emerald-300"
                    >
                      <span
                        className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-lg border text-xs font-bold"
                        style={{ borderColor: colors.border }}
                      >
                        {p.logoUrl ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={p.logoUrl} alt="" className="h-full w-full object-cover" />
                        ) : (
                          p.ticker.slice(0, 2)
                        )}
                      </span>
                      <span>
                        {p.name}
                        <span className="ml-1 text-zinc-500">{p.ticker}</span>
                      </span>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase"
                      style={{ background: `${colors.color}22`, color: colors.border }}
                    >
                      {colors.label}
                    </span>
                  </td>
                  <td className="px-4 py-3 tabular-nums text-zinc-300">
                    {p.convictionScore}
                    <span className="text-zinc-600">/100</span>
                  </td>
                  <td className="px-4 py-3 tabular-nums">
                    {p.ddInflow24h > 0 ? (
                      <span className="text-emerald-400">+{formatUsd(p.ddInflow24h, 0)}</span>
                    ) : (
                      <span className="text-zinc-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <TrendSpark direction={p.trendDirection} />
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/project/${p.slug}`}
                      className="rounded-lg bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
                    >
                      View
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
