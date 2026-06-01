'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { formatUsd, STARTING_CASH_USD } from '@dcf/utils';
import { SiteBrand } from '@/components/site-nav';
import type { PlatformStats } from '@/lib/api';

function formatStat(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return value.toLocaleString();
  return value.toLocaleString();
}

const LANDING_NAV = [
  { href: '/discover', label: 'Discover' },
  { href: '/projects', label: 'Projects' },
  { href: '/leaderboard', label: 'Rankings' },
  { href: '/founder-den', label: 'Founder OS' },
  { href: '/raise-room', label: 'Raise Room' },
  { href: '/trust-center', label: 'Trust Center' },
] as const;

const NODE_ORBIT = [
  { label: 'GitHub', sub: 'Commits & updates', angle: -55 },
  { label: 'Your Data', sub: 'Encrypted locally', angle: 5 },
  { label: 'AI Models', sub: 'Models you trust', angle: 55 },
  { label: 'Phala TEE', sub: 'Hardware encryption', angle: 125 },
  { label: 'Community', sub: 'Proof & conviction', angle: 180 },
  { label: 'Founder OS', sub: 'Plan · ship · launch', angle: 235 },
];

const PIPELINE = [
  { step: 'Build', tag: 'Transparency', desc: 'Connect GitHub. Ship in public.', color: 'text-violet-300' },
  { step: 'Validate', tag: 'Conviction', desc: 'Traders use DDollar. Scouts vote.', color: 'text-sky-300' },
  { step: 'Raise', tag: 'Community', desc: 'Find early believers. Simulated allocations.', color: 'text-amber-300' },
  { step: 'Launch', tag: 'Trust', desc: 'Launch when execution and demand are visible.', color: 'text-emerald-300' },
];

const SHRIMP_FLOW = [
  { label: 'Join', sub: 'Create account' },
  { label: `${formatUsd(STARTING_CASH_USD, 0)} DDollar`, sub: 'Free paper capital' },
  { label: 'Trade', sub: 'Paper trade any token' },
  { label: 'Scout', sub: 'Vote on projects' },
  { label: 'Earn Reputation', sub: 'Build your score' },
  { label: 'Gain Followers', sub: 'Build your network' },
];

const DDOLLAR_USES = [
  { icon: '📈', label: 'Paper Trading' },
  { icon: '🎯', label: 'Predictions' },
  { icon: '🔭', label: 'Scout Votes' },
  { icon: '💰', label: 'DDollar Earned' },
  { icon: '🏆', label: 'Rewards' },
  { icon: '⚡', label: 'Platform Access' },
];

const ANTI_PATTERNS = [
  'No scams',
  'No pump & dumps',
  'No extractors',
  'No outside money',
  'Closed system · Fair game',
];

const NETWORK_RULES = [
  { icon: '🚫', label: 'No scams', sub: 'Proof over hype' },
  { icon: '🔨', label: 'Build in public', sub: '100% community' },
  { icon: '🔒', label: 'Private by default', sub: 'Paper first' },
  { icon: '🛡', label: 'Verified humans', sub: 'Transparency first' },
];

const AI_PROVIDERS = ['OpenAI', 'DeepSeek', 'Claude', 'Gemini'];

export function LandingHeader() {
  const { data: session } = useSession();

  return (
    <header className="border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-3 px-4 py-2.5 sm:px-6 lg:px-8">
        <SiteBrand className="text-sm font-bold tracking-tight" />
        <nav className="hidden flex-wrap items-center gap-0.5 lg:flex">
          {LANDING_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-1.5 text-sm text-zinc-400 transition hover:bg-zinc-900 hover:text-white"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <Link
            href="/discover"
            className="hidden rounded-lg p-2 text-zinc-400 hover:bg-zinc-900 hover:text-white sm:inline-flex"
            aria-label="Search"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M11 18a7 7 0 100-14 7 7 0 000 14z" />
            </svg>
          </Link>
          {!session && (
            <Link
              href="/login"
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:text-white"
            >
              Login
            </Link>
          )}
          <Link
            href="/founder-den"
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
          >
            Open Founder OS →
          </Link>
        </div>
      </div>
    </header>
  );
}

function FounderNodeVisual() {
  return (
    <div className="relative mx-auto flex aspect-square w-full max-w-[300px] items-center justify-center">
      <div className="pointer-events-none absolute inset-[8%] rounded-full border border-violet-500/20" />
      <div className="pointer-events-none absolute inset-[18%] rounded-full border border-violet-500/10" />
      <div className="pointer-events-none absolute inset-0 rounded-full bg-[radial-gradient(circle,rgba(99,102,241,0.28),transparent_68%)]" />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 320 320" aria-hidden>
        {NODE_ORBIT.map((node) => {
          const rad = (node.angle * Math.PI) / 180;
          const x2 = 160 + Math.cos(rad) * 112;
          const y2 = 160 + Math.sin(rad) * 112;
          return (
            <line
              key={node.label}
              x1="160"
              y1="160"
              x2={x2}
              y2={y2}
              stroke="rgba(139,92,246,0.4)"
              strokeWidth="1.5"
            />
          );
        })}
      </svg>
      <div className="relative z-10 flex h-24 w-24 flex-col items-center justify-center rounded-2xl border border-violet-400/60 bg-gradient-to-br from-zinc-900 via-indigo-950 to-violet-950 shadow-[0_0_56px_rgba(99,102,241,0.5)]">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-violet-200">Founder</span>
        <span className="text-base font-bold text-white">NODE</span>
        <span className="mt-0.5 text-base" aria-hidden>
          🔒
        </span>
      </div>
      {NODE_ORBIT.map((node) => {
        const rad = (node.angle * Math.PI) / 180;
        const left = 50 + Math.cos(rad) * 38;
        const top = 50 + Math.sin(rad) * 38;
        return (
          <div
            key={node.label}
            className="absolute z-10 w-[84px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-zinc-700/80 bg-zinc-950/95 px-1.5 py-1 text-center shadow-lg backdrop-blur-sm"
            style={{ left: `${left}%`, top: `${top}%` }}
          >
            <p className="text-[9px] font-semibold text-white">{node.label}</p>
            <p className="text-[7px] leading-tight text-zinc-500">{node.sub}</p>
          </div>
        );
      })}
    </div>
  );
}

function PlatformStatsPanel({ stats }: { stats: PlatformStats | null }) {
  const statItems = [
    { label: 'Verified founders', value: stats ? formatStat(stats.verifiedFounders) : '—' },
    { label: 'Active projects', value: stats ? formatStat(stats.activeProjects) : '—' },
    { label: 'GitHub commits', value: stats ? formatStat(stats.githubCommits) : '—' },
    { label: 'Scout votes', value: stats ? formatStat(stats.scoutVotes) : '—' },
    { label: 'DDollar in ecosystem', value: stats ? formatUsd(stats.simulatedCapital, 1) : '—' },
    { label: 'Community members', value: stats ? formatStat(stats.communityMembers) : '—' },
  ];

  return (
    <div className="rounded-2xl border border-zinc-800/80 bg-zinc-950/60 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Platform stats</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {statItems.map((item) => (
          <div key={item.label} className="rounded-xl border border-zinc-800/60 bg-black/30 px-2.5 py-2">
            <p className="text-base font-bold text-white">{item.value}</p>
            <p className="mt-0.5 text-[9px] uppercase tracking-wider text-zinc-500">{item.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function FounderNodeCard() {
  return (
    <div className="rounded-2xl border border-emerald-500/25 bg-emerald-950/10 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">Founder Node</p>
      <div className="mt-2 flex h-16 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950/60">
        <span className="text-3xl" aria-hidden>
          🖥
        </span>
      </div>
      <ul className="mt-2 space-y-1 text-[11px] text-zinc-300">
        {[
          'Self-custody vault on your PC',
          'Your memory stays yours',
          'Metadata sync only',
          'Phala TEE encryption',
          'Private data stays private',
        ].map((line) => (
          <li key={line} className="flex gap-2">
            <span className="text-emerald-400">✓</span>
            {line}
          </li>
        ))}
      </ul>
      <Link href="/founder-node" className="mt-2 inline-block text-xs font-semibold text-emerald-300 hover:underline">
        Download Founder Node →
      </Link>
      <p className="mt-0.5 text-[9px] text-zinc-600">Works on Windows, macOS, Linux</p>
    </div>
  );
}

export function LandingSinglePage({ stats }: { stats: PlatformStats | null }) {
  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-4 px-4 py-4 sm:px-6 lg:space-y-5 lg:px-8 lg:py-5">
      {/* Hero row: copy | orbit | stats + node */}
      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)_minmax(0,0.75fr)] xl:items-start xl:gap-5">
        <div className="xl:pt-2">
          <h1 className="text-[2rem] font-bold uppercase leading-[0.92] tracking-tight text-white sm:text-[2.6rem] lg:text-[2.85rem]">
            Private by default.
            <br />
            <span className="bg-gradient-to-r from-violet-300 via-indigo-200 to-cyan-300 bg-clip-text text-transparent">
              Public by proof.
            </span>
          </h1>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-zinc-400">
            The operating system for crypto startups. Build with AI. Validate with community. Launch with trust.
          </p>
          <p className="mt-2 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-950/30 px-2.5 py-1 text-[10px] text-emerald-200">
            <span aria-hidden>🛡</span>
            Powered by Founder Node + Phala Network TEE · Your data. Your keys. Your startup.
          </p>
          <div className="mt-4 flex flex-wrap gap-2.5">
            <Link
              href="/founder-den"
              className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-900/30 hover:bg-violet-500"
            >
              Open Founder OS →
            </Link>
            <Link
              href="/list-your-project"
              className="rounded-xl border border-zinc-600 px-5 py-2.5 text-sm font-semibold text-white hover:border-violet-400/50"
            >
              List your project
            </Link>
          </div>
        </div>

        <div className="flex items-center justify-center xl:-mt-1">
          <FounderNodeVisual />
        </div>

        <div className="space-y-3">
          <PlatformStatsPanel stats={stats} />
          <FounderNodeCard />
        </div>
      </section>

      {/* Pipeline */}
      <section className="rounded-2xl border border-zinc-800/80 bg-zinc-950/40 p-3">
        <p className="text-center text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Build in public · Validate demand · Launch with trust
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map((step, i) => (
            <div key={step.step} className="relative rounded-xl border border-zinc-800/60 bg-black/20 p-2.5">
              {i < PIPELINE.length - 1 && (
                <span
                  className="absolute -right-1.5 top-1/2 hidden translate-x-full -translate-y-1/2 text-zinc-600 lg:inline"
                  aria-hidden
                >
                  →
                </span>
              )}
              <p className={`text-[10px] font-bold uppercase tracking-wider ${step.color}`}>{step.tag}</p>
              <p className="mt-0.5 text-sm font-bold text-white">{step.step}</p>
              <p className="mt-0.5 text-[11px] text-zinc-500">{step.desc}</p>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap justify-center gap-3 text-[10px] text-zinc-500">
          <span>GitHub</span>
          <span>·</span>
          <span>DDollar</span>
          <span>·</span>
          <span>Scouts</span>
          <span>·</span>
          <span>Raise Room</span>
        </div>
      </section>

      {/* Shrimp + DDollar */}
      <section className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-2xl border border-amber-500/20 bg-amber-950/10 p-3">
          <h2 className="text-base font-bold uppercase tracking-wide text-white">Built for shrimps, not whales</h2>
          <p className="mt-1 text-xs text-zinc-500">Everyone starts equal. Skill over capital.</p>
          <div className="mt-3 flex flex-wrap items-start gap-1">
            {SHRIMP_FLOW.map((step, i) => (
              <div key={step.label} className="flex items-center gap-1">
                <div className="rounded-lg border border-violet-500/30 bg-violet-950/40 px-2 py-1.5">
                  <p className="text-[10px] font-semibold text-violet-100">{step.label}</p>
                  <p className="text-[8px] text-zinc-500">{step.sub}</p>
                </div>
                {i < SHRIMP_FLOW.length - 1 && <span className="text-zinc-600">→</span>}
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-zinc-800/60 pt-2 text-[10px] text-zinc-500">
            {ANTI_PATTERNS.map((line) => (
              <span key={line}>{line}</span>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">The DDollar ecosystem</p>
              <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">
                DDollar is the ecosystem currency. No intrinsic value. Not redeemable for cash. Not an investment
                product. Used only for platform participation, reputation, predictions, scouting, and platform
                services.
              </p>
            </div>
            <div
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-violet-500/40 bg-gradient-to-br from-violet-600/40 to-indigo-900/60 text-xl font-bold text-violet-100 shadow-[0_0_24px_rgba(139,92,246,0.35)]"
              aria-hidden
            >
              D
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            {DDOLLAR_USES.map((item) => (
              <div
                key={item.label}
                className="flex items-center gap-1.5 rounded-lg border border-zinc-800/80 bg-black/20 px-2 py-1.5"
              >
                <span className="text-sm" aria-hidden>
                  {item.icon}
                </span>
                <span className="text-[10px] font-medium text-zinc-300">{item.label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Rules + footer modules */}
      <section className="grid gap-3 lg:grid-cols-[1.35fr_1fr]">
        <div className="rounded-2xl border border-zinc-800/80 bg-zinc-950/40 p-3">
          <p className="text-center text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
            Rules of the network
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {NETWORK_RULES.map((rule) => (
              <div key={rule.label} className="flex flex-col items-center gap-1 rounded-xl border border-zinc-800/50 bg-black/20 px-2 py-2.5 text-center">
                <span className="text-lg" aria-hidden>
                  {rule.icon}
                </span>
                <span className="text-[10px] font-semibold text-zinc-300">{rule.label}</span>
                <span className="text-[9px] text-zinc-500">{rule.sub}</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-center text-[10px] text-zinc-600">🛡 No bots allowed</p>
        </div>

        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
          <div className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-3">
            <p className="text-2xl font-bold text-white">80%</p>
            <p className="text-xs font-semibold text-violet-200">Community owned</p>
            <ul className="mt-1.5 space-y-0.5 text-[10px] text-zinc-400">
              <li>10% Airdropped</li>
              <li>70% Distributed over 10 years</li>
              <li>20% Team allocation</li>
            </ul>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-3">
            <p className="text-[9px] uppercase tracking-wider text-zinc-500">Built on Phala Network TEE</p>
            <p className="mt-1 text-[10px] font-semibold text-emerald-300">Enterprise-grade privacy</p>
          </div>
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-3">
            <p className="text-[9px] uppercase tracking-wider text-zinc-500">Use the models you trust</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {AI_PROVIDERS.map((name) => (
                <span
                  key={name}
                  className="rounded border border-zinc-700/80 bg-zinc-950/60 px-1.5 py-0.5 text-[9px] text-zinc-400"
                >
                  {name}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-zinc-800/80 pt-3 text-center text-[9px] text-zinc-600">
        © {new Date().getFullYear()} Doxxed Crypto · Private by default. Public by proof.
      </footer>
    </div>
  );
}
