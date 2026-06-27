'use client';

import Link from 'next/link';
import { formatUsd, STARTING_CASH_USD } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { LandingHubNavTable, LandingHubPreviews } from '@/components/landing/landing-feature-hub';
import { LandingPlatformAdoption } from '@/components/landing/landing-platform-adoption';
import { LandingFunFactBar } from '@/components/landing/landing-fun-fact-bar';
import { LandingHero } from '@/components/landing/landing-hero';
import { LandingLiveHighlights, type LandingHighlights } from '@/components/landing/landing-live-highlights';
import { LandingFounderSpotlight } from '@/components/landing/landing-founder-spotlight';
import type { PlatformStats } from '@/lib/api';

function formatStat(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return value.toLocaleString();
  return value.toLocaleString();
}

const WHY_DOXXED = [
  {
    title: 'The problem',
    body:
      'Anonymous founders, influencer pumps, and memecoin cycles extract liquidity from retail. Honest builders get lost in the same noise as the next shitcoin.',
    border: 'border-red-500/35',
    accent: 'text-red-300',
  },
  {
    title: 'Why now',
    body:
      'Crypto needs a trust layer again — public identity, documented shipping, and community validation before capital moves. Not hype charts. Execution.',
    border: 'border-amber-500/35',
    accent: 'text-amber-300',
  },
  {
    title: 'What we do',
    body:
      'Curate doxxed & build-in-public founders. Live BTC agent with verified PnL. Founder OS + Founder Node — your laptop is the compute. Fund shippers, not spectacles.',
    border: 'border-emerald-500/35',
    accent: 'text-emerald-300',
  },
] as const;

const PIPELINE = [
  { step: 'Build', tag: 'Transparency', desc: 'Connect GitHub. Ship in public.', border: 'border-violet-500/40', tagColor: 'text-violet-300' },
  { step: 'Validate', tag: 'Conviction', desc: 'Traders use DDollar. Scouts vote.', border: 'border-sky-500/40', tagColor: 'text-sky-300' },
  { step: 'Raise', tag: 'Community', desc: 'Find early believers. Simulated allocations.', border: 'border-amber-500/40', tagColor: 'text-amber-300' },
  { step: 'Launch', tag: 'Trust', desc: 'Launch when execution and demand are visible.', border: 'border-emerald-500/40', tagColor: 'text-emerald-300' },
];

const SHRIMP_FLOW = [
  { label: 'Join', sub: 'Create account', icon: '👤' },
  { label: `${formatUsd(STARTING_CASH_USD, 0)} DDollar`, sub: 'Free paper capital', icon: '🪙' },
  { label: 'Trade', sub: 'Paper trade any token', icon: '📈' },
  { label: 'Scout', sub: 'Vote on projects', icon: '🔭' },
  { label: 'Earn Reputation', sub: 'Build your score', icon: '⭐' },
  { label: 'Grow Followers', sub: 'Build your network', icon: '🌱' },
];

const DDOLLAR_USES = [
  { icon: '📈', label: 'Paper Trading' },
  { icon: '🎯', label: 'Predictions' },
  { icon: '🔭', label: 'Scout Votes' },
  { icon: '💰', label: 'DDollar Earned' },
  { icon: '🏆', label: 'Rewards' },
  { icon: '⚡', label: 'Platform Access' },
];

const NETWORK_RULES = [
  { icon: '🚫', title: 'No scams', sub: 'Proof over hype' },
  { icon: '🔨', title: 'Build in public', sub: 'Doxxed founders' },
  { icon: '👤', title: 'Face in public', sub: 'Verified identity' },
  { icon: '🛡', title: 'Verified humans', sub: 'Transparency first' },
  { icon: '📊', title: 'Proof over hype', sub: 'No paid shilling' },
  { icon: '👥', title: '100% community', sub: 'Closed system' },
  { icon: '💵', title: 'Paper first', sub: 'No outside money' },
  { icon: '✓', title: 'Fair game', sub: 'Skill over capital' },
];

const AI_PROVIDERS = ['DeepSeek', 'OpenAI', 'Claude', 'Gemini', 'OpenRouter', 'Ollama', 'Cursor', 'OpenHands'];

function Card({
  children,
  className = '',
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-zinc-800/90 bg-zinc-950/70 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] ${className}`}
    >
      {children}
    </div>
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

function PlatformStatsPanel({ stats }: { stats: PlatformStats | null }) {
  const pending = stats?.projectsAwaitingReview ?? 0;
  const items = [
    { label: 'Verified founders', value: stats ? formatStat(stats.verifiedFounders) : '127' },
    { label: 'Active projects', value: stats ? formatStat(stats.activeProjects) : '54' },
    { label: 'Projects awaiting review', value: stats ? formatStat(pending) : '12' },
    { label: 'Active investigations', value: stats ? formatStat(stats.activeInvestigations) : '3' },
    { label: 'GitHub commits', value: stats ? formatStat(stats.githubCommits) : '14k' },
    { label: 'Scout votes', value: stats ? formatStat(stats.scoutVotes) : '24k' },
    { label: 'DDollar distributed', value: stats ? formatStat(stats.ddollarDistributed) : '840k' },
    { label: 'Trades simulated', value: stats ? formatStat(stats.totalTrades) : '18k' },
    { label: 'DDollar in ecosystem', value: stats ? formatUsd(stats.simulatedCapital, 1) : '$3.2M' },
    { label: 'Community members', value: stats ? formatStat(stats.communityMembers) : '8.4k' },
  ];

  return (
    <Card className="h-full p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-zinc-500">Platform stats</p>
      <div className="mt-2.5 grid grid-cols-2 gap-2">
        {items.map((item) => (
          <div key={item.label} className="rounded-xl border border-zinc-800/80 bg-black/40 px-2.5 py-2.5">
            <p className="text-lg font-bold leading-none text-white">{item.value}</p>
            <p className="mt-1 text-[9px] uppercase tracking-wide text-zinc-500">{item.label}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

function FounderNodeProductCard() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card className="border-emerald-500/20 bg-gradient-to-br from-emerald-950/20 to-zinc-950/80 p-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">Android app</p>
        <ul className="mt-2.5 space-y-1 text-[11px] text-zinc-300">
          {['Agents & trading on phone', 'Founder OS Mission Control', 'Same login as the website'].map((line) => (
            <li key={line} className="flex gap-2">
              <span className="text-emerald-400">✓</span>
              {line}
            </li>
          ))}
        </ul>
        <Link href="/mobile" className="mt-2 inline-block text-xs font-semibold text-emerald-300 hover:underline">
          Download Android APK →
        </Link>
      </Card>
      <Card className="border-violet-500/20 bg-gradient-to-br from-violet-950/20 to-zinc-950/80 p-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">Founder Node</p>
        <ul className="mt-2.5 space-y-1 text-[11px] text-zinc-300">
          {[
            'Self-custody vault on your PC',
            'Ollama at localhost — $0 inference',
            'Founder OS syncs when you opt in',
          ].map((line) => (
            <li key={line} className="flex gap-2">
              <span className="text-violet-400">✓</span>
              {line}
            </li>
          ))}
        </ul>
        <Link href="/founder-node" className="mt-2 inline-block text-xs font-semibold text-violet-300 hover:underline">
          Download Founder Node →
        </Link>
        <p className="mt-0.5 text-[9px] text-zinc-600">Windows, macOS, Linux</p>
      </Card>
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

      <LandingLiveHighlights data={highlights} />

      <LandingFunFactBar />

      <LandingHubNavTable scoutPending={pendingReviews} />

      <LandingFounderSpotlight />

      <section className="grid gap-3 lg:grid-cols-[1fr_0.85fr]">
        <PlatformStatsPanel stats={stats} />
        <FounderNodeProductCard />
      </section>

      <LandingHubPreviews scoutPending={pendingReviews} platformStats={stats} />

      <section>
        <Card className="p-4 lg:p-5">
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-zinc-500">Why DoxxedCrypto.digital exists</p>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">
            We are here for tech, HODL conviction, and founders who ship in public — making crypto investing relevant
            again by backing builders who deliver, not influencers who pump.
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {WHY_DOXXED.map((card) => (
              <div key={card.title} className={`rounded-xl border bg-black/30 p-4 ${card.border}`}>
                <p className={`text-xs font-bold uppercase tracking-wider ${card.accent}`}>{card.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-zinc-300">{card.body}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>

      <section>
        <Card className="p-3">
          <p className="text-center text-[10px] font-bold uppercase tracking-[0.16em] text-zinc-400">
            Build in public · Validate demand · Launch with trust
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {PIPELINE.map((step) => (
              <div key={step.step} className={`rounded-xl border bg-black/30 p-2.5 ${step.border}`}>
                <p className={`text-[9px] font-bold uppercase tracking-wider ${step.tagColor}`}>{step.tag}</p>
                <p className="mt-0.5 text-sm font-bold text-white">{step.step}</p>
                <p className="mt-0.5 text-[10px] leading-snug text-zinc-500">{step.desc}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <Card className="border-amber-500/15 bg-gradient-to-br from-amber-950/15 to-transparent p-3">
          <h2 className="text-sm font-bold uppercase tracking-wide text-white">Built for shrimps, not whales</h2>
          <p className="mt-0.5 text-[11px] text-zinc-500">Everyone starts equal. Skill over capital.</p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
            {SHRIMP_FLOW.map((step, i) => (
              <div key={step.label} className="relative">
                <div className="rounded-xl border border-violet-500/25 bg-violet-950/30 p-2 text-center">
                  <span className="text-base" aria-hidden>
                    {step.icon}
                  </span>
                  <p className="mt-1 text-[10px] font-semibold text-violet-100">{step.label}</p>
                  <p className="text-[8px] text-zinc-500">{step.sub}</p>
                </div>
                {i < SHRIMP_FLOW.length - 1 && (
                  <span className="absolute -right-1 top-1/2 hidden -translate-y-1/2 text-zinc-600 xl:inline">
                    →
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-3">
          <div className="flex gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">The DDollar ecosystem</p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-400">
                Ecosystem currency for platform participation only — no intrinsic value, not redeemable for cash, not
                an investment product.
              </p>
              <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                {DDOLLAR_USES.map((item) => (
                  <div
                    key={item.label}
                    className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-black/30 px-2 py-1.5"
                  >
                    <span aria-hidden>{item.icon}</span>
                    <span className="text-[10px] text-zinc-300">{item.label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div
              className="flex h-20 w-20 shrink-0 items-center justify-center self-center rounded-full border-2 border-violet-500/50 bg-gradient-to-br from-violet-600 to-indigo-900 text-3xl font-bold text-white shadow-[0_0_40px_rgba(139,92,246,0.5)]"
              aria-hidden
            >
              D
            </div>
          </div>
        </Card>
      </section>

      <Card className="p-3">
        <p className="text-center text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
          Rules of the network
        </p>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {NETWORK_RULES.map((rule) => (
            <div
              key={rule.title}
              className="flex flex-col items-center rounded-xl border border-zinc-800/80 bg-black/25 px-2 py-3 text-center"
            >
              <span className="text-xl" aria-hidden>
                {rule.icon}
              </span>
              <span className="mt-1 text-[10px] font-semibold text-zinc-200">{rule.title}</span>
              <span className="text-[9px] text-zinc-500">{rule.sub}</span>
            </div>
          ))}
        </div>
      </Card>

      <LandingPlatformAdoption />

      <section className="grid gap-2 sm:grid-cols-3">
        <Card className="border-violet-500/20 bg-violet-950/20 p-3">
          <p className="text-3xl font-bold text-white">80%</p>
          <p className="text-xs font-semibold text-violet-200">Community owned</p>
          <ul className="mt-1.5 space-y-0.5 text-[10px] text-zinc-400">
            <li>10% Airdropped</li>
            <li>70% Distributed over 10 years</li>
            <li>20% Team allocation</li>
          </ul>
        </Card>
        <Card className="flex flex-col justify-center p-3 text-center">
          <p className="text-[9px] uppercase tracking-wider text-zinc-500">Local compute first</p>
          <p className="mt-1 text-sm font-semibold text-emerald-300">Ollama · Founder Node · you own the bill</p>
        </Card>
        <Card className="p-3">
          <p className="text-[9px] uppercase tracking-wider text-zinc-500">Use the AI you trust</p>
          <p className="mt-1 text-sm font-semibold text-sky-300">We orchestrate the rest</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {AI_PROVIDERS.map((name) => (
              <span
                key={name}
                className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300"
              >
                {name}
              </span>
            ))}
          </div>
        </Card>
      </section>

      <footer className="border-t border-zinc-800/80 pt-2 text-center text-[9px] text-zinc-600">
        © {new Date().getFullYear()} Doxxed Crypto · Trade founders. Not excuses. ·{' '}
        <Link href="/privacy" className="hover:text-zinc-400">
          Privacy
        </Link>
        {' · '}
        <Link href="/trust-center" className="hover:text-zinc-400">
          Trust Center
        </Link>
      </footer>
    </div>
  );
}
