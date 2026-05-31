'use client';

import Link from 'next/link';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { LandingWorkflowStrip } from '@/components/landing/landing-workflow-strip';
import {
  LandingMockupCommunity,
  LandingMockupEconomy,
  LandingMockupFooter,
  LandingMockupHero,
  LandingMockupLiveHub,
  LandingMockupRules,
  LandingMockupStackRow,
  LandingMockupStats,
} from '@/components/landing/landing-mockup-sections';
import {
  fetchPlatformStats,
  fetchSpotlightProjects,
  PlatformStats,
  SpotlightProject,
} from '@/lib/api';
import { useEffect, useState } from 'react';

export function LandingPage() {
  const [spotlight, setSpotlight] = useState<SpotlightProject[]>([]);
  const [platformStats, setPlatformStats] = useState<PlatformStats | null>(null);

  useEffect(() => {
    fetchSpotlightProjects().then(setSpotlight).catch(() => setSpotlight([]));
    fetchPlatformStats().then(setPlatformStats).catch(() => setPlatformStats(null));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-10">
          <SiteBrand className="text-sm" />
          <SiteNav />
        </div>
      </header>

      <LandingMockupHero />
      <LandingMockupStats stats={platformStats} />
      <LandingWorkflowStrip />
      <LandingMockupEconomy />
      <LandingMockupLiveHub spotlight={spotlight} />
      <LandingMockupStackRow />
      <LandingMockupRules />
      <LandingMockupCommunity />

      <section className="border-b border-zinc-800/80 py-10">
        <div className="mx-auto flex w-full max-w-[90rem] flex-wrap justify-center gap-3 px-4 sm:px-6 lg:px-10">
          <Link
            href="/founder-den"
            className="rounded-xl bg-violet-600 px-8 py-3.5 text-sm font-semibold text-white hover:bg-violet-500"
          >
            Open Founder OS →
          </Link>
          <Link
            href="/list-your-project"
            className="rounded-xl border border-zinc-600 px-8 py-3.5 text-sm font-semibold text-white hover:border-violet-500/50"
          >
            List your project
          </Link>
        </div>
      </section>

      <LandingMockupFooter />
    </main>
  );
}
