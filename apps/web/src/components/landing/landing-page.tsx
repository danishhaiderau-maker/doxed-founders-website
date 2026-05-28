'use client';

import Link from 'next/link';
import { SiteNav } from '@/components/site-nav';
import { ProjectCard } from '@/components/project-card';
import { ProjectSpotlight } from '@/components/landing/project-spotlight';
import {
  fetchFeaturedProjects,
  fetchSpotlightProjects,
  ProjectSummary,
  SpotlightProject,
} from '@/lib/api';
import { useEffect, useState } from 'react';

export function LandingPage() {
  const [featured, setFeatured] = useState<ProjectSummary[]>([]);
  const [spotlight, setSpotlight] = useState<SpotlightProject[]>([]);

  useEffect(() => {
    fetchFeaturedProjects().then(setFeatured).catch(() => setFeatured([]));
    fetchSpotlightProjects().then(setSpotlight).catch(() => setSpotlight([]));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]/80 bg-[#050508]/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div>
            <p className="text-xs uppercase tracking-[0.25em] text-emerald-400/80">
              The reputation layer for crypto
            </p>
            <h1 className="text-xl font-semibold tracking-tight">DoxedCryptoFounder</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-[var(--color-border)]/60">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(16,185,129,0.12)_0%,_transparent_55%)]" />
        <div className="relative mx-auto max-w-6xl px-6 py-20 md:py-28">
          <p className="text-sm font-medium uppercase tracking-widest text-amber-300/90">
            Talent over deep pockets.
          </p>
          <h2 className="mt-4 max-w-3xl text-4xl font-bold leading-tight tracking-tight md:text-6xl">
            Would you send money to a stranger?
            <span className="mt-2 block text-emerald-400">
              Then why buy undoxxed coins?
            </span>
          </h2>
          <p className="mt-6 max-w-2xl text-lg text-[var(--color-muted)]">
            Crypto rewards whales and influencers. We reward{' '}
            <strong className="text-white">skill, scouting, and conviction</strong>. Paper trade
            with $10,000 virtual cash — no wallet size required. Sign in with X and show your
            track record in public.
          </p>
          <div className="mt-10 flex flex-wrap gap-4">
            <Link
              href="/projects"
              className="rounded-lg bg-emerald-500 px-6 py-3 text-sm font-semibold text-black hover:bg-emerald-400"
            >
              Explore projects
            </Link>
            <Link
              href="/register"
              className="rounded-lg border border-amber-500/50 px-6 py-3 text-sm font-semibold text-amber-200 hover:border-amber-400"
            >
              Start earning points
            </Link>
            <Link
              href="/paper-trading"
              className="rounded-lg border border-[var(--color-border)] px-6 py-3 text-sm font-medium text-white hover:border-emerald-400"
            >
              Paper trade — $10,000
            </Link>
          </div>
          <p className="mt-6 text-xs text-[var(--color-muted)]">
            Don&apos;t trust hype. Trust reputation. · Blow $10k paper?{' '}
            <Link href="/busted" className="text-red-300/90 hover:underline">
              Poor judgment hall
            </Link>{' '}
            · Season 1 beta
          </p>
        </div>
      </section>

      {/* Paper trading — talent not wallets */}
      <section className="border-b border-[var(--color-border)]/60 bg-gradient-to-b from-emerald-950/15 to-transparent py-16">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h3 className="text-2xl font-bold md:text-3xl">
            Deep pockets don&apos;t make you right.
          </h3>
          <p className="mx-auto mt-4 max-w-2xl text-[var(--color-muted)]">
            Paper trading proves who can actually pick doxxed founders — before real money is on the
            line. Sign in with X. Post your thesis. Win reputation. Or join the hall of shame.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            {[
              ['Talent > capital', 'Everyone starts with $10,000 paper. Same playing field.'],
              ['Sign in with X', 'Your handle ties to your public portfolio — wins and wipes.'],
              ['Scout & earn', 'Find doxxed gems first. Top scouts earn 1,000+ reputation pts.'],
            ].map(([title, body]) => (
              <div
                key={title}
                className="rounded-xl border border-emerald-500/20 bg-black/30 px-4 py-5 text-left"
              >
                <p className="font-semibold text-emerald-300">{title}</p>
                <p className="mt-2 text-sm text-[var(--color-muted)]">{body}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            <Link
              href="/login?callbackUrl=/paper-trading"
              className="rounded-lg bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-zinc-100"
            >
              Sign in with X — paper trade
            </Link>
            <Link
              href="/busted"
              className="rounded-lg border border-red-500/40 px-6 py-3 text-sm text-red-200 hover:border-red-400"
            >
              Who blew $10k? →
            </Link>
          </div>
        </div>
      </section>

      {/* Spotlight */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <ProjectSpotlight projects={spotlight} />
      </section>

      {/* Problem */}
      <section className="border-y border-[var(--color-border)]/60 bg-[var(--color-card)]/30 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h3 className="text-2xl font-bold md:text-3xl">Crypto rewards hype. Not transparency.</h3>
          <p className="mt-4 max-w-3xl text-[var(--color-muted)]">
            Anonymous founders launch tokens overnight. Influencers shill for attention. Retail gets
            exit liquidity. We think crypto deserves better — a belief layer where signal beats
            noise.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {['Rug pulls', 'Anonymous teams', 'Paid shills', 'Fake hype'].map((item) => (
              <div
                key={item}
                className="rounded-xl border border-red-500/20 bg-red-950/10 px-4 py-3 text-sm text-red-200/90"
              >
                {item}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Solution + gamification */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <h3 className="text-2xl font-bold">Introducing the belief layer</h3>
            <p className="mt-4 text-[var(--color-muted)]">
              A community-driven platform to discover, evaluate, and rank projects based on founder
              transparency, team credibility, product quality, and long-term execution.
            </p>
            <ul className="mt-6 space-y-2 text-sm text-white/90">
              {[
                'Founder transparency & public interviews',
                'Curated listings with verification scores',
                'Paper trading with live DexScreener prices',
                'Social feed for theses and trade discussion',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-emerald-400">✓</span> {item}
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-2xl border border-amber-500/25 bg-gradient-to-br from-amber-950/20 to-transparent p-6">
            <h4 className="text-lg font-semibold text-amber-200">Earn reputation before capital</h4>
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              Submit projects, post theses, paper trade, comment on feed — every action earns points
              on your public profile.
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 text-center text-xs">
              {[
                ['+1,000', 'Scout approved'],
                ['+50', 'Sign up'],
                ['+15', 'Vote on scout'],
                ['+10', 'Paper trade'],
              ].map(([pts, label]) => (
                <div key={label} className="rounded-lg bg-black/30 py-3">
                  <div className="text-lg font-bold text-amber-300">{pts}</div>
                  <div className="text-[var(--color-muted)]">{label}</div>
                </div>
              ))}
            </div>
            <Link
              href="/reputation"
              className="mt-4 inline-block text-sm text-amber-300/90 hover:text-amber-200"
            >
              See full points & vote math →
            </Link>
          </div>
        </div>
      </section>

      {/* Featured */}
      {featured.length > 0 && (
        <section className="border-t border-[var(--color-border)]/60 py-16">
          <div className="mx-auto max-w-6xl px-6">
            <h3 className="text-sm font-semibold uppercase tracking-widest text-emerald-400/90">
              Verified founders
            </h3>
            <div className="mt-6 flex gap-4 overflow-x-auto pb-2">
              {featured.map((project) => (
                <div key={project.slug} className="w-[min(100%,320px)] shrink-0">
                  <ProjectCard project={project} />
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* CTA */}
      <section className="border-t border-[var(--color-border)]/60 py-20">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h3 className="text-3xl font-bold">Help make crypto trustworthy again</h3>
          <p className="mt-4 text-[var(--color-muted)]">
            Find the best founders. Surface the best projects. Reward real conviction.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            <Link
              href="/register"
              className="rounded-lg bg-emerald-500 px-8 py-3 font-semibold text-black hover:bg-emerald-400"
            >
              Join beta
            </Link>
            <Link
              href="/list-your-project"
              className="rounded-lg border border-[var(--color-border)] px-8 py-3 font-medium text-white hover:border-emerald-400"
            >
              List your project — free
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
