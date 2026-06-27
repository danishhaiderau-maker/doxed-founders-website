'use client';

import Link from 'next/link';
import { formatUsd, STARTING_CASH_USD } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { LandingHubPreviews } from '@/components/landing/landing-feature-hub';
import { LandingPlatformAdoption } from '@/components/landing/landing-platform-adoption';
import { LandingFunFactBar } from '@/components/landing/landing-fun-fact-bar';
import { LandingHero } from '@/components/landing/landing-hero';
import { LandingPulseBar } from '@/components/landing/landing-pulse-bar';
import type { LandingHighlights } from '@/components/landing/landing-live-highlights';
import { LandingFounderSpotlight } from '@/components/landing/landing-founder-spotlight';
import type { PlatformStats } from '@/lib/api';

function formatStat(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return value.toLocaleString();
  return value.toLocaleString();
}

const PIPELINE = [
  { step: 'Build', desc: 'Ship in public · GitHub connected', border: 'border-violet-500/35', accent: 'text-violet-300' },
  { step: 'Validate', desc: 'DDollar conviction · scout votes', border: 'border-sky-500/35', accent: 'text-sky-300' },
  { step: 'Raise', desc: 'Community believers · simulated raise', border: 'border-amber-500/35', accent: 'text-amber-300' },
  { step: 'Launch', desc: 'Trust visible · execution first', border: 'border-emerald-500/35', accent: 'text-emerald-300' },
] as const;

const SHRIMP_FLOW = [
  { label: 'Join', icon: '👤' },
  { label: formatUsd(STARTING_CASH_USD, 0), icon: '🪙' },
  { label: 'Trade', icon: '📈' },
  { label: 'Scout', icon: '🔭' },
  { label: 'Earn', icon: '⭐' },
  { label: 'Grow', icon: '🌱' },
] as const;

const RULE_CHIPS = ['Doxxed founders', 'Paper first', 'No paid shills', 'Proof over hype', 'Skill over capital'] as const;

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border border-zinc-800/90 bg-zinc-950/70 ${className}`}>{children}</div>
  );
}

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

function ProofStrip({ stats }: { stats: PlatformStats | null }) {
  const items = [
    { label: 'Founders', value: stats ? formatStat(stats.verifiedFounders) : '127' },
    { label: 'Projects', value: stats ? formatStat(stats.activeProjects) : '54' },
    { label: 'Trades', value: stats ? formatStat(stats.totalTrades) : '18k' },
    { label: 'Scout votes', value: stats ? formatStat(stats.scoutVotes) : '24k' },
    { label: 'Commits', value: stats ? formatStat(stats.githubCommits) : '14k' },
    { label: 'Members', value: stats ? formatStat(stats.communityMembers) : '8.4k' },
  ];

  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {items.map((item) => (
        <div key={item.label} className="rounded-lg border border-zinc-800/70 bg-black/30 px-2 py-2 text-center">
          <p className="text-sm font-bold text-white">{item.value}</p>
          <p className="text-[8px] uppercase tracking-wide text-zinc-500">{item.label}</p>
        </div>
      ))}
    </div>
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
      <LandingHero scoutPending={pendingReviews} topAgent={highlights?.topAgent ?? null} />

      <LandingPulseBar data={highlights} stats={stats} scoutPending={pendingReviews} />

      <LandingFunFactBar />

      <LandingFounderSpotlight />

      <LandingHubPreviews scoutPending={pendingReviews} platformStats={stats} compact />

      <Card className="p-4">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">How conviction becomes launch</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map((step) => (
            <div key={step.step} className={`rounded-xl border bg-black/25 px-3 py-2.5 ${step.border}`}>
              <p className={`text-[9px] font-bold uppercase tracking-wider ${step.accent}`}>{step.step}</p>
              <p className="mt-1 text-[11px] leading-snug text-zinc-400">{step.desc}</p>
            </div>
          ))}
        </div>
        <div className="mt-4 border-t border-zinc-800/60 pt-4">
          <ProofStrip stats={stats} />
        </div>
      </Card>

      <Card className="border-amber-500/10 bg-gradient-to-br from-amber-950/10 to-transparent p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-bold text-white">Built for shrimps, not whales</h2>
            <p className="mt-0.5 text-[11px] text-zinc-500">Everyone starts with free DDollar. Skill over capital.</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {RULE_CHIPS.map((rule) => (
              <span
                key={rule}
                className="rounded-full border border-zinc-700/80 bg-zinc-900/60 px-2.5 py-1 text-[10px] font-medium text-zinc-400"
              >
                {rule}
              </span>
            ))}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
          {SHRIMP_FLOW.map((step, i) => (
            <div key={step.label} className="flex items-center gap-2">
              <div className="flex flex-col items-center rounded-lg border border-violet-500/20 bg-violet-950/20 px-3 py-2">
                <span aria-hidden>{step.icon}</span>
                <span className="mt-1 text-[10px] font-semibold text-violet-100">{step.label}</span>
              </div>
              {i < SHRIMP_FLOW.length - 1 ? (
                <span className="hidden text-zinc-600 sm:inline" aria-hidden>
                  →
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </Card>

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
