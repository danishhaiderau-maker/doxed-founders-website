'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import type { RaiseRoomProjectCard } from '@/lib/api';

export function RaiseRoomProjectCardView({ project }: { project: RaiseRoomProjectCard }) {
  return (
    <Link
      href={`/project/${project.slug}`}
      className="block rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-amber-500/35"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-zinc-950 text-xs font-bold text-amber-300">
            {project.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={project.logoUrl} alt="" className="h-11 w-11 rounded-lg object-cover" />
            ) : (
              project.ticker.slice(0, 2)
            )}
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-semibold text-white">{project.name}</p>
              <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                {project.rewardTier}
              </span>
            </div>
            <p className="text-xs text-zinc-500">
              ${project.ticker}
              {project.category ? ` · ${project.category.name}` : ''}
            </p>
          </div>
        </div>
        <div className="text-right">
          <p className="font-semibold text-amber-300">{formatUsd(project.paperConviction, 0)}</p>
          <p className="text-xs text-zinc-500">
            LQ {project.launchQualityScore} · {project.demandPct}% of goal
          </p>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-amber-500/80"
          style={{ width: `${project.demandPct}%` }}
        />
      </div>
      <p className="mt-3 line-clamp-2 text-xs text-zinc-400">{project.aiSummary}</p>
    </Link>
  );
}

export function RaiseRoomTrendingCards({ projects }: { projects: RaiseRoomProjectCard[] }) {
  if (projects.length === 0) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-white">Trending now</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {projects.slice(0, 4).map((p) => (
          <RaiseRoomProjectCardView key={p.raiseId} project={p} />
        ))}
      </div>
    </section>
  );
}
