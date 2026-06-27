'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { DiscoverUniverseMap } from '@/components/discover/discover-universe-map';
import { DiscoverTopProjectsTable } from '@/components/discover/discover-top-table';
import {
  fetchDiscoverUniverse,
  type DiscoverTimeframe,
  type DiscoverUniverseResponse,
  type DiscoverUniverseStageFilter,
} from '@/lib/api';

/**
 * Landing Projects section — reuses the REAL Discover bubble map and
 * top projects table. The ranking algorithm is the existing Discover
 * activity-score system (0–100, weighted across build posts, GitHub,
 * DDollar inflow, trade volume, followers, scout stakes, community
 * threads, and long-term bubble score) — not a custom one.
 */
export function LandingProjectsSection() {
  const [stageFilter, setStageFilter] = useState<DiscoverUniverseStageFilter>('all');
  const [chainSlug, setChainSlug] = useState('');
  const [timeframe, setTimeframe] = useState<DiscoverTimeframe>('24h');
  const [data, setData] = useState<DiscoverUniverseResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUniverse = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchDiscoverUniverse({ stageFilter, chainSlug: chainSlug || undefined, timeframe });
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [stageFilter, chainSlug, timeframe]);

  useEffect(() => {
    void loadUniverse();
  }, [loadUniverse]);

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c]">
      <div className="border-b border-zinc-800/70 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">Projects</p>
            <p className="mt-0.5 text-sm text-zinc-400">
              Top projects · ranked by activity score (GitHub, DDollar, posts, followers)
            </p>
          </div>
          <Link
            href="/discover"
            className="shrink-0 text-[11px] font-semibold text-violet-300 hover:text-violet-200"
          >
            Open full Discover →
          </Link>
        </div>
      </div>

      <div className="p-3 sm:p-4">
        {loading && !data ? (
          <div className="flex min-h-[420px] flex-col items-center justify-center gap-3 rounded-xl border border-zinc-800 bg-zinc-950/50">
            <div className="flex gap-3">
              {[96, 72, 120, 56].map((size, i) => (
                <span
                  key={size}
                  className="animate-pulse rounded-full bg-violet-500/20"
                  style={{ width: size, height: size, animationDelay: `${i * 120}ms` }}
                />
              ))}
            </div>
            <p className="text-sm text-zinc-500">Loading project universe…</p>
          </div>
        ) : data ? (
          <div className="space-y-4">
            <DiscoverUniverseMap
              projects={data.projects}
              chains={data.chains}
              stageFilter={stageFilter}
              chainSlug={chainSlug}
              timeframe={timeframe}
              onStageFilter={setStageFilter}
              onChainSlug={setChainSlug}
              onTimeframe={setTimeframe}
            />
            {data.projects.length > 0 && (
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-zinc-500">
                    Top Projects
                  </p>
                  <Link href="/projects" className="text-[11px] font-semibold text-violet-300 hover:text-violet-200">
                    See more projects →
                  </Link>
                </div>
                <DiscoverTopProjectsTable projects={data.projects.slice(0, 5)} />
              </div>
            )}
          </div>
        ) : (
          <div className="flex min-h-[200px] items-center justify-center rounded-xl border border-dashed border-zinc-800 text-sm text-zinc-600">
            Could not load projects —{' '}
            <button type="button" onClick={() => void loadUniverse()} className="ml-1 underline hover:text-white">
              retry
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
