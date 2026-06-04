'use client';

import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AirdropRunwayPage } from '@/components/airdrop/airdrop-runway-page';

export default function AirdropPage() {
  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Airdrop Runway</h1>
            <p className="text-sm text-zinc-500">Top X-linked accounts · activity-weighted claim preview</p>
          </div>
          <SiteNav />
        </div>
      </header>
      <div className="mx-auto max-w-5xl px-6 py-8">
        <AirdropRunwayPage />
      </div>
    </main>
  );
}
