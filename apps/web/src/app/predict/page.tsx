'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { PREDICTION_MARKET_HOURS } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { PredictionConstitutionPanel } from '@/components/predict/prediction-constitution-panel';
import { PredictionMarketsLive } from '@/components/predict/prediction-markets-live';
import {
  PredictionPlatformNav,
  type PredictionPlatformTab,
} from '@/components/predict/prediction-platform-nav';
import { PredictionOracleRankPanel } from '@/components/predict/prediction-oracle-rank-panel';
import { PredictionTraderRankPanel } from '@/components/predict/prediction-trader-rank-panel';

function parseTab(raw: string | null): PredictionPlatformTab {
  if (raw === 'markets' || raw === 'oracle' || raw === 'traders') return raw;
  return 'rules';
}

function PredictPageInner() {
  const searchParams = useSearchParams();
  const tab = parseTab(searchParams.get('tab'));

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <SiteBrand className="mt-1 text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Public conviction markets</h1>
            <p className="text-sm text-zinc-500">
              DDollar stakes on founder execution, projects, and proof — not generic gambling
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <div className="flex flex-wrap gap-2">
          <Link
            href="/paper-trading"
            className="rounded-lg border border-amber-500/40 bg-amber-950/25 px-3 py-1.5 text-sm font-medium text-amber-100 hover:bg-amber-950/40"
          >
            Trading Alpha
          </Link>
          <span className="self-center text-xs text-zinc-600">paired with</span>
          <span className="rounded-lg bg-indigo-600/30 px-3 py-1.5 text-sm font-semibold text-indigo-100 ring-1 ring-indigo-500/40">
            Predictions
          </span>
          <span className="w-full text-[10px] text-zinc-600 sm:w-auto sm:ml-auto">
            Windows close after {PREDICTION_MARKET_HOURS}h · separate Oracle vs Trading ranks
          </span>
        </div>

        <PredictionPlatformNav active={tab} />

        {tab === 'rules' && <PredictionConstitutionPanel />}
        {tab === 'markets' && <PredictionMarketsLive />}
        {tab === 'oracle' && <PredictionOracleRankPanel />}
        {tab === 'traders' && <PredictionTraderRankPanel />}
      </div>
    </main>
  );
}

export default function PredictPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#050508] p-8 text-sm text-zinc-500">Loading prediction platform…</div>
      }
    >
      <PredictPageInner />
    </Suspense>
  );
}
