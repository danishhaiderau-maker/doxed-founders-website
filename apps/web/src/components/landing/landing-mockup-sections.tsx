'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { formatUsd, STARTING_CASH_USD } from '@dcf/utils';
import { ProjectSpotlight } from '@/components/landing/project-spotlight';
import {
  fetchLeaderboard,
  fetchOpenScoutListings,
  fetchVotingStats,
  LeaderboardEntry,
  PlatformStats,
  ScoutListing,
  SpotlightProject,
} from '@/lib/api';

function formatStat(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000).toLocaleString()}K`;
  return value.toLocaleString();
}

export function LandingMockupHero() {
  return (
    <section className="relative overflow-hidden border-b border-zinc-800/80">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_20%_0%,rgba(99,102,241,0.18),transparent)]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_80%_20%,rgba(139,92,246,0.12),transparent)]" />
      <div className="relative mx-auto grid w-full max-w-[90rem] gap-10 px-4 py-14 sm:px-6 lg:grid-cols-2 lg:items-center lg:gap-16 lg:px-10 lg:py-20">
        <div>
          <h1 className="text-4xl font-bold leading-[1.06] tracking-tight md:text-5xl xl:text-[3.25rem]">
            <span className="text-white">Private by default.</span>
            <br />
            <span className="bg-gradient-to-r from-violet-300 via-indigo-300 to-sky-300 bg-clip-text text-transparent">
              Public by proof.
            </span>
          </h1>
          <p className="mt-6 max-w-lg text-base leading-relaxed text-zinc-400 md:text-lg">
            Doxxed is where crypto startups build in public, traders validate with Ddollar paper capital,
            and communities back founders who prove execution — not hype.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/founder-den"
              className="rounded-xl bg-violet-600 px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 hover:bg-violet-500"
            >
              Founder OS →
            </Link>
            <Link
              href="/feed"
              className="inline-flex items-center gap-2 rounded-xl border border-zinc-600 bg-zinc-900/40 px-7 py-3.5 text-sm font-semibold text-white hover:border-violet-500/40"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-full border border-zinc-600 text-[10px]">
                ▶
              </span>
              Watch demo
            </Link>
          </div>
          <div className="mt-8 flex items-center gap-3">
            <div className="flex -space-x-2">
              {['DN', 'CF', 'JT', 'BK'].map((initials) => (
                <span
                  key={initials}
                  className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-[#050508] bg-gradient-to-br from-violet-500 to-indigo-600 text-[10px] font-bold text-white"
                >
                  {initials}
                </span>
              ))}
            </div>
            <p className="text-sm text-zinc-500">
              Join founders, traders, scouts & supporters building together.
            </p>
          </div>
        </div>

        <div className="relative mx-auto flex min-h-[280px] w-full max-w-md items-center justify-center lg:max-w-none">
          <div className="relative h-56 w-56 sm:h-64 sm:w-64">
            <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-indigo-500/30 via-violet-600/20 to-cyan-500/20 blur-3xl" />
            <div
              className="absolute left-1/2 top-1/2 h-32 w-32 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-2xl border border-cyan-400/40 bg-gradient-to-br from-indigo-600/80 via-violet-700/70 to-cyan-600/60 shadow-[0_0_60px_rgba(99,102,241,0.5)]"
              style={{ transform: 'translate(-50%, -50%) rotateX(18deg) rotateY(-24deg) rotateZ(45deg)' }}
            />
            {[
              { label: 'Build in public', pos: 'left-0 top-4' },
              { label: 'Self-custody', pos: 'right-0 top-8' },
              { label: 'AI Copilot', pos: 'left-2 bottom-16' },
              { label: 'Community proof', pos: 'right-0 bottom-8' },
            ].map((chip) => (
              <span
                key={chip.label}
                className={`absolute ${chip.pos} rounded-full border border-zinc-700/80 bg-zinc-900/90 px-3 py-1.5 text-[11px] font-medium text-zinc-200 shadow-lg`}
              >
                {chip.label}
              </span>
            ))}
            <span className="absolute bottom-0 left-1/2 -translate-x-1/2 rounded-full border border-emerald-500/30 bg-emerald-950/50 px-3 py-1 text-[10px] font-medium text-emerald-300">
              Powered by Phala Network TEE
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

export function LandingMockupStats({ stats }: { stats: PlatformStats | null }) {
  const [scoutVotes, setScoutVotes] = useState<number | null>(null);

  useEffect(() => {
    fetchVotingStats()
      .then((v) => setScoutVotes(v.activeUsers))
      .catch(() => setScoutVotes(null));
  }, []);

  const items = [
    {
      label: 'Verified founders',
      value: stats ? formatStat(stats.verifiedFounders) : '—',
      icon: '🛡',
    },
    {
      label: 'Projects building',
      value: stats ? formatStat(stats.activeProjects) : '—',
      icon: '📦',
    },
    {
      label: 'Ddollar in ecosystem',
      value: stats ? formatUsd(stats.simulatedCapital, 0) : '—',
      icon: '💵',
    },
    {
      label: 'Paper traders',
      value: stats && stats.paperTraders > 0 ? formatStat(stats.paperTraders) : '—',
      icon: '📈',
    },
    {
      label: 'Scout voters',
      value: scoutVotes != null ? formatStat(scoutVotes) : '—',
      icon: '🔭',
    },
    {
      label: 'Community members',
      value: stats ? formatStat(stats.communityMembers) : '—',
      icon: '👥',
    },
  ];

  return (
    <section className="border-b border-zinc-800/80 bg-zinc-950/80">
      <div className="mx-auto grid w-full max-w-[90rem] grid-cols-2 gap-px bg-zinc-800/50 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((item) => (
          <div
            key={item.label}
            className="flex flex-col items-center justify-center bg-[#050508] px-4 py-6 text-center"
          >
            <span className="text-xl" aria-hidden>
              {item.icon}
            </span>
            <p className="mt-2 text-xl font-bold text-white sm:text-2xl">{item.value}</p>
            <p className="mt-1 text-[10px] font-medium uppercase tracking-wider text-zinc-500">
              {item.label}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

const ECONOMY_FLOW = [
  { step: 'Join', desc: 'Create account' },
  { step: '10,000 DDollar', desc: 'Free paper cash' },
  { step: 'Trade', desc: 'Any token' },
  { step: 'Scout', desc: 'Validate listings' },
  { step: 'Reputation', desc: 'Earn trust' },
  { step: 'Rewards', desc: 'Unlock perks' },
];

export function LandingMockupEconomy() {
  return (
    <section className="border-b border-zinc-800/80 py-16 md:py-20">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <div className="grid gap-10 lg:grid-cols-2 lg:gap-16">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-400/90">
              The Ddollar ecosystem
            </p>
            <h2 className="mt-3 text-3xl font-bold text-white md:text-4xl">
              Built for shrimps, not whales
            </h2>
            <ul className="mt-6 space-y-3 text-sm text-zinc-400">
              {[
                `${formatUsd(STARTING_CASH_USD, 0)} DDollar free at signup`,
                'Trade any token with paper capital — no real money',
                'Scout votes & reputation unlock community rewards',
                'Raise Room simulated allocations before real launch',
              ].map((line) => (
                <li key={line} className="flex gap-2">
                  <span className="text-emerald-400">✓</span>
                  {line}
                </li>
              ))}
            </ul>
            <p className="mt-6 text-xs text-zinc-600">
              DDollar is ecosystem currency for simulation only — no intrinsic value, not a security.
            </p>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
            <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Your journey</p>
            <div className="mt-6 flex flex-wrap items-center gap-2">
              {ECONOMY_FLOW.map((node, i) => (
                <div key={node.step} className="flex items-center gap-2">
                  <div className="rounded-xl border border-violet-500/30 bg-violet-950/30 px-3 py-2 text-center">
                    <p className="text-xs font-bold text-violet-200">{node.step}</p>
                    <p className="text-[10px] text-zinc-500">{node.desc}</p>
                  </div>
                  {i < ECONOMY_FLOW.length - 1 && (
                    <span className="text-zinc-600" aria-hidden>
                      →
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function LandingMockupLiveHub({ spotlight }: { spotlight: SpotlightProject[] }) {
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);
  const [scouts, setScouts] = useState<ScoutListing[]>([]);

  useEffect(() => {
    fetchLeaderboard()
      .then((rows) => setLeaders(rows.slice(0, 5)))
      .catch(() => setLeaders([]));
    fetchOpenScoutListings()
      .then((rows) => setScouts(rows.slice(0, 3)))
      .catch(() => setScouts([]));
  }, []);

  return (
    <section className="border-b border-zinc-800/80 py-14 md:py-18">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">Live on Doxxed</p>
        <div className="mt-8 grid gap-6 lg:grid-cols-12">
          <div className="lg:col-span-5">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-emerald-400">
              Live spotlight
            </p>
            <ProjectSpotlight projects={spotlight} />
          </div>
          <div className="lg:col-span-3">
            <div className="h-full rounded-2xl border border-amber-500/25 bg-amber-950/10 p-5">
              <p className="text-xs font-semibold uppercase tracking-wider text-amber-400">
                Paper trader leaderboard
              </p>
              {leaders.length > 0 ? (
                <ul className="mt-4 space-y-3">
                  {leaders.map((e) => (
                    <li key={e.userId} className="flex items-center gap-3">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs font-bold text-zinc-300">
                        {e.rank}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-white">{e.displayName}</p>
                        <p className={`text-xs ${e.roi >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {e.roi >= 0 ? '+' : ''}
                          {e.roi.toFixed(1)}%
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-zinc-500">No rankings yet — be first on paper trading.</p>
              )}
              <Link href="/leaderboard" className="mt-4 inline-block text-sm text-amber-300 hover:underline">
                Full leaderboard →
              </Link>
            </div>
          </div>
          <div className="lg:col-span-4">
            <div className="h-full rounded-2xl border border-violet-500/25 bg-violet-950/10 p-5">
              <p className="text-xs font-semibold uppercase tracking-wider text-violet-400">
                Scout highlights
              </p>
              {scouts.length > 0 ? (
                <ul className="mt-4 space-y-4">
                  {scouts.map((s) => {
                    const yesPct = s.tally.yesPercent > 0 ? Math.round(s.tally.yesPercent) : s.verificationScore;
                    return (
                      <li key={s.id} className="rounded-xl border border-zinc-800/80 bg-black/20 p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-semibold text-white">{s.projectName}</p>
                          <span className="rounded-full bg-violet-500/20 px-2 py-0.5 text-xs font-bold text-violet-200">
                            {yesPct}%
                          </span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs text-zinc-500">
                          {s.whyList ?? s.summary ?? 'Community validation in progress'}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-zinc-500">
                  Open listings appear here as scouts vote.
                </p>
              )}
              <Link href="/scout-votes" className="mt-4 inline-block text-sm text-violet-300 hover:underline">
                Scout votes →
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const AI_PROVIDERS = ['DeepSeek', 'OpenAI', 'Claude', 'Gemini', 'OpenRouter'];

export function LandingMockupStackRow() {
  return (
    <section className="border-b border-zinc-800/80 py-14">
      <div className="mx-auto grid w-full max-w-[90rem] gap-6 px-4 sm:px-6 lg:grid-cols-3 lg:px-10">
        <div className="rounded-2xl border border-emerald-500/25 bg-emerald-950/10 p-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Founder Node</p>
          <h3 className="mt-2 text-lg font-bold text-white">Self-custody vault on your PC</h3>
          <p className="mt-2 text-sm text-zinc-400">
            Project memory stays local. Founder OS syncs metadata only — resume on any device when paired.
          </p>
          <div className="mt-4 flex h-24 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950/50">
            <span className="text-4xl" aria-hidden>
              🖥
            </span>
          </div>
          <Link
            href="/founder-node"
            className="mt-4 inline-block text-sm font-semibold text-emerald-300 hover:underline"
          >
            Download Founder Node →
          </Link>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Privacy by design</p>
          <h3 className="mt-2 text-lg font-bold text-white">Private data stays private</h3>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {[
              ['✅ Your vault on your disk', '❌ Not 20GB on our servers'],
              ['✅ Metadata sync only', '❌ Not full AI chat logs'],
              ['✅ Verified human, not doxxed', '❌ Not passport images public'],
              ['✅ GitHub & build proof', '❌ Not wallet seed phrases'],
            ].map(([yes, no]) => (
              <div key={yes} className="rounded-lg border border-zinc-800/80 bg-black/30 p-3 text-xs">
                <p className="text-emerald-300">{yes}</p>
                <p className="mt-1 text-zinc-600">{no}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-violet-500/25 bg-violet-950/10 p-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-violet-400">Bring your own AI</p>
          <h3 className="mt-2 text-lg font-bold text-white">Use the models you trust</h3>
          <p className="mt-2 text-sm text-zinc-400">You own the keys — we don&apos;t train on your data.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {AI_PROVIDERS.map((name) => (
              <span
                key={name}
                className="rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 py-1.5 text-xs font-medium text-zinc-300"
              >
                {name}
              </span>
            ))}
          </div>
          <Link
            href="/settings/builder"
            className="mt-4 inline-block text-sm font-semibold text-violet-300 hover:underline"
          >
            Manage API keys →
          </Link>
        </div>
      </div>
    </section>
  );
}

const NETWORK_RULES = [
  { icon: '🚫', label: 'No scams' },
  { icon: '🔨', label: 'Build in public' },
  { icon: '🔒', label: 'Privacy by default' },
  { icon: '✓', label: 'Verified humans' },
  { icon: '📊', label: 'Proof over hype' },
  { icon: '👥', label: '80% community' },
  { icon: '💵', label: 'Paper first' },
  { icon: '🛡', label: 'Transparency first' },
];

export function LandingMockupRules() {
  return (
    <section className="border-b border-zinc-800/80 py-12">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <p className="text-center text-xs font-semibold uppercase tracking-[0.2em] text-zinc-500">
          Rules of the network
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-4 sm:gap-6">
          {NETWORK_RULES.map((rule) => (
            <div
              key={rule.label}
              className="flex w-24 flex-col items-center gap-2 text-center sm:w-28"
            >
              <span className="flex h-12 w-12 items-center justify-center rounded-full border border-zinc-700 bg-zinc-900/60 text-lg">
                {rule.icon}
              </span>
              <span className="text-[11px] font-medium text-zinc-400">{rule.label}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function LandingMockupCommunity() {
  return (
    <section className="border-b border-zinc-800/80 py-16">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <div className="overflow-hidden rounded-3xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-zinc-950 to-indigo-950/30 p-8 md:p-12">
          <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
            <div>
              <p className="text-5xl font-bold text-white md:text-6xl">80%</p>
              <p className="mt-2 text-xl font-semibold text-violet-200">Community owned</p>
              <ul className="mt-6 space-y-2 text-sm text-zinc-300">
                <li>
                  <span className="font-semibold text-blue-300">10%</span> Airdropped at launch
                </li>
                <li>
                  <span className="font-semibold text-emerald-300">70%</span> Vested over 10 years
                </li>
                <li>
                  <span className="font-semibold text-zinc-400">20%</span> Team allocation
                </li>
              </ul>
            </div>
            <div className="flex justify-center">
              <div
                className="relative h-40 w-40 rounded-full"
                style={{
                  background: 'conic-gradient(#8b5cf6 0 288deg, #52525b 288deg 360deg)',
                }}
                aria-hidden
              >
                <div className="absolute inset-4 flex flex-col items-center justify-center rounded-full bg-zinc-950 text-center">
                  <span className="text-xs text-zinc-500">Team</span>
                  <span className="text-2xl font-bold text-white">20%</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const FOOTER_LINKS = [
  { label: 'Docs', href: '/founder-node' },
  { label: 'Founder OS', href: '/founder-den' },
  { label: 'Paper trading', href: '/paper-trading' },
  { label: 'Scout votes', href: '/scout-votes' },
  { label: 'List project', href: '/list-your-project' },
];

export function LandingMockupFooter() {
  return (
    <footer className="border-t border-zinc-800/80 bg-zinc-950/90 py-12">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <div className="flex flex-wrap items-start justify-between gap-8">
          <div>
            <p className="text-lg font-bold text-white">Doxxed crypto</p>
            <p className="mt-1 max-w-xs text-sm text-zinc-500">
              Private by default. Public by proof. The operating system for crypto startups.
            </p>
            <div className="mt-4 flex gap-3">
              {[
                { label: 'X', href: 'https://x.com' },
                { label: 'GitHub', href: 'https://github.com/danishhaiderau-maker/doxed-founders-website' },
              ].map((s) => (
                <a
                  key={s.label}
                  href={s.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
                >
                  {s.label}
                </a>
              ))}
            </div>
          </div>
          <nav className="flex flex-wrap gap-x-6 gap-y-2">
            {FOOTER_LINKS.map((link) => (
              <Link key={link.href} href={link.href} className="text-sm text-zinc-500 hover:text-white">
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
        <p className="mt-10 text-center text-xs text-zinc-600">
          © {new Date().getFullYear()} Doxxed Crypto. Build publicly. Earn trust. Launch responsibly.
        </p>
      </div>
    </footer>
  );
}
