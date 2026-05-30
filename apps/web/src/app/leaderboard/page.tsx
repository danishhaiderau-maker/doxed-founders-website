'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { SiteNav } from '@/components/site-nav';
import { TraderRankTabs, type TraderRankTab } from '@/components/trader-rank-tabs';

function LeaderboardInner() {
  const searchParams = useSearchParams();
  const tabParam = searchParams.get('tab');
  const initialTab: TraderRankTab = tabParam === 'losers' ? 'losers' : 'winners';

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/paper-trading" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Paper Trading
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Trader rankings</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Top traders by portfolio value · Top losers by biggest paper losses (busted accounts flagged).
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        <TraderRankTabs initialTab={initialTab} />
      </div>
    </main>
  );
}

export default function LeaderboardPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508] p-8 text-[var(--color-muted)]">Loading…</div>}>
      <LeaderboardInner />
    </Suspense>
  );
}
