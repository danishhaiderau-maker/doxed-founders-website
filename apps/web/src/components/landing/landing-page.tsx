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
              Proof of Conviction
            </p>
            <h1 className="text-xl font-semibold tracking-tight">Doxxed crypto</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      {/* 1. Hero */}
      <section className="relative overflow-hidden border-b border-[var(--color-border)]/60">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(16,185,129,0.12)_0%,_transparent_55%)]" />
        <div className="relative mx-auto max-w-6xl px-6 py-20 md:py-28">
          <p className="text-sm font-medium uppercase tracking-widest text-amber-300/90">
            Conviction over capital · Reputation is the new alpha
          </p>
          <h2 className="mt-4 max-w-3xl text-4xl font-bold leading-tight tracking-tight md:text-6xl">
            Would you send money to a stranger?
            <span className="mt-2 block text-emerald-400">
              Then why buy undoxxed coins?
            </span>
          </h2>
          <p className="mt-6 max-w-2xl text-lg text-[var(--color-muted)]">
            Crypto should reward skill, conviction, and reputation — not insiders and deep pockets.
            Build your public track record before you risk capital. Trust earned. Not bought.
          </p>
          <div className="mt-10 flex flex-wrap gap-4">
            <Link
              href="/login?callbackUrl=/paper-trading"
              className="rounded-lg bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-zinc-100"
            >
              Join the leaderboard
            </Link>
            <Link
              href="/projects"
              className="rounded-lg bg-emerald-500 px-6 py-3 text-sm font-semibold text-black hover:bg-emerald-400"
            >
              Explore verified founders
            </Link>
            <Link
              href="/reputation"
              className="rounded-lg border border-[var(--color-border)] px-6 py-3 text-sm font-medium text-white hover:border-emerald-400"
            >
              How reputation works
            </Link>
          </div>
        </div>
      </section>

      {/* 2. Problem */}
      <section className="border-b border-[var(--color-border)]/60 bg-[var(--color-card)]/30 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h3 className="text-2xl font-bold md:text-3xl">Crypto rewards hype. Not skill.</h3>
          <p className="mt-4 max-w-3xl text-[var(--color-muted)]">
            Anonymous founders, paid shills, and deleted tweets. Retail gets exit liquidity while
            influencers flex wallets they never had to prove. The market needs a merit layer — not
            another memecoin casino.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {['Anonymous teams', 'Influencer scams', 'Fake conviction', 'No track record'].map(
              (item) => (
                <div
                  key={item}
                  className="rounded-xl border border-red-500/20 bg-red-950/10 px-4 py-3 text-sm text-red-200/90"
                >
                  {item}
                </div>
              ),
            )}
          </div>
        </div>
      </section>

      {/* 3. Solution — Proof of Conviction */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-400">
          The Proof of Conviction layer
        </p>
        <h3 className="mt-3 text-2xl font-bold md:text-3xl">
          Onchain transparency meets measurable reputation
        </h3>
        <p className="mt-4 max-w-3xl text-[var(--color-muted)]">
          Discover doxxed founders. Paper trade with live prices. Post theses. Scout listings.
          Every action compounds your public reputation — like LinkedIn for crypto conviction,
          with the rigor of a terminal and the engagement of a performance league.
        </p>
        <ul className="mt-8 grid gap-3 sm:grid-cols-2">
          {[
            'Verified founder discovery & verification scores',
            'Paper trading — prove your edge before real capital',
            'Scout vote board — community filters before admin review',
            'Public portfolios, leaderboards, and reputation points',
          ].map((item) => (
            <li key={item} className="flex gap-2 text-sm text-white/90">
              <span className="text-emerald-400">✓</span> {item}
            </li>
          ))}
        </ul>
      </section>

      {/* Spotlight */}
      <section className="mx-auto max-w-6xl border-t border-[var(--color-border)]/60 px-6 py-16">
        <ProjectSpotlight projects={spotlight} />
      </section>

      {/* 4. Paper trading */}
      <section className="border-y border-[var(--color-border)]/60 bg-gradient-to-b from-emerald-950/15 to-transparent py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h3 className="text-center text-2xl font-bold md:text-3xl">
            No capital required. Only conviction.
          </h3>
          <p className="mx-auto mt-4 max-w-2xl text-center text-[var(--color-muted)]">
            Paper trade. Prove yourself. Earn reputation. Everyone starts with $10,000 virtual
            cash — the best traders aren&apos;t always the richest.
          </p>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {[
              ['Risk nothing. Build everything.', 'Same starting balance. Performance is public.'],
              ['Track every thesis. Every win. Every loss.', 'Feed posts tie trades to your reasoning.'],
              ['The market remembers good calls.', 'Leaderboard ranks by portfolio value and ROI.'],
            ].map(([title, body]) => (
              <div
                key={title}
                className="rounded-xl border border-emerald-500/20 bg-black/30 p-5"
              >
                <p className="font-semibold text-emerald-200">{title}</p>
                <p className="mt-2 text-sm text-[var(--color-muted)]">{body}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            <Link
              href="/paper-trading"
              className="rounded-lg bg-emerald-500 px-6 py-3 text-sm font-semibold text-black hover:bg-emerald-400"
            >
              Paper trade — $10,000
            </Link>
            <Link
              href="/leaderboard"
              className="rounded-lg border border-[var(--color-border)] px-6 py-3 text-sm hover:border-emerald-400"
            >
              View leaderboard
            </Link>
          </div>
        </div>
      </section>

      {/* 5. X identity */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <h3 className="text-2xl font-bold">Sign in with X. Build your crypto reputation.</h3>
            <p className="mt-4 text-[var(--color-muted)]">
              Crypto-native identity, instant onboarding, and a public layer people can actually
              trust. Link your handle to your portfolio, scout record, and prediction history —
              conviction you can verify.
            </p>
            <Link
              href="/login?callbackUrl=/paper-trading"
              className="mt-6 inline-block rounded-lg bg-white px-6 py-3 text-sm font-semibold text-black hover:bg-zinc-100"
            >
              Continue with X
            </Link>
          </div>
          <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)]/50 p-6 text-sm">
            <p className="font-medium text-white">What gets tracked on your profile</p>
            <ul className="mt-4 space-y-2 text-[var(--color-muted)]">
              <li>· Paper trading P&amp;L and ROI</li>
              <li>· Reputation points &amp; contributor level</li>
              <li>· Scout submissions &amp; community votes</li>
              <li>· Feed theses and trade commentary</li>
            </ul>
          </div>
        </div>
      </section>

      {/* 6. Transparency */}
      <section className="border-y border-[var(--color-border)]/60 bg-[var(--color-card)]/20 py-16">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h3 className="text-2xl font-bold md:text-3xl">
            Everyone has opinions. Few have track records.
          </h3>
          <p className="mx-auto mt-4 max-w-2xl text-[var(--color-muted)]">
            Every prediction, thesis, and paper trade is tracked publicly. Win consistently — build
            influence. Underperform — your record stays visible. No fake gurus. No hindsight
            experts. No deleted tweets.
          </p>
          <Link
            href="/busted"
            className="mt-6 inline-block text-sm text-emerald-400 hover:underline"
          >
            Transparent track records — view performance history →
          </Link>
        </div>
      </section>

      {/* 7. Rewards — not airdrop framing */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <h3 className="text-2xl font-bold">Built for long-term contributors</h3>
            <p className="mt-4 text-[var(--color-muted)]">
              The platform rewards users who contribute signal, insight, and conviction over time —
              not short-term farming. Points reflect real participation. Top scouts, analysts, and
              traders build influence that may inform future ecosystem rewards and governance.
            </p>
            <Link href="/reputation" className="mt-4 inline-block text-sm text-amber-300 hover:underline">
              Full points breakdown →
            </Link>
          </div>
          <div className="rounded-2xl border border-amber-500/25 bg-gradient-to-br from-amber-950/20 to-transparent p-6">
            <h4 className="text-lg font-semibold text-amber-200">Earn reputation before capital</h4>
            <div className="mt-4 grid grid-cols-2 gap-3 text-center text-xs">
              {[
                ['+1,000', 'Verified scout'],
                ['+50', 'Join platform'],
                ['+15', 'Community vote'],
                ['+10', 'Paper trade'],
              ].map(([pts, label]) => (
                <div key={label} className="rounded-lg bg-black/30 py-3">
                  <div className="text-lg font-bold text-amber-300">{pts}</div>
                  <div className="text-[var(--color-muted)]">{label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 8. Tokenomics — illustrative, professional */}
      <section className="border-t border-[var(--color-border)]/60 bg-black/40 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h3 className="text-2xl font-bold">Long-term alignment</h3>
          <p className="mt-3 max-w-2xl text-sm text-[var(--color-muted)]">
            Illustrative framework for ecosystem sustainability — not a guarantee of value.
            Reputation-based rewards and performance-driven allocation designed for contributors,
            not speculators.
          </p>
          <div className="mt-10 grid gap-8 lg:grid-cols-2">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
                Token allocation (example)
              </p>
              <ul className="mt-4 space-y-3">
                {[
                  ['60%', 'Community & user rewards'],
                  ['20%', 'Team & development (vested)'],
                  ['10%', 'Early supporters'],
                  ['10%', 'Ecosystem & liquidity'],
                ].map(([pct, label]) => (
                  <li
                    key={label}
                    className="flex items-center justify-between rounded-lg border border-[var(--color-border)] px-4 py-3 text-sm"
                  >
                    <span>{label}</span>
                    <span className="font-semibold text-emerald-400">{pct}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-amber-400/90">
                Platform economics (example)
              </p>
              <ul className="mt-4 space-y-3 text-sm text-[var(--color-muted)]">
                <li className="rounded-lg border border-[var(--color-border)] px-4 py-3">
                  Quarterly community distributions tied to contribution tiers
                </li>
                <li className="rounded-lg border border-[var(--color-border)] px-4 py-3">
                  10-year vesting model for team alignment
                </li>
                <li className="rounded-lg border border-[var(--color-border)] px-4 py-3">
                  Reputation-weighted reward mechanics
                </li>
                <li className="rounded-lg border border-[var(--color-border)] px-4 py-3">
                  Portion of platform revenue allocated to buyback &amp; burn mechanisms for
                  ecosystem sustainability
                </li>
              </ul>
            </div>
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

      {/* 9. CTA */}
      <section className="border-t border-[var(--color-border)]/60 py-20">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h3 className="text-3xl font-bold">Build Proof of Conviction</h3>
          <p className="mt-4 text-[var(--color-muted)]">
            Discover alpha. Build reputation. Turn conviction into influence. The future belongs to
            contributors.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            <Link
              href="/register"
              className="rounded-lg bg-emerald-500 px-8 py-3 font-semibold text-black hover:bg-emerald-400"
            >
              Join beta
            </Link>
            <Link
              href="/leaderboard"
              className="rounded-lg border border-[var(--color-border)] px-8 py-3 font-medium text-white hover:border-emerald-400"
            >
              Join the leaderboard
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
