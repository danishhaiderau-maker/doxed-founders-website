'use client';

import Link from 'next/link';
import { formatUsd } from '@dcf/utils';
import type { PlatformStats } from '@/lib/api';

function formatStatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return n.toLocaleString();
  return String(n);
}

export function LandingHero() {
  return (
    <section className="relative overflow-hidden border-b border-zinc-800/80">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(16,185,129,0.15),transparent)]" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,transparent,rgba(5,5,8,0.8))]" />
      <div className="relative mx-auto max-w-6xl px-6 py-24 md:py-32">
        <p className="inline-flex items-center gap-2 rounded-full border border-emerald-500/25 bg-emerald-950/30 px-3 py-1 text-xs font-medium uppercase tracking-widest text-emerald-300">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          Founder OS · live now
        </p>
        <h1 className="mt-6 max-w-4xl text-4xl font-bold leading-[1.08] tracking-tight text-white md:text-6xl lg:text-7xl">
          Build in public. Publish everywhere. Prove it before you raise.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-zinc-400 md:text-xl">
          Connect GitHub, Vercel, Railway, Neon, and X. Sync commits, translate dev work for
          traders, and post to your build feed, community, and X in one click — the operating
          system for transparent crypto startups.
        </p>
        <div className="mt-10 flex flex-wrap gap-3">
          <Link
            href="/founder-den"
            className="rounded-xl bg-white px-6 py-3.5 text-sm font-semibold text-black transition hover:bg-zinc-100"
          >
            Open Founder OS
          </Link>
          <Link
            href="/projects"
            className="rounded-xl border border-zinc-600 bg-zinc-900/50 px-6 py-3.5 text-sm font-semibold text-white transition hover:border-emerald-500/50 hover:bg-zinc-900"
          >
            Explore projects
          </Link>
          <Link
            href="/paper-trading"
            className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-6 py-3.5 text-sm font-semibold text-emerald-300 transition hover:bg-emerald-950/40"
          >
            Proof of Conviction — $10k paper
          </Link>
        </div>
      </div>
    </section>
  );
}

export function LandingLiveMetrics({ stats }: { stats: PlatformStats | null }) {
  const items = stats
    ? [
        { label: 'Verified founders', value: formatStatNumber(stats.verifiedFounders) },
        { label: 'Active projects', value: formatStatNumber(stats.activeProjects) },
        {
          label: 'Simulated capital',
          value: formatUsd(stats.simulatedCapital, 0),
        },
        { label: 'Community members', value: formatStatNumber(stats.communityMembers) },
      ]
    : [
        { label: 'Verified founders', value: '—' },
        { label: 'Active projects', value: '—' },
        { label: 'Simulated capital', value: '—' },
        { label: 'Community members', value: '—' },
      ];

  return (
    <section className="border-b border-zinc-800/80 bg-zinc-950/50">
      <div className="mx-auto grid max-w-6xl grid-cols-2 divide-x divide-y divide-zinc-800/80 md:grid-cols-4 md:divide-y-0">
        {items.map((item) => (
          <div key={item.label} className="px-6 py-8 text-center md:py-10">
            <p className="text-2xl font-bold tracking-tight text-white md:text-3xl">{item.value}</p>
            <p className="mt-2 text-xs font-medium uppercase tracking-wider text-zinc-500">
              {item.label}
            </p>
          </div>
        ))}
      </div>
      {stats && stats.totalTrades > 0 && (
        <p className="border-t border-zinc-800/80 py-3 text-center text-xs text-zinc-600">
          {stats.totalTrades.toLocaleString()} paper trades logged · {stats.paperTraders.toLocaleString()}{' '}
          active traders
        </p>
      )}
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
  'Proof of Conviction on every trade',
  'Quality rewards — not spam points',
  'Simulated fundraising demand',
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
            href="/build-feed"
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
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
              Privacy-first verification
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-white">
              Trust without oversharing
            </h2>
            <p className="mt-4 text-zinc-400">
              Doxxing carries real security risk. We verify founders — we do not expose sensitive
              documents publicly. Verified transparency, not a data leak.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              ['✅ Verified identity', '❌ Not home address'],
              ['✅ Verified human', '❌ Not passport image'],
              ['✅ Public founder profile', '❌ Not private KYC docs'],
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
              {['GitHub', 'Vercel', 'Railway', 'Neon', 'DigitalOcean', 'Supabase', 'X', 'Cursor Build Room'].map(
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
    <section className="border-t border-zinc-800/80 py-24">
      <div className="mx-auto max-w-3xl px-6 text-center">
        <h2 className="text-3xl font-bold tracking-tight text-white md:text-4xl">
          The public startup operating system
        </h2>
        <p className="mt-4 text-zinc-400">
          Founders build in public. Traders prove conviction. Every minute shipping becomes
          marketing, transparency, and community growth.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-3">
          <Link
            href="/founder-den"
            className="rounded-xl bg-white px-8 py-3.5 font-semibold text-black hover:bg-zinc-100"
          >
            Open Founder OS
          </Link>
          <Link
            href="/register"
            className="rounded-xl border border-zinc-600 px-8 py-3.5 font-semibold text-white hover:border-emerald-500/50"
          >
            Join the community
          </Link>
        </div>
      </div>
    </section>
  );
}
