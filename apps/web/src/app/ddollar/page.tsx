'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { DdollarWalletPage } from '@/components/ddollar/ddollar-wallet-page';

export default function DdollarPage() {
  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Ddollar</h1>
            <p className="text-sm text-zinc-500">
              In-game currency — earn by contributing · spend on agents and paper trading
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        <p className="mb-6 text-sm text-zinc-500">
          Ddollar powers paper trading, agent rentals, scout voting, and platform rewards.{' '}
          <Link href="/leaderboard" className="text-emerald-400 hover:underline">
            Leaderboard →
          </Link>
        </p>
        <DdollarWalletPage />
      </div>
    </main>
  );
}
