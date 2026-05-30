'use client';

import Link from 'next/link';
import { SiteNav, SiteBrand } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { ProjectSpotlight } from '@/components/landing/project-spotlight';
import {
  LandingHero,
  LandingLiveMetrics,
  LandingLiveActivity,
  LandingFourPhases,
  LandingEconomy,
  LandingTrustSecurity,
  LandingFinalCta,
} from '@/components/landing/landing-sections';
import {
  fetchFeaturedProjects,
  fetchPlatformStats,
  fetchSpotlightProjects,
  PlatformStats,
  ProjectSummary,
  SpotlightProject,
} from '@/lib/api';
import { useEffect, useState } from 'react';

export function LandingPage() {
  const [featured, setFeatured] = useState<ProjectSummary[]>([]);
  const [spotlight, setSpotlight] = useState<SpotlightProject[]>([]);
  const [platformStats, setPlatformStats] = useState<PlatformStats | null>(null);

  useEffect(() => {
    fetchFeaturedProjects().then(setFeatured).catch(() => setFeatured([]));
    fetchSpotlightProjects().then(setSpotlight).catch(() => setSpotlight([]));
    fetchPlatformStats().then(setPlatformStats).catch(() => setPlatformStats(null));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.25em] text-blue-500/80">
              Founder OS
            </p>
            <SiteBrand />
          </div>
          <SiteNav />
        </div>
      </header>

      <LandingHero />
      <LandingLiveMetrics stats={platformStats} />
      <LandingFourPhases />
      <LandingEconomy />
      <LandingLiveActivity />

      {spotlight.length > 0 && (
        <section className="border-y border-zinc-800/80 py-16">
          <div className="mx-auto max-w-6xl px-6">
            <ProjectSpotlight projects={spotlight} />
          </div>
        </section>
      )}

      <LandingTrustSecurity />

      {featured.length > 0 && (
        <section className="border-t border-zinc-800/80 py-16">
          <div className="mx-auto max-w-6xl px-6">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-blue-500/90">
                  Launch phase
                </p>
                <h2 className="mt-2 text-2xl font-bold">Projects building in public</h2>
              </div>
              <Link href="/projects" className="text-sm text-blue-400 hover:underline">
                View all →
              </Link>
            </div>
            <div className="mt-8 flex gap-4 overflow-x-auto pb-2">
              {featured.map((project) => (
                <div key={project.slug} className="w-[min(100%,320px)] shrink-0">
                  <ProjectCard project={project} />
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <LandingFinalCta />
    </main>
  );
}
