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
  fetchTrustInvestigations,
  fetchTrustCommunityReviews,
  type PlatformAdoptionProjectRow,
  type TrustInvestigation,
  type TrustCommunityReview,
  type PlatformStats,
} from '@/lib/api';

/* ------------------------------------------------------------------ */
/*  Ranking algorithm                                                  */
/* ------------------------------------------------------------------ */
/**
 * Composite project ranking across every signal the platform tracks.
 *
 *   rankScore = 0.25 · norm(activityScore)        // overall activity
 *             + 0.20 · norm(ddollarVolume)        // economic activity
 *             + 0.15 · norm(bubbleScore)          // launch readiness
 *             + 0.15 · norm(githubEvents)         // code shipping
 *             + 0.10 · norm(buildPosts)           // public building
 *             + 0.10 · norm(tokensIn + tokensOut) // AI usage
 *             + 0.05 · norm(aiCalls)              // AI engagement
 *
 * Min-max normalization is per-fetched-set so the ordering adapts to
 * whatever the platform returns (last 14 days by default). A project
 * can't game a single metric — it has to perform across the board.
 */
const RANK_WEIGHTS = {
  activity: 0.25,
  ddollar: 0.2,
  bubble: 0.15,
  github: 0.15,
  posts: 0.1,
  tokens: 0.1,
  ai: 0.05,
} as const;

function normalize(value: number, max: number): number {
  if (max <= 0) return 0;
  return value / max;
}

type RankedProject = PlatformAdoptionProjectRow & {
  _rank: number;
  _github: number;
  _tokens: number;
};

function rankProjects(rows: PlatformAdoptionProjectRow[]): RankedProject[] {
  if (rows.length === 0) return [];
  const max = {
    activity: Math.max(1, ...rows.map((r) => r.activityScore)),
    ddollar: Math.max(1, ...rows.map((r) => r.ddollarVolume)),
    bubble: Math.max(1, ...rows.map((r) => r.bubbleScore)),
    github: Math.max(1, ...rows.map((r) => r.githubEvents)),
    posts: Math.max(1, ...rows.map((r) => r.buildPosts)),
    tokens: Math.max(1, ...rows.map((r) => r.tokensIn + r.tokensOut)),
    ai: Math.max(1, ...rows.map((r) => r.aiCalls)),
  };
  return [...rows]
    .map((r) => {
      const tokens = r.tokensIn + r.tokensOut;
      const rank =
        RANK_WEIGHTS.activity * normalize(r.activityScore, max.activity) +
        RANK_WEIGHTS.ddollar * normalize(r.ddollarVolume, max.ddollar) +
        RANK_WEIGHTS.bubble * normalize(r.bubbleScore, max.bubble) +
        RANK_WEIGHTS.github * normalize(r.githubEvents, max.github) +
        RANK_WEIGHTS.posts * normalize(r.buildPosts, max.posts) +
        RANK_WEIGHTS.tokens * normalize(tokens, max.tokens) +
        RANK_WEIGHTS.ai * normalize(r.aiCalls, max.ai);
      return { ...r, _rank: rank, _github: r.githubEvents, _tokens: tokens };
    })
    .sort((a, b) => b._rank - a._rank);
}

/* ------------------------------------------------------------------ */
/*  Scam allegation aggregation                                        */
/* ------------------------------------------------------------------ */
type ScamSignal = {
  reports: number;       // count of "Suspicious" / "Likely scam" community votes
  scamPercent: number;   // % of voters who marked scam in investigations
  totalVoters: number;   // total voters in the investigation
  highAlert: boolean;    // >10% of platform users marked scam
  rimAlert: boolean;     // 1-2 reports → red rim
};

function buildScamSignals(
  investigations: TrustInvestigation[],
  reviews: TrustCommunityReview[],
  communityMembers: number,
): Map<string, ScamSignal> {
  const map = new Map<string, ScamSignal>();

  // Community reviews → count scam-allegation votes per project ticker.
  const reportCounts = new Map<string, number>();
  for (const review of reviews) {
    const verdict = (review.vote || '').toLowerCase();
    if (verdict.includes('scam') || verdict.includes('suspect') || verdict.includes('likely scam')) {
      const key = review.application?.ticker?.toUpperCase() ?? '';
      if (key) reportCounts.set(key, (reportCounts.get(key) ?? 0) + 1);
    }
  }

  // Investigations → scamPercent + totalVoters per project slug.
  for (const inv of investigations) {
    const slug = inv.project?.slug ?? '';
    if (!slug) continue;
    const scamPercent = inv.tally?.scamPercent ?? 0;
    const totalVoters = inv.tally?.totalVoters ?? 0;
    const reports = reportCounts.get(inv.project?.ticker?.toUpperCase() ?? '') ?? 0;
    // >10% of platform users marked scam → high alert (light red fill)
    const userShare = communityMembers > 0 ? (totalVoters * scamPercent) / 100 / communityMembers : 0;
    const highAlert = scamPercent > 10 || userShare > 0.1;
    // 1-2 reports → red rim
    const rimAlert = reports >= 1 && reports <= 2;
    map.set(slug, { reports, scamPercent, totalVoters, highAlert, rimAlert });
  }

  // Also map by ticker for projects without an investigation but with reviews.
  for (const [ticker, reports] of reportCounts) {
    if (!reports) continue;
    const slugKey = `ticker:${ticker}`;
    if (map.has(slugKey)) continue;
    map.set(slugKey, {
      reports,
      scamPercent: 0,
      totalVoters: 0,
      highAlert: false,
      rimAlert: reports >= 1 && reports <= 2,
    });
  }

  return map;
}

function scamColor(signal: ScamSignal | undefined): {
  fill: string;
  stroke: string;
  fillOpacity: number;
} {
  if (!signal) return { fill: '#a78bfa', stroke: '#c4b5fd', fillOpacity: 0.55 };
  if (signal.highAlert) return { fill: '#fca5a5', stroke: '#ef4444', fillOpacity: 0.7 }; // light red fill
  if (signal.rimAlert) return { fill: '#a78bfa', stroke: '#ef4444', fillOpacity: 0.55 }; // red rim only
  if (signal.reports > 0) return { fill: '#a78bfa', stroke: '#f87171', fillOpacity: 0.55 }; // 3+ reports, not yet 10%
  return { fill: '#a78bfa', stroke: '#c4b5fd', fillOpacity: 0.55 };
}

/* ------------------------------------------------------------------ */
/*  Components                                                         */
/* ------------------------------------------------------------------ */
function ProjectRow({ project, rank, scam }: { project: RankedProject; rank: number; scam?: ScamSignal }) {
  const scamBadge =
    scam?.highAlert ? { text: 'High alert', cls: 'border-red-500/40 bg-red-950/40 text-red-300' }
    : scam?.rimAlert ? { text: `${scam.reports} report${scam.reports > 1 ? 's' : ''}`, cls: 'border-red-500/30 bg-red-950/30 text-red-300' }
    : scam && scam.reports > 0 ? { text: `${scam.reports} reports`, cls: 'border-orange-500/30 bg-orange-950/30 text-orange-300' }
    : null;

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
          Activity {project.activityScore} · {formatUsd(project.ddollarVolume, 0)} DDollar · {project._github} GitHub · {project.buildPosts} posts
        </p>
      </div>
      {scamBadge ? (
        <span className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold ${scamBadge.cls}`}>
          {scamBadge.text}
        </span>
      ) : (
        <span className="shrink-0 rounded-md border border-emerald-500/20 bg-emerald-950/40 px-2 py-0.5 text-[10px] font-bold text-emerald-300">
          {project._rank.toFixed(2)}
        </span>
      )}
    </Link>
  );
}

/* Per-bubble coloring requires individual <Scatter> per point because
   recharts applies a single fill to the whole series. */
function BubbleMapColored({
  projects,
  scamSignals,
}: {
  projects: RankedProject[];
  scamSignals: Map<string, ScamSignal>;
}) {
  const data = projects.map((p) => {
    const signal = scamSignals.get(p.slug) ?? scamSignals.get(`ticker:${p.ticker.toUpperCase()}`);
    const color = scamColor(signal);
    return {
      name: p.ticker,
      slug: p.slug,
      x: p.activityScore,
      y: p.buildPosts,
      z: Math.max(20, p.ddollarVolume),
      ddollar: p.ddollarVolume,
      github: p._github,
      fill: color.fill,
      stroke: color.stroke,
      fillOpacity: color.fillOpacity,
      scam: signal,
    };
  });

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
            contentStyle={{ background: '#09090b', border: '1px solid #3f3f46', borderRadius: 8, fontSize: 11 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as (typeof data)[number];
              return (
                <div className="rounded-lg border border-zinc-700 bg-black/90 px-2.5 py-2 text-[11px] shadow-xl">
                  <p className="font-bold text-white">${d.name}</p>
                  <p className="text-zinc-400">Activity {d.x}</p>
                  <p className="text-zinc-400">Posts {d.y} · GitHub {d.github}</p>
                  <p className="text-emerald-300">{formatUsd(d.ddollar, 0)} DDollar</p>
                  {d.scam ? (
                    <p className="mt-0.5 text-red-300">
                      {d.scam.reports} scam report{d.scam.reports !== 1 ? 's' : ''}
                      {d.scam.highAlert ? ' · HIGH ALERT' : ''}
                    </p>
                  ) : null}
                </div>
              );
            }}
          />
          {data.map((point) => (
            <Scatter
              key={point.slug}
              data={[point]}
              fill={point.fill}
              fillOpacity={point.fillOpacity}
              stroke={point.stroke}
              strokeWidth={2}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LandingProjectsSection({
  platformStats,
}: {
  platformStats?: PlatformStats | null;
}) {
  const [projects, setProjects] = useState<PlatformAdoptionProjectRow[] | null>(null);
  const [scamSignals, setScamSignals] = useState<Map<string, ScamSignal>>(new Map());

  useEffect(() => {
    const communityMembers = platformStats?.communityMembers ?? 0;
    Promise.all([
      fetchPlatformAdoptionMetrics(14).catch(() => null),
      fetchTrustInvestigations().catch(() => [] as TrustInvestigation[]),
      fetchTrustCommunityReviews().catch(() => [] as TrustCommunityReview[]),
    ]).then(([metrics, investigations, reviews]) => {
      if (metrics) setProjects(metrics.projects);
      setScamSignals(buildScamSignals(investigations, reviews, communityMembers));
    });
  }, [platformStats?.communityMembers]);

  const ranked = useMemo(() => (projects ? rankProjects(projects) : []), [projects]);
  const top5 = ranked.slice(0, 5);

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c]">
      <div className="border-b border-zinc-800/70 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">Projects</p>
            <p className="mt-0.5 text-sm text-zinc-400">
              Top 5 · ranked by activity, DDollar, GitHub, posts, AI usage (14d)
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
            top5.map((p, i) => {
              const scam = scamSignals.get(p.slug) ?? scamSignals.get(`ticker:${p.ticker.toUpperCase()}`);
              return <ProjectRow key={p.slug} project={p} rank={i + 1} scam={scam} />;
            })
          )}
          <p className="px-1 pt-1 text-[10px] text-zinc-600">
            Click any project to see its full profile ·{' '}
            <Link href="/projects" className="text-violet-400 hover:text-violet-300">
              view all listed projects
            </Link>
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800/60 bg-black/20 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Bubble map · size = DDollar volume
            </p>
          </div>
          <BubbleMapColored projects={ranked} scamSignals={scamSignals} />
          <div className="mt-2 flex flex-wrap gap-2 text-[9px] text-zinc-500">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-violet-400" /> Healthy
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full border-2 border-red-500 bg-violet-400" /> 1-2 scam reports
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-red-300" />&gt;10% marked scam
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
