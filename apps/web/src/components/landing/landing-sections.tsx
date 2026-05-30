'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { formatUsd, TOP_UP_FEE_USD, STARTING_CASH_USD } from '@dcf/utils';
import type { PlatformActivityItem, PlatformStats } from '@/lib/api';
import { fetchPlatformActivity } from '@/lib/api';

function activityTimeAgo(iso: string) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'Yesterday';
  return `${days}d ago`;
}

function activityIcon(kind: PlatformActivityItem['kind']) {
  if (kind === 'build') return '🔨';
  if (kind === 'video') return '🎥';
  return '💰';
}

export function LandingHero() {
  const features = [
    { icon: '🔨', label: 'Build in Public' },
    { icon: '📈', label: 'Validate Demand' },
    { icon: '⭐', label: 'Earn Reputation' },
    { icon: '🌐', label: 'Own the Network' },
  ];

  return (
    <section className="relative overflow-hidden border-b border-zinc-800/80">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,rgba(139,92,246,0.18),transparent)]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_40%_at_80%_20%,rgba(59,130,246,0.08),transparent)]" />
      <div className="relative mx-auto grid max-w-6xl gap-12 px-6 py-16 md:grid-cols-2 md:py-24">
        <div>
          <p className="inline-flex items-center gap-2 rounded-full border border-violet-500/40 bg-violet-950/40 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-violet-300">
            Community-owned platform
          </p>
          <h1 className="mt-6 text-4xl font-bold leading-[1.08] tracking-tight text-white md:text-5xl lg:text-6xl">
            Build in public. Validate demand.{' '}
            <span className="bg-gradient-to-r from-violet-400 via-fuchsia-400 to-pink-400 bg-clip-text text-transparent">
              Launch with trust.
            </span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-zinc-400">
            The operating system for crypto startups — where founders ship in public, traders prove
            conviction with paper capital, and{' '}
            <strong className="font-medium text-zinc-200">80% of the network belongs to the community</strong>.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/founder-den"
              className="rounded-xl bg-violet-600 px-6 py-3.5 text-sm font-semibold text-white transition hover:bg-violet-500"
            >
              Open Founder OS →
            </Link>
            <Link
              href="/discover"
              className="rounded-xl border border-zinc-600 bg-zinc-900/50 px-6 py-3.5 text-sm font-semibold text-white transition hover:border-violet-500/50"
            >
              Discover projects
            </Link>
          </div>
          <div className="mt-8 flex flex-wrap gap-4 border-t border-zinc-800/80 pt-6">
            {features.map((f) => (
              <span
                key={f.label}
                className="inline-flex items-center gap-2 text-xs font-medium text-zinc-400"
              >
                <span aria-hidden>{f.icon}</span>
                {f.label}
              </span>
            ))}
          </div>
        </div>

        <div className="relative flex items-center justify-center">
          <div className="relative w-full max-w-md">
            <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-violet-600/20 via-blue-600/10 to-emerald-600/20 blur-3xl" />
            <div className="relative rounded-2xl border border-zinc-700/80 bg-zinc-900/60 p-8 backdrop-blur-sm">
              <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-600 text-3xl font-bold text-white shadow-lg shadow-violet-900/40">
                E
              </div>
              <div className="mt-8 grid grid-cols-2 gap-3 text-sm">
                {[
                  { step: 'BUILD', color: 'text-emerald-400', border: 'border-emerald-500/30' },
                  { step: 'VALIDATE', color: 'text-blue-400', border: 'border-blue-500/30' },
                  { step: 'RAISE', color: 'text-pink-400', border: 'border-pink-500/30' },
                  { step: 'LAUNCH', color: 'text-amber-400', border: 'border-amber-500/30' },
                ].map((s) => (
                  <div
                    key={s.step}
                    className={`rounded-xl border ${s.border} bg-zinc-950/50 px-3 py-2.5 text-center font-semibold ${s.color}`}
                  >
                    {s.step}
                  </div>
                ))}
              </div>
              <p className="mt-4 text-center text-xs text-zinc-500">
                GitHub → validate → raise → launch — one founder workflow
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const FOUR_PHASES = [
  {
    step: '01',
    title: 'Build',
    color: 'emerald',
    summary: 'Connect GitHub. Ship in public. Founder Node keeps your vault private on your disk.',
    bullets: ['GitHub → translate → build feed', 'Founder Copilot & agents', 'Founder Node private vault'],
    href: '/founder-den',
    cta: 'Start building',
  },
  {
    step: '02',
    title: 'Validate',
    color: 'blue',
    summary: 'Raise Room simulated allocations, Proof of Conviction, and community scout votes.',
    bullets: ['Raise Room paper allocations', 'Proof of Conviction thesis', 'Community scout votes'],
    href: '/raise-room',
    cta: 'See demand signals',
  },
  {
    step: '03',
    title: 'Raise',
    color: 'pink',
    summary: 'Attract early backers and build a reputation & credibility score before real capital.',
    bullets: ['Simulated raise rooms', 'Scout conviction markets', 'Public founder track record'],
    href: '/raise-room',
    cta: 'Explore Raise Room',
  },
  {
    step: '04',
    title: 'Launch',
    color: 'amber',
    summary: 'Verified founder presence and launch readiness score — trust without oversharing.',
    bullets: ['Verified founder presence', 'Launch readiness score', 'Token only when proven'],
    href: '/projects',
    cta: 'Explore launch-ready',
  },
] as const;

const phaseStyles = {
  emerald: {
    border: 'border-emerald-500/25',
    bg: 'bg-emerald-950/20',
    badge: 'text-emerald-400',
    dot: 'bg-emerald-400',
  },
  blue: {
    border: 'border-blue-500/25',
    bg: 'bg-blue-950/20',
    badge: 'text-blue-400',
    dot: 'bg-blue-400',
  },
  pink: {
    border: 'border-pink-500/25',
    bg: 'bg-pink-950/20',
    badge: 'text-pink-400',
    dot: 'bg-pink-400',
  },
  amber: {
    border: 'border-amber-500/25',
    bg: 'bg-amber-950/20',
    badge: 'text-amber-400',
    dot: 'bg-amber-400',
  },
};

export function LandingFourPhases() {
  return (
    <section className="border-b border-zinc-800/80 py-16 md:py-20">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-center text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
          How it works
        </p>
        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {FOUR_PHASES.map((phase) => {
            const style = phaseStyles[phase.color];
            return (
              <div
                key={phase.title}
                className={`rounded-2xl border ${style.border} ${style.bg} p-6`}
              >
                <p className={`text-xs font-bold tracking-widest ${style.badge}`}>{phase.step}</p>
                <h2 className="mt-2 text-xl font-bold text-white">{phase.title}</h2>
                <p className="mt-3 text-sm leading-relaxed text-zinc-400">{phase.summary}</p>
                <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                  {phase.bullets.map((b) => (
                    <li key={b} className="flex gap-2">
                      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} />
                      {b}
                    </li>
                  ))}
                </ul>
                <Link
                  href={phase.href}
                  className="mt-5 inline-block text-sm font-medium text-white hover:underline"
                >
                  {phase.cta} →
                </Link>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** @deprecated use LandingFourPhases */
export function LandingThreePhases() {
  return <LandingFourPhases />;
}

function formatStatNumber(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}K`;
  return value.toLocaleString();
}

export function LandingLiveMetrics({ stats }: { stats: PlatformStats | null }) {
  const items = stats
    ? [
        { label: 'Verified founders', value: formatStatNumber(stats.verifiedFounders) },
        { label: 'Active projects', value: formatStatNumber(stats.activeProjects) },
        { label: 'Ddollar in ecosystem', value: formatUsd(stats.simulatedCapital, 0) },
        { label: 'Community members', value: formatStatNumber(stats.communityMembers) },
      ]
    : [
        { label: 'Verified founders', value: '—' },
        { label: 'Active projects', value: '—' },
        { label: 'Ddollar in ecosystem', value: '—' },
        { label: 'Community members', value: '—' },
      ];

  return (
    <section className="border-b border-zinc-800/80 bg-zinc-950/60">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="flex flex-wrap items-center justify-center gap-3">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
            Platform pulse
          </p>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-950/40 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            Live
          </span>
        </div>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((item) => (
            <div
              key={item.label}
              className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-5 py-4 text-center"
            >
              <p className="text-2xl font-bold text-white">{item.value}</p>
              <p className="mt-1 text-xs uppercase tracking-widest text-zinc-500">{item.label}</p>
            </div>
          ))}
        </div>
        {stats && (stats.totalTrades > 0 || stats.paperTraders > 0) && (
          <p className="mt-4 text-center text-sm text-zinc-500">
            {stats.paperTraders.toLocaleString()} paper traders ·{' '}
            {stats.totalTrades.toLocaleString()} simulated trades · each trader starts with $10,000
            virtual cash
          </p>
        )}
      </div>
    </section>
  );
}

export function LandingEconomy() {
  return (
    <section className="border-b border-zinc-800/80 py-16 md:py-20">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-center text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
          Economy & tokenomics
        </p>
        <div className="mt-10 grid gap-6 lg:grid-cols-3">
          <div className="rounded-2xl border border-amber-500/20 bg-amber-950/10 p-6">
            <p className="text-2xl" aria-hidden>
              🪙
            </p>
            <h3 className="mt-3 text-lg font-bold text-white">The DDOLLAR economy</h3>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              Virtual currency for paper trading, prediction markets, and Raise Room allocations.
              Every trader starts with {formatUsd(STARTING_CASH_USD, 0)} paper cash.
            </p>
            <Link
              href="/account?tab=topup"
              className="mt-4 inline-block rounded-lg border border-amber-500/40 px-4 py-2 text-sm font-medium text-amber-200 hover:border-amber-400/60"
            >
              Top up 10,000 DDOLLAR for ${TOP_UP_FEE_USD} USDC
            </Link>
          </div>

          <div className="rounded-2xl border border-violet-500/20 bg-violet-950/10 p-6">
            <h3 className="text-lg font-bold text-white">Tokenomics</h3>
            <div className="mt-4 flex items-center gap-4">
              <div
                className="relative h-28 w-28 shrink-0 rounded-full"
                style={{
                  background:
                    'conic-gradient(#8b5cf6 0 288deg, #3b82f6 288deg 324deg, #10b981 324deg 360deg)',
                }}
              >
                <div className="absolute inset-3 flex flex-col items-center justify-center rounded-full bg-zinc-950 text-center">
                  <span className="text-[10px] uppercase text-zinc-500">Company</span>
                  <span className="text-sm font-bold text-white">20%</span>
                </div>
              </div>
              <ul className="space-y-2 text-sm text-zinc-300">
                <li>
                  <span className="font-semibold text-violet-300">80%</span> Community
                </li>
                <li>
                  <span className="font-semibold text-blue-300">10%</span> Airdropped
                </li>
                <li>
                  <span className="font-semibold text-emerald-300">10%</span> Distributed over 10 years
                </li>
              </ul>
            </div>
          </div>

          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-950/10 p-6">
            <h3 className="text-lg font-bold text-white">Revenue flywheel</h3>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              Top-ups, tools, and distributions feed platform revenue →{' '}
              <strong className="font-medium text-emerald-300">50% buybacks & burn</strong> → stronger
              ecosystem for founders and traders.
            </p>
            <div className="mt-4 space-y-2 text-xs text-zinc-500">
              <p className="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
                Distributions · Top-ups · Builder tools
              </p>
              <p className="text-center text-zinc-600">↓</p>
              <p className="rounded-lg border border-emerald-500/20 bg-emerald-950/30 px-3 py-2 text-emerald-300">
                Platform revenue → 50% buybacks & burn
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function LandingLiveActivity() {
  const [items, setItems] = useState<PlatformActivityItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPlatformActivity(8)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <section className="border-b border-zinc-800/80 bg-zinc-950/40">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
              Live on Founder OS
            </p>
            <h2 className="mt-1 text-lg font-semibold text-white">Real activity — not vanity metrics</h2>
          </div>
          <Link href="/feed" className="text-sm text-blue-400 hover:underline">
            Feed →
          </Link>
        </div>
        {loading ? (
          <div className="mt-6 space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-14 animate-pulse rounded-xl bg-zinc-900" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <p className="mt-6 text-sm text-zinc-500">
            Founders are shipping — be the first to publish from{' '}
            <Link href="/founder-den" className="text-blue-400 hover:underline">
              Founder OS
            </Link>
            .
          </p>
        ) : (
          <ul className="mt-6 divide-y divide-zinc-800/80 rounded-xl border border-zinc-800/80 bg-zinc-900/30">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3.5 sm:px-5">
                <span className="text-lg" aria-hidden>
                  {activityIcon(item.kind)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-white">
                    {item.founderSlug ? (
                      <Link href={`/founder/${item.founderSlug}`} className="font-semibold hover:text-blue-300">
                        {item.founderName}
                      </Link>
                    ) : (
                      <span className="font-semibold">{item.founderName}</span>
                    )}
                    {item.projectSlug && item.projectName && (
                      <>
                        {' · '}
                        <Link href={`/project/${item.projectSlug}`} className="text-zinc-400 hover:text-white">
                          {item.projectName}
                        </Link>
                      </>
                    )}
                  </p>
                  <p className="mt-0.5 text-sm text-zinc-400">{item.headline}</p>
                  {item.detail && (
                    <p className="text-xs text-zinc-600">{item.detail}</p>
                  )}
                </div>
                <span className="shrink-0 text-xs text-zinc-600">{activityTimeAgo(item.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

const FAILURES = [
  'Anonymous founders',
  'Building in silence',
  'Updates scattered across 5 apps',
  'Spam communities & bot followers',
  'Zero market validation',
];

const SOLUTIONS = [
  'Verified public founders',
  'GitHub → translate → publish everywhere',
  'Demand validation before real raises',
  'Public reasoning — not anonymous hype',
];

export function LandingProblemSolution() {
  return (
    <section className="border-b border-zinc-800/80 py-20 md:py-28">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
          The insight
        </p>
        <h2 className="mt-3 max-w-2xl text-3xl font-bold tracking-tight text-white md:text-4xl">
          Building and marketing should be the same action
        </h2>
        <p className="mt-4 max-w-2xl text-zinc-400">
          Founders spend too much time updating Telegram, Discord, X, and Medium. Founder OS
          turns every commit and deploy into community content — automatically.
        </p>

        <div className="mt-12 grid gap-8 lg:grid-cols-2">
          <div>
            <p className="mb-4 text-sm font-semibold text-red-400">The old workflow</p>
            <ul className="space-y-3">
              {FAILURES.map((item) => (
                <li
                  key={item}
                  className="flex items-center gap-3 rounded-xl border border-red-500/20 bg-red-950/15 px-4 py-3.5 text-sm text-red-100/90 transition hover:border-red-500/35"
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-red-500/20 text-xs text-red-300">
                    ✕
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-4 text-sm font-semibold text-emerald-400">Founder OS workflow</p>
            <ul className="space-y-3">
              {SOLUTIONS.map((item) => (
                <li
                  key={item}
                  className="flex items-center gap-3 rounded-xl border border-emerald-500/20 bg-emerald-950/15 px-4 py-3.5 text-sm text-emerald-100/90 transition hover:border-emerald-500/35"
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/20 text-xs text-emerald-300">
                    ✓
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

const OS_WORKFLOW = [
  { step: 'Connect', desc: 'GitHub, Vercel, Railway, Neon, X' },
  { step: 'Ship', desc: 'Commit or deploy — webhook auto-drafts update' },
  { step: 'Translate', desc: 'Dev view + trader view, no LLM bill' },
  { step: 'Approve', desc: 'Review suggested post in Founder OS' },
  { step: 'Publish', desc: 'Build feed + X + project room — one click' },
];

export function LandingFounderOsWorkflow() {
  return (
    <section className="border-b border-zinc-800/80 bg-gradient-to-b from-indigo-950/20 to-transparent py-20 md:py-28">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-400">
          Live today
        </p>
        <h2 className="mt-3 text-3xl font-bold tracking-tight text-white md:text-4xl">
          GitHub commit → translate → publish everywhere
        </h2>
        <p className="mt-4 max-w-2xl text-zinc-400">
          One unified founder workflow. Connect your stack once — every ship becomes transparency,
          community engagement, and reputation.
        </p>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {OS_WORKFLOW.map((item, i) => (
            <div
              key={item.step}
              className="relative rounded-2xl border border-indigo-500/20 bg-zinc-900/40 p-5"
            >
              <span className="text-xs font-bold text-indigo-400">{String(i + 1).padStart(2, '0')}</span>
              <p className="mt-2 font-semibold text-white">{item.step}</p>
              <p className="mt-1 text-xs text-zinc-500">{item.desc}</p>
            </div>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap gap-3">
          <Link
            href="/founder-den"
            className="rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white hover:bg-indigo-500"
          >
            Open Founder OS
          </Link>
          <Link
            href="/feed"
            className="rounded-xl border border-zinc-700 px-6 py-3 text-sm text-zinc-300 hover:text-white"
          >
            See build feed
          </Link>
        </div>
      </div>
    </section>
  );
}

const JOURNEY_STEPS = [
  { title: 'Idea', desc: 'Founder submits vision & identity' },
  { title: 'Prototype', desc: 'Early product or smart contract' },
  { title: 'Public build', desc: 'GitHub sync & publish everywhere' },
  { title: 'Demand validation', desc: 'Community votes & paper capital' },
  { title: 'Simulated raise', desc: 'Virtual ICO — no real money' },
  { title: 'Community growth', desc: 'Helpful marks, bounties, scouts' },
  { title: 'Real launch', desc: 'Token only when execution is proven' },
];

export function LandingFounderJourney() {
  return (
    <section className="border-b border-zinc-800/80 bg-zinc-950/30 py-20 md:py-28">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
          Founder journey
        </p>
        <h2 className="mt-3 text-3xl font-bold tracking-tight text-white md:text-4xl">
          Verify first. Build publicly. Raise last.
        </h2>
        <p className="mt-4 max-w-2xl text-zinc-400">
          Flip the launch order — prove execution before anyone touches real capital.
        </p>

        <div className="mt-14 flex flex-col items-start gap-0 md:flex-row md:items-stretch md:gap-0">
          {JOURNEY_STEPS.map((step, i) => (
            <div key={step.title} className="relative flex flex-1 flex-col items-center pb-10 md:pb-0">
              {i < JOURNEY_STEPS.length - 1 && (
                <>
                  <div className="absolute left-1/2 top-8 hidden h-px w-full bg-gradient-to-r from-emerald-500/50 to-emerald-500/10 md:block" />
                  <div className="absolute left-4 top-8 bottom-0 w-px bg-gradient-to-b from-emerald-500/50 to-emerald-500/10 md:hidden" />
                </>
              )}
              <div className="relative z-10 flex h-16 w-16 items-center justify-center rounded-2xl border border-emerald-500/30 bg-zinc-900 text-sm font-bold text-emerald-300 shadow-lg shadow-emerald-950/30">
                {i + 1}
              </div>
              <p className="mt-4 text-center text-sm font-semibold text-white">{step.title}</p>
              <p className="mt-1 max-w-[120px] text-center text-xs text-zinc-500">{step.desc}</p>
              {i < JOURNEY_STEPS.length - 1 && (
                <span className="mt-2 text-emerald-500/50 md:hidden">↓</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function LandingTrustSecurity() {
  return (
    <section className="border-b border-zinc-800/80 py-20">
      <div className="mx-auto max-w-6xl px-6">
        <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-400">
              Privacy-first · trending for a reason
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-white">
              Private data stays private. Public proof stays public.
            </h2>
            <p className="mt-4 text-zinc-400">
              Crypto founders already self-custody keys — project context should work the same way.
              Choose cloud memory, GitHub files, browser-local, or a{' '}
              <strong className="font-medium text-zinc-300">Founder Node vault</strong> on your PC.
              Founder OS orchestrates; you decide what leaves your machine.
            </p>
            <p className="mt-3 text-sm text-zinc-500">
              Verified founders without leaking KYC. Agent history without a platform reading every
              prompt. Transparency you choose — not a data grab dressed as &ldquo;AI features.&rdquo;
            </p>
            <Link
              href="/settings/builder"
              className="mt-6 inline-block text-sm font-medium text-cyan-400 hover:underline"
            >
              Set up Founder Node →
            </Link>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              ['✅ Your vault on your disk', '❌ Not 20GB on our servers'],
              ['✅ Metadata sync only', '❌ Not full AI chat logs'],
              ['✅ Verified human, not doxxed', '❌ Not passport images public'],
              ['✅ GitHub & build proof', '❌ Not wallet seed phrases'],
            ].map(([yes, no]) => (
              <div
                key={yes}
                className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm"
              >
                <p className="text-emerald-300">{yes}</p>
                <p className="mt-1 text-zinc-500">{no}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

const LIVE_FEATURES = [
  {
    title: 'Founder OS dashboard',
    desc: 'Credits, bounties, community pool, connected stack — one place.',
    status: 'Live',
  },
  {
    title: 'Publish everywhere',
    desc: 'Build feed + X + project room from a single approved update.',
    status: 'Live',
  },
  {
    title: 'GitHub & deploy sync',
    desc: 'Commits and Vercel/Railway webhooks auto-draft build updates.',
    status: 'Live',
  },
  {
    title: 'Proof of Conviction',
    desc: 'Record thesis at buy, share to X in one tap after OAuth sign-in.',
    status: 'Live',
  },
  {
    title: 'Simulated raises',
    desc: 'Community allocates paper capital — demand proof before real raise.',
    status: 'Live',
  },
  {
    title: 'Quality rewards',
    desc: 'Top 0.2% useful contributors win daily paper cash — spam excluded.',
    status: 'Live',
  },
];

const COMING_FEATURES = [
  {
    title: 'Trust ring reputation',
    desc: 'Visual score: identity, GitHub, delivery, community, transparency.',
    status: 'Coming',
  },
  {
    title: 'Discord & Telegram publish',
    desc: 'Extend publish everywhere to community channels.',
    status: 'Coming',
  },
  {
    title: 'Founder battle arena',
    desc: 'Two projects, community votes — viral discovery loop.',
    status: 'Coming',
  },
];

export function LandingRoadmap() {
  const all = [...LIVE_FEATURES, ...COMING_FEATURES];

  return (
    <section className="py-20 md:py-28">
      <div className="mx-auto max-w-6xl px-6">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
          Platform
        </p>
        <h2 className="mt-3 text-3xl font-bold tracking-tight text-white">
          What&apos;s live vs what&apos;s next
        </h2>
        <p className="mt-4 max-w-2xl text-zinc-400">
          Founder OS is shipping — not a roadmap slide. Connect your stack and start publishing
          today.
        </p>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {all.map((item) => (
            <div
              key={item.title}
              className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-5 transition hover:border-zinc-700"
            >
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold text-white">{item.title}</h3>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                    item.status === 'Live'
                      ? 'bg-emerald-950/50 text-emerald-400'
                      : 'bg-zinc-800 text-zinc-500'
                  }`}
                >
                  {item.status}
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-500">{item.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function LandingProofLayer() {
  return (
    <section className="border-y border-zinc-800/80 bg-gradient-to-b from-emerald-950/10 to-transparent py-20">
      <div className="mx-auto max-w-6xl px-6">
        <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-500">
              Live today
            </p>
            <h2 className="mt-3 text-3xl font-bold text-white">Proof of Conviction</h2>
            <p className="mt-4 text-zinc-400">
              Paper trade with live prices. Record your thesis at buy. Share conviction to X in one
              click. Scouts validate listings. Founders mark helpful replies — quality over spam.
            </p>
            <ul className="mt-6 space-y-2 text-sm text-zinc-300">
              {[
                '1-click X posting after sign-in with X',
                '$10,000 paper trading with conviction fields',
                'Daily quality lottery for top 0.2% contributors',
                '25,000 Founder Credits + community pool on launch',
                'Public portfolios, leaderboards, Early Scout badges',
              ].map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="text-emerald-400">→</span> {item}
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/paper-trading"
                className="rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500"
              >
                Start paper trading
              </Link>
              <Link
                href="/reputation"
                className="rounded-xl border border-zinc-700 px-5 py-2.5 text-sm text-zinc-300 hover:text-white"
              >
                Points & rewards
              </Link>
            </div>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
            <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
              Stack hub · connect once
            </p>
            <ul className="mt-4 space-y-2 text-sm">
              {['GitHub', 'Vercel', 'Railway', 'Neon', 'DigitalOcean', 'Supabase', 'X', 'Founder Copilot'].map(
                (name) => (
                  <li
                    key={name}
                    className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2"
                  >
                    <span className="text-zinc-300">{name}</span>
                    <span className="text-[10px] uppercase text-emerald-500">Connect in Founder OS</span>
                  </li>
                ),
              )}
            </ul>
            <p className="mt-4 text-xs text-zinc-600">
              Deploy webhooks auto-draft updates — one dashboard instead of five separate tools.
            </p>
            <Link
              href="/founder-den"
              className="mt-4 inline-block text-sm font-medium text-emerald-400 hover:underline"
            >
              Connect your stack →
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

export function LandingFinalCta() {
  return (
    <section className="border-t border-zinc-800/80 bg-gradient-to-b from-violet-950/20 to-transparent py-24">
      <div className="mx-auto max-w-3xl px-6 text-center">
        <h2 className="text-3xl font-bold tracking-tight text-white md:text-4xl">
          Build. Validate. Launch. Own the network.
        </h2>
        <p className="mt-4 text-zinc-400">
          Prove execution publicly. Show real demand. Keep sensitive project memory private with
          Founder Node — Founder OS coordinates everything, you keep ownership.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-3">
          <Link
            href="/founder-den"
            className="rounded-xl bg-violet-600 px-8 py-3.5 font-semibold text-white hover:bg-violet-500"
          >
            Open Founder OS →
          </Link>
          <Link
            href="/list-your-project"
            className="rounded-xl border border-zinc-600 px-8 py-3.5 font-semibold text-white hover:border-violet-500/50"
          >
            List your project
          </Link>
        </div>
      </div>
    </section>
  );
}
