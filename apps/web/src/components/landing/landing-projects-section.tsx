'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { DiscoverUniverseMap } from '@/components/discover/discover-universe-map';
import { DiscoverTopProjectsTable } from '@/components/discover/discover-top-table';
import type { BubbleScamLevel } from '@/components/discover/discover-universe-bubble';
import {
  fetchDiscoverUniverse,
  fetchTrustInvestigations,
  type DiscoverTimeframe,
  type DiscoverUniverseResponse,
  type DiscoverUniverseStageFilter,
  type TrustInvestigation,
} from '@/lib/api';

/**
 * Landing Projects section — reuses the REAL Discover bubble map and
 * top projects table. The ranking algorithm is the existing Discover
 * activity-score system (0–100, weighted across build posts, GitHub,
 * DDollar inflow, trade volume, followers, scout stakes, community
 * threads, and long-term bubble score) — not a custom one.
 *
 * Bubbles are tinted red when the Trust Center has scam allegations:
 *   level 1 — under review (rim turns red)
 *   level 2 — high alert (>10% of voters marked scam → light red fill)
 */
function buildScamMap(investigations: TrustInvestigation[]): Record<string, BubbleScamLevel> {
  const map: Record<string, BubbleScamLevel> = {};
  for (const inv of investigations) {
    const slug = inv.project?.slug;
    if (!slug) continue;
    const scamPct = inv.tally?.scamPercent ?? 0;
    if (scamPct > 10) {
      map[slug] = 2;
    } else if (inv.scamScore > 0 || scamPct > 0) {
      map[slug] = 1;
    }
  }
  return map;
}

export function LandingProjectsSection() {
  const [stageFilter, setStageFilter] = useState<DiscoverUniverseStageFilter>('all');
  const [chainSlug, setChainSlug] = useState('');
  const [timeframe, setTimeframe] = useState<DiscoverTimeframe>('24h');
  const [data, setData] = useState<DiscoverUniverseResponse | null>(null);
  const [scamMap, setScamMap] = useState<Record<string, BubbleScamLevel>>({});
  const [loading, setLoading] = useState(true);

  const loadUniverse = useCallback(async () => {
    setLoading(true);
    try {
      const [res, investigations] = await Promise.all([
        fetchDiscoverUniverse({ stageFilter, chainSlug: chainSlug || undefined, timeframe }),
        fetchTrustInvestigations().catch(() => [] as TrustInvestigation[]),
      ]);
      setData(res);
      setScamMap(buildScamMap(investigations));
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [stageFilter, chainSlug, timeframe]);

  useEffect(() => {
    void loadUniverse();
  }, [loadUniverse]);

  const scamCount = useMemo(() => Object.values(scamMap).filter((v) => v > 0).length, [scamMap]);

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
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-zinc-500">
          <span className="inline-flex items-center gap-1">
            <span className="text-amber-300">⭐</span> Hot — high activity / trending up
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full border border-red-400" /> Under review
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-red-400/70" /> Scam alert (&gt;10% voters)
          </span>
          {scamCount > 0 && (
            <span className="font-semibold text-red-300/80">{scamCount} flagged</span>
          )}
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
              scamMap={scamMap}
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
