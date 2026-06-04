import Link from 'next/link';
import { BuilderRewardsPage } from '@/components/builder-rewards/builder-rewards-page';
import { SiteNav } from '@/components/site-nav';

export default function BuilderRewardsRoute() {
  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <header className="sticky top-0 z-20 border-b border-zinc-800 bg-black/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div className="min-w-0">
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <p className="mt-2 text-[10px] font-bold uppercase tracking-[0.25em] text-violet-400">Explore</p>
            <h1 className="mt-1 text-2xl font-bold">Builder Rewards</h1>
            <p className="mt-2 text-sm text-zinc-500">
              Trade builders, not hype — proof of contribution for the community pool.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <BuilderRewardsPage />
      </div>
    </div>
  );
}
