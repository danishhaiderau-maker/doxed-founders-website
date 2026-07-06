'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import { formatUsd } from '@dcf/utils';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { ProjectMaturityBadge } from '@/components/project-maturity-badge';
import { fetchDemandHeatmap } from '@/lib/api';

type HeatmapRow = {
  project: {
    slug: string;
    name: string;
    ticker: string;
    logoUrl: string | null;
    lifecycleStage?: string;
    isLiveToken?: boolean;
  };
  goalUsd: number;
  totalDemand: number;
  allocatorCount: number;
};

export default function RaiseRoomPage() {
  const { data: session } = useSession();
  const [rows, setRows] = useState<HeatmapRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDemandHeatmap()
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-xl font-semibold">Raise Room</h1>
            <p className="text-xs text-zinc-500">
              Discover tomorrow&apos;s founders — paper conviction before Founder Graduation
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-8 px-6 py-8">
        <section className="rounded-2xl border border-amber-500/25 bg-amber-950/10 p-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-amber-400">Validate</p>
          <h2 className="mt-2 text-2xl font-bold text-white">Discover founders worth backing</h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400">
            Followers are weak. Paper conviction and weighted community validation show who would
            commit — allocation registration on the path to Founder Graduation, not a token
            launchpad.
          </p>
          <Link
            href={session ? '/founder-den?tab=funding' : '/login?callbackUrl=/founder-den?tab=funding'}
            className="mt-5 inline-block rounded-xl bg-amber-600 px-5 py-2.5 text-sm font-semibold text-black hover:bg-amber-500"
          >
            {session ? 'Open Proof Raise (when eligible)' : 'Sign in to explore raises'}
          </Link>
        </section>

        <section>
          <h3 className="text-lg font-semibold text-white">Trending Proof Raises</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Paper dollars only — no real money, no token sale. Earn eligibility before graduation.
          </p>

          {loading && <p className="mt-6 text-sm text-zinc-500">Loading demand signals…</p>}

          {!loading && rows.length === 0 && (
            <div className="mt-6 rounded-xl border border-dashed border-zinc-800 p-10 text-center">
              <p className="text-zinc-400">No active Proof Raises yet.</p>
              <Link href="/founder-den" className="mt-3 inline-block text-sm text-amber-400 hover:underline">
                Founders: pass launch qualification, then open Proof Raise from Founder OS →
              </Link>
            </div>
          )}

          {!loading && rows.length > 0 && (
            <ul className="mt-6 space-y-3">
              {rows.map((r) => {
                const pct = r.goalUsd > 0 ? Math.min(100, Math.round((r.totalDemand / r.goalUsd) * 100)) : 0;
                return (
                  <li key={r.project.slug}>
                    <Link
                      href={`/project/${r.project.slug}`}
                      className="block rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-amber-500/35"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-950 text-xs font-bold text-amber-300">
                            {r.project.logoUrl ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={r.project.logoUrl} alt="" className="h-10 w-10 rounded-lg object-cover" />
                            ) : (
                              r.project.ticker.slice(0, 2)
                            )}
                          </div>
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="font-semibold text-white">{r.project.name}</p>
                              {r.project.lifecycleStage && (
                                <ProjectMaturityBadge
                                  lifecycleStage={r.project.lifecycleStage}
                                  isLiveToken={r.project.isLiveToken}
                                  hasActiveRaise
                                />
                              )}
                            </div>
                            <p className="text-xs text-zinc-500">${r.project.ticker}</p>
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="font-semibold text-amber-300">{formatUsd(r.totalDemand, 0)}</p>
                          <p className="text-xs text-zinc-500">
                            {r.allocatorCount} allocator{r.allocatorCount === 1 ? '' : 's'} · {pct}% of goal
                          </p>
                        </div>
                      </div>
                      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                        <div
                          className="h-full rounded-full bg-amber-500/80"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-5 text-sm text-zinc-400">
          <p className="font-medium text-zinc-200">Trust-first path</p>
          <p className="mt-2">
            List Project → Trust Center → Raise Room → Launch Qualification → Founder Graduation.
            Paper conviction and DDollar Proof of Contribution feed discovery — not an investment
            contract.
          </p>
          <p className="mt-3 text-xs text-zinc-500">
            Raise Room is a simulation layer for founder discovery. Paper dollars have no cash value.
            Consult independent legal advice before any real token raise.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link href="/paper-trading" className="text-blue-400 hover:underline">
              Proof of Conviction
            </Link>
            <Link href="/scout-votes" className="text-blue-400 hover:underline">
              Scout votes
            </Link>
            <Link href="/reputation" className="text-blue-400 hover:underline">
              Proof of Contribution
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}
