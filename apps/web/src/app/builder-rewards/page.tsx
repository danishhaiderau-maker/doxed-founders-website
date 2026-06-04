import { BuilderRewardsPage } from '@/components/builder-rewards/builder-rewards-page';

export default function BuilderRewardsRoute() {
  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <header className="mb-8 border-b border-zinc-800 pb-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-violet-400">Explore</p>
          <h1 className="mt-1 text-2xl font-bold">Builder Rewards</h1>
          <p className="mt-2 text-sm text-zinc-500">
            Trade builders, not hype — proof of contribution for the community pool.
          </p>
        </header>
        <BuilderRewardsPage />
      </div>
    </div>
  );
}
