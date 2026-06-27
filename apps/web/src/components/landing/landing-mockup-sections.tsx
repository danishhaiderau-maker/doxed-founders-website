'use client';

import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { LandingHubPreviews } from '@/components/landing/landing-feature-hub';
import { LandingPlatformAdoption } from '@/components/landing/landing-platform-adoption';
import { LandingFunFactBar } from '@/components/landing/landing-fun-fact-bar';
import { LandingHero } from '@/components/landing/landing-hero';
import { LandingPulseBar } from '@/components/landing/landing-pulse-bar';
import { LandingProjectsSection } from '@/components/landing/landing-projects-section';
import type { LandingHighlights } from '@/components/landing/landing-live-highlights';
import { LandingFounderSpotlight } from '@/components/landing/landing-founder-spotlight';
import type { PlatformStats } from '@/lib/api';

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/90 backdrop-blur-xl">
      <div className="mx-auto flex w-full max-w-[88rem] items-center justify-between gap-4 px-4 py-2.5 lg:px-8">
        <SiteBrand className="shrink-0 text-sm font-bold tracking-tight uppercase" />
        <SiteNav />
      </div>
    </header>
  );
}

export function LandingSinglePage({
  stats,
  highlights,
}: {
  stats: PlatformStats | null;
  highlights: LandingHighlights | null;
}) {
  const pendingReviews = stats?.projectsAwaitingReview ?? 12;

  return (
    <div className="mx-auto w-full max-w-[88rem] space-y-4 px-4 py-4 sm:px-6 lg:space-y-5 lg:px-8 lg:py-5">
      <LandingProjectsSection platformStats={stats} />

      <LandingHero topAgent={highlights?.topAgent ?? null} />

      <LandingPulseBar data={highlights} stats={stats} scoutPending={pendingReviews} />

      <LandingFunFactBar />

      <LandingFounderSpotlight />

      <LandingHubPreviews scoutPending={pendingReviews} platformStats={stats} compact />

      <LandingPlatformAdoption />

      <footer className="rounded-2xl border border-zinc-800/80 bg-zinc-950/50 px-4 py-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-lg font-bold text-white">80% community owned</p>
            <p className="text-[10px] text-zinc-500">10% airdrop · 70% over 10 years · Ollama + Founder Node — you own the inference bill</p>
          </div>
          <div className="flex flex-wrap gap-3 text-[10px] text-zinc-500">
            <Link href="/founder-node" className="font-semibold text-violet-300 hover:text-violet-200">
              Founder Node →
            </Link>
            <Link href="/mobile" className="font-semibold text-emerald-300 hover:text-emerald-200">
              Android app →
            </Link>
            <Link href="/privacy" className="hover:text-zinc-300">
              Privacy
            </Link>
            <Link href="/trust-center" className="hover:text-zinc-300">
              Trust Center
            </Link>
          </div>
        </div>
        <p className="mt-3 border-t border-zinc-800/60 pt-2 text-center text-[9px] text-zinc-600">
          © {new Date().getFullYear()} Doxxed Crypto · Trade founders. Not excuses.
        </p>
      </footer>
    </div>
  );
}
