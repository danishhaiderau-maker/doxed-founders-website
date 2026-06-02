'use client';

import { useEffect, useState } from 'react';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { DiscoverMetricsRow } from '@/components/discover/discover-metrics-row';
import { DiscoverUniverseMap } from '@/components/discover/discover-universe-map';
import { DiscoverSidebar } from '@/components/discover/discover-sidebar';
import { DiscoverTopProjectsTable } from '@/components/discover/discover-top-table';
import { DiscoverBottomCtas } from '@/components/discover/discover-bottom-ctas';
import {
  fetchDiscoverUniverse,
  type DiscoverTimeframe,
  type DiscoverUniverseResponse,
  type DiscoverUniverseStageFilter,
} from '@/lib/api';

export default function DiscoverPage() {
  const [stageFilter, setStageFilter] = useState<DiscoverUniverseStageFilter>('all');
  const [chainSlug, setChainSlug] = useState('');
  const [timeframe, setTimeframe] = useState<DiscoverTimeframe>('24h');
  const [data, setData] = useState<DiscoverUniverseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchDiscoverUniverse({
      stageFilter,
      chainSlug: chainSlug || undefined,
      timeframe,
    })
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [stageFilter, chainSlug, timeframe]);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 sm:py-5 lg:px-10">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold tracking-tight">Discover Startups</h1>
            <p className="text-sm text-zinc-500">
              Track real builders. Follow conviction. Invest with DDollar.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto w-full max-w-[90rem] px-4 py-8 sm:px-6 lg:px-10">
        {error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        {data && <DiscoverMetricsRow metrics={data.metrics} />}

        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_280px]">
          <div>
            {loading && !data ? (
              <div className="flex h-[520px] items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-950/50">
                <p className="text-sm text-zinc-500">Loading project universe…</p>
              </div>
            ) : data ? (
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
            ) : null}
          </div>

          {data && (
            <DiscoverSidebar
              sidebar={data.sidebar}
              scoutCount={data.metrics.scoutReviewsAwaiting}
            />
          )}
        </div>

        {data && data.projects.length > 0 && (
          <div className="mt-10">
            <DiscoverTopProjectsTable projects={data.projects} />
          </div>
        )}

        <div className="mt-10">
          <DiscoverBottomCtas scoutCount={data?.metrics.scoutReviewsAwaiting ?? 0} />
        </div>
      </div>
    </main>
  );
}
