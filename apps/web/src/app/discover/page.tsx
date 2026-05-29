'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { formatUsd, LIFECYCLE_STAGES } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { DiscoverProject, fetchDiscoverProjects } from '@/lib/api';

const FILTERS = [
  { key: 'trending', label: 'Trending' },
  { key: 'most_followed', label: 'Most followed' },
  { key: 'highest_demand', label: 'Highest demand' },
  { key: 'launch_ready', label: 'Launch ready' },
  { key: 'recently_launched', label: 'Recently launched' },
  { key: 'newest', label: 'Newest' },
] as const;

function stageLabel(key: string) {
  return LIFECYCLE_STAGES.find((s) => s.key === key)?.label ?? key.replace(/_/g, ' ');
}

export default function DiscoverPage() {
  const [filter, setFilter] = useState<string>('trending');
  const [projects, setProjects] = useState<DiscoverProject[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDiscoverProjects(filter)
      .then(setProjects)
      .catch((e: Error) => setError(e.message));
  }, [filter]);

  const maxBubble = useMemo(
    () => Math.max(...projects.map((p) => p.bubbleScore), 1),
    [projects],
  );

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Bubble discovery</h1>
            <p className="text-sm text-zinc-500">
              Size = attention · Color = lifecycle stage · Click to open project room
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                filter === f.key
                  ? 'bg-emerald-500/20 font-semibold text-emerald-200 ring-1 ring-emerald-500/40'
                  : 'text-zinc-500 hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

        <div className="relative mt-8 min-h-[420px] rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8">
          {projects.length === 0 && !error && (
            <p className="text-center text-zinc-500">Loading project universe…</p>
          )}
          <div className="flex flex-wrap items-center justify-center gap-4">
            {projects.slice(0, 24).map((p, i) => {
              const size = 64 + (p.bubbleScore / maxBubble) * 96;
              const hue = (i * 47) % 360;
              return (
                <Link
                  key={p.slug}
                  href={`/project/${p.slug}`}
                  className="group flex flex-col items-center transition hover:scale-105"
                  style={{ width: size + 24 }}
                >
                  <div
                    className="flex items-center justify-center rounded-full border border-white/10 font-bold text-white shadow-lg transition group-hover:border-emerald-400/50"
                    style={{
                      width: size,
                      height: size,
                      background: `radial-gradient(circle at 30% 30%, hsl(${hue} 60% 45%), hsl(${hue} 50% 25%))`,
                      fontSize: size > 100 ? 14 : 11,
                    }}
                    title={p.name}
                  >
                    {p.ticker.slice(0, 4)}
                  </div>
                  <p className="mt-2 max-w-[100px] truncate text-center text-xs font-medium text-zinc-300 group-hover:text-emerald-300">
                    {p.name}
                  </p>
                  <p className="text-[10px] text-zinc-500">{stageLabel(p.lifecycleStage)}</p>
                </Link>
              );
            })}
          </div>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.slice(0, 12).map((p) => (
            <Link
              key={p.slug}
              href={`/project/${p.slug}`}
              className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-5 transition hover:border-emerald-500/40"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-white">{p.name}</p>
                  <p className="text-xs text-zinc-500">{stageLabel(p.lifecycleStage)}</p>
                </div>
                <span className="text-xs font-medium text-emerald-400">{p.launchReadiness}% ready</span>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-zinc-400">
                <span>Followers {p.followerCount.toLocaleString()}</span>
                <span>Demand {formatUsd(p.simulatedDemand, 0)}</span>
                <span>Founder {p.founderScore}</span>
                <span>Streak {p.buildStreakDays}d</span>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
