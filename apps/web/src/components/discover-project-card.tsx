'use client';

import Link from 'next/link';
import { useState } from 'react';
import { formatUsd, formatTokenPrice, LIFECYCLE_STAGES, getStageBucketMeta, type StageBucket } from '@dcf/utils';
import type { DiscoverProject } from '@/lib/api';

function formatMc(value: number | null | undefined) {
  if (value == null) return '—';
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return formatUsd(value, 0);
}

function stageLabel(key: string) {
  return LIFECYCLE_STAGES.find((s) => s.key === key)?.label ?? key.replace(/_/g, ' ');
}

function daysAgo(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d < 1) return 'Today';
  if (d === 1) return '1 day ago';
  return `${d} days ago`;
}

export function DiscoverProjectCard({ project }: { project: DiscoverProject }) {
  const [flipped, setFlipped] = useState(false);
  const bucket = getStageBucketMeta(project.stageBucket as StageBucket);

  return (
    <div className="group h-[280px] [perspective:1000px]">
      <div
        className={`relative h-full w-full transition-transform duration-500 [transform-style:preserve-3d] ${
          flipped ? '[transform:rotateY(180deg)]' : ''
        }`}
      >
        {/* Front */}
        <div
          className="absolute inset-0 flex flex-col rounded-2xl border bg-zinc-900/60 p-5 [backface-visibility:hidden]"
          style={{ borderColor: `${bucket.border}55` }}
        >
          <div className="flex items-start gap-4">
            <div
              className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border-2 bg-zinc-950 text-sm font-bold"
              style={{ borderColor: bucket.border }}
            >
              {project.logoUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={project.logoUrl} alt="" className="h-full w-full object-cover" />
              ) : (
                project.ticker.slice(0, 2)
              )}
            </div>
            <div className="min-w-0 flex-1">
              <Link href={`/project/${project.slug}`} className="font-semibold text-white hover:text-emerald-300">
                {project.name}
              </Link>
              <p className="mt-0.5 text-xs" style={{ color: bucket.border }}>
                {bucket.label}
              </p>
              {project.founder && (
                <p className="mt-1 truncate text-xs text-zinc-500">{project.founder.name}</p>
              )}
            </div>
            <span className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">
              {project.founderScore}
            </span>
          </div>

          {project.founderVideoUrl && (
            <a
              href={project.founderVideoUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-4 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-sm text-emerald-200 hover:bg-emerald-950/40"
              onClick={(e) => e.stopPropagation()}
            >
              <span>▶</span>
              <span className="truncate">{project.founderVideoTitle ?? 'Founder intro'}</span>
            </a>
          )}

          <div className="mt-auto space-y-2 pt-4">
            <div className="flex justify-between text-xs text-zinc-500">
              <span>Demand</span>
              <span>
                {project.raiseGoalUsd > 0
                  ? `${formatUsd(project.simulatedDemand, 0)} / ${formatUsd(project.raiseGoalUsd, 0)}`
                  : formatUsd(project.simulatedDemand, 0)}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-emerald-500"
                style={{ width: `${project.demandPct || Math.min(100, project.journeyProgress)}%` }}
              />
            </div>
            <p className="text-[10px] text-zinc-600">{stageLabel(project.lifecycleStage)}</p>
          </div>

          <button
            type="button"
            onClick={() => setFlipped(true)}
            className="absolute bottom-3 right-3 text-[10px] text-zinc-600 hover:text-zinc-400"
          >
            Flip →
          </button>
        </div>

        {/* Back */}
        <div className="absolute inset-0 flex flex-col rounded-2xl border border-zinc-700 bg-zinc-950 p-5 [backface-visibility:hidden] [transform:rotateY(180deg)]">
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">Market snapshot</p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-zinc-500">Market cap</dt>
              <dd className="font-medium">{formatMc(project.marketCap)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-zinc-500">Token price</dt>
              <dd>{project.priceUsd != null ? formatTokenPrice(project.priceUsd) : '—'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-zinc-500">Followers</dt>
              <dd>{project.followerCount.toLocaleString()}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-zinc-500">24h volume</dt>
              <dd>{formatMc(project.volume24h)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-zinc-500">Last update</dt>
              <dd className="max-w-[140px] truncate text-right text-xs">
                {project.lastUpdateHeadline ?? daysAgo(project.lastUpdateAt)}
              </dd>
            </div>
          </dl>
          <Link
            href={`/project/${project.slug}`}
            className="mt-auto rounded-lg bg-emerald-600 py-2 text-center text-sm font-semibold text-white hover:bg-emerald-500"
          >
            Open project room
          </Link>
          <button
            type="button"
            onClick={() => setFlipped(false)}
            className="mt-2 text-center text-[10px] text-zinc-600 hover:text-zinc-400"
          >
            ← Back
          </button>
        </div>
      </div>
    </div>
  );
}
