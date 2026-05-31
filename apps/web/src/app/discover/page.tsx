'use client';

import { useEffect, useState } from 'react';
import { STAGE_BUCKETS } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { DiscoverBubbleMap } from '@/components/discover-bubble-map';
import { DiscoverProjectCard } from '@/components/discover-project-card';
import { DiscoverProject, fetchDiscoverProjects } from '@/lib/api';

const SORT_FILTERS = [
  { key: 'trending', label: 'Trending' },
  { key: 'most_followed', label: 'Most followed' },
  { key: 'highest_demand', label: 'Highest demand' },
  { key: 'live_tokens', label: 'Live tokens' },
  { key: 'newest', label: 'Newest' },
] as const;

export default function DiscoverPage() {
  const [sort, setSort] = useState<string>('trending');
  const [stageBucket, setStageBucket] = useState<string>('');
  const [projects, setProjects] = useState<DiscoverProject[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDiscoverProjects(sort, stageBucket || undefined)
      .then(setProjects)
      .catch((e: Error) => setError(e.message));
  }, [sort, stageBucket]);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 sm:py-5 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Discover startups</h1>
            <p className="text-sm text-zinc-500">
              Logo bubbles · stage colors · flip cards for market depth
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setStageBucket('')}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              !stageBucket ? 'bg-white/10 font-semibold text-white' : 'text-zinc-500 hover:text-white'
            }`}
          >
            All
          </button>
          {STAGE_BUCKETS.map((b) => (
            <button
              key={b.key}
              type="button"
              onClick={() => setStageBucket(b.key)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                stageBucket === b.key ? 'font-semibold text-white ring-1' : 'text-zinc-500 hover:text-white'
              }`}
              style={
                stageBucket === b.key
                  ? { background: `${b.color}22`, borderColor: b.border, boxShadow: `0 0 0 1px ${b.border}55` }
                  : undefined
              }
            >
              {b.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {SORT_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setSort(f.key)}
              className={`rounded-lg px-3 py-1 text-xs ${
                sort === f.key ? 'bg-emerald-500/20 text-emerald-200' : 'text-zinc-600 hover:text-zinc-400'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

        <div className="mt-8">
          <DiscoverBubbleMap projects={projects} />
        </div>

        <div className="mt-10">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-zinc-500">Project cards</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <DiscoverProjectCard key={p.slug} project={p} />
            ))}
          </div>
          {projects.length === 0 && !error && (
            <p className="mt-8 text-center text-zinc-500">Loading ecosystem…</p>
          )}
        </div>
      </div>
    </main>
  );
}
