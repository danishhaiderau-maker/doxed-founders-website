'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  Scatter,
  ScatterChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { formatUsd } from '@dcf/utils';
import {
  fetchPlatformAdoptionMetrics,
  type PlatformAdoptionProjectRow,
} from '@/lib/api';

/**
 * Ranking algorithm — blends activity, DDollar volume, and bubble score
 * so a project can't game one metric. Higher = more deserving of attention.
 *
 *   rankScore = 0.45 * normalized(activityScore)
 *             + 0.35 * normalized(ddollarVolume)
 *             + 0.20 * normalized(bubbleScore)
 *
 * Normalization is min-max across the fetched project set so the ordering
 * adapts to whatever the platform returns (last 14 days by default).
 */
function rankProjects(rows: PlatformAdoptionProjectRow[]): PlatformAdoptionProjectRow[] {
  if (rows.length === 0) return [];
  const maxActivity = Math.max(1, ...rows.map((r) => r.activityScore));
  const maxDdollar = Math.max(1, ...rows.map((r) => r.ddollarVolume));
  const maxBubble = Math.max(1, ...rows.map((r) => r.bubbleScore));
  return [...rows]
    .map((r) => ({
      ...r,
      _rank:
        0.45 * (r.activityScore / maxActivity) +
        0.35 * (r.ddollarVolume / maxDdollar) +
        0.2 * (r.bubbleScore / maxBubble),
    }))
    .sort((a, b) => (b as any)._rank - (a as any)._rank);
}

function ProjectRow({ project, rank }: { project: PlatformAdoptionProjectRow; rank: number }) {
  return (
    <Link
      href={`/project/${project.slug}`}
      className="group flex items-center gap-3 rounded-xl border border-zinc-800/80 bg-black/30 px-3 py-2.5 transition hover:border-violet-500/40 hover:bg-violet-950/15"
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[11px] font-bold text-zinc-400">
        {rank}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-bold text-white group-hover:text-violet-50">
          {project.ticker}
          <span className="ml-1.5 font-normal text-zinc-500">· {project.name}</span>
        </p>
        <p className="mt-0.5 text-[10px] text-zinc-500">
          Activity {project.activityScore} · {formatUsd(project.ddollarVolume, 0)} DDollar · {project.buildPosts} posts
        </p>
      </div>
      <span className="shrink-0 rounded-md border border-emerald-500/20 bg-emerald-950/40 px-2 py-0.5 text-[10px] font-bold text-emerald-300">
        {project.bubbleScore.toFixed(1)}
      </span>
    </Link>
  );
}

function BubbleMap({ projects }: { projects: PlatformAdoptionProjectRow[] }) {
  const data = projects.map((p) => ({
    name: p.ticker,
    slug: p.slug,
    x: p.activityScore,
    y: p.buildPosts,
    z: Math.max(20, p.ddollarVolume),
    ddollar: p.ddollarVolume,
  }));

  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-xl border border-dashed border-zinc-800 text-xs text-zinc-600">
        Bubble map fills as projects ship on the platform.
      </div>
    );
  }

  return (
    <div className="h-56 w-full sm:h-64">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="x"
            name="Activity"
            tick={{ fill: '#71717a', fontSize: 10 }}
            stroke="#3f3f46"
            label={{ value: 'Activity score', position: 'insideBottom', offset: -2, fill: '#71717a', fontSize: 10 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name="Build posts"
            tick={{ fill: '#71717a', fontSize: 10 }}
            stroke="#3f3f46"
            width={36}
            label={{ value: 'Posts', angle: -90, position: 'insideLeft', fill: '#71717a', fontSize: 10 }}
          />
          <ZAxis type="number" dataKey="z" range={[40, 600]} name="DDollar volume" />
          <Tooltip
            cursor={{ strokeDasharray: '3 3', stroke: '#52525b' }}
            contentStyle={{
              background: '#09090b',
              border: '1px solid #3f3f46',
              borderRadius: 8,
              fontSize: 11,
            }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as (typeof data)[number];
              return (
                <div className="rounded-lg border border-zinc-700 bg-black/90 px-2.5 py-2 text-[11px] shadow-xl">
                  <p className="font-bold text-white">${d.name}</p>
                  <p className="text-zinc-400">Activity {d.x}</p>
                  <p className="text-zinc-400">Posts {d.y}</p>
                  <p className="text-emerald-300">{formatUsd(d.ddollar, 0)} DDollar</p>
                </div>
              );
            }}
          />
          <Scatter data={data} fill="#a78bfa" fillOpacity={0.55} stroke="#c4b5fd" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LandingProjectsSection() {
  const [projects, setProjects] = useState<PlatformAdoptionProjectRow[] | null>(null);

  useEffect(() => {
    fetchPlatformAdoptionMetrics(14)
      .then((res) => setProjects(res.projects))
      .catch(() => setProjects(null));
  }, []);

  const ranked = useMemo(() => (projects ? rankProjects(projects) : []), [projects]);
  const top5 = ranked.slice(0, 5);

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c]">
      <div className="border-b border-zinc-800/70 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">Projects</p>
            <p className="mt-0.5 text-sm text-zinc-400">
              Top 5 by activity, DDollar volume & bubble score (14d)
            </p>
          </div>
          <Link
            href="/projects"
            className="shrink-0 text-[11px] font-semibold text-violet-300 hover:text-violet-200"
          >
            See more projects →
          </Link>
        </div>
      </div>

      <div className="grid gap-3 p-3 sm:p-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-2">
          {top5.length === 0 ? (
            <div className="rounded-xl border border-dashed border-zinc-800 px-3 py-6 text-center text-xs text-zinc-600">
              Loading top projects…
            </div>
          ) : (
            top5.map((p, i) => <ProjectRow key={p.slug} project={p} rank={i + 1} />)
          )}
          <p className="px-1 pt-1 text-[10px] text-zinc-600">
            Click any project to see its full profile ·{' '}
            <Link href="/projects" className="text-violet-400 hover:text-violet-300">
              view all listed projects
            </Link>
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800/60 bg-black/20 p-3">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Bubble map · size = DDollar volume
          </p>
          <BubbleMap projects={ranked} />
        </div>
      </div>
    </section>
  );
}
