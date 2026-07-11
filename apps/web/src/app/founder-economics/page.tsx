'use client';

import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderEconomicsDashboard } from '@/components/founder-economics/founder-economics-dashboard';

export default function FounderEconomicsPage() {
  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Founder Economics</h1>
            <p className="text-xs text-zinc-500">
              Automated epoch-based vesting · on-chain simple, off-chain swappable
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <FounderEconomicsDashboard />
      </div>
    </main>
  );
}
