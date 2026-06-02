'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { formatUsd, STARTING_CASH_USD } from '@dcf/utils';
import { SiteBrand } from '@/components/site-nav';
import { NotificationBell } from '@/components/notification-bell';
import { LandingFeatureHub } from '@/components/landing/landing-feature-hub';
import type { PlatformStats } from '@/lib/api';

function formatStat(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return value.toLocaleString();
  return value.toLocaleString();
}

const WHY_DOXXED = [
  {
    title: 'Scam culture',
    body: 'Anonymous founders extracted billions.',
    border: 'border-red-500/35',
    accent: 'text-red-300',
  },
  {
    title: 'Broken incentives',
    body: 'Retail chases memes. Builders get ignored.',
    border: 'border-amber-500/35',
    accent: 'text-amber-300',
  },
  {
    title: 'Our solution',
    body: 'Trade doxxed founders. Reward conviction. Bring back HODL culture.',
    border: 'border-emerald-500/35',
    accent: 'text-emerald-300',
  },
] as const;

const NODE_ORBIT = [
  { label: 'GitHub', sub: 'Commits & updates', angle: -58 },
  { label: 'Phala TEE', sub: 'Hardware encryption', angle: -8 },
  { label: 'Your Data', sub: 'Encrypted locally', angle: 48 },
  { label: 'AI Models', sub: 'Models you trust', angle: 118 },
  { label: 'Community', sub: 'Proof & conviction', angle: 178 },
  { label: 'Founder OS', sub: 'Plan · ship · launch', angle: 238 },
];

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
  { icon: '🔨', title: 'Build in public', sub: '100% community' },
  { icon: '🔒', title: 'Private by default', sub: 'Paper first' },
  { icon: '🛡', title: 'Verified humans', sub: 'Transparency first' },
  { icon: '📊', title: 'Proof over hype', sub: 'No paid shilling' },
  { icon: '👥', title: '100% community', sub: 'Closed system' },
  { icon: '💵', title: 'Paper first', sub: 'No outside money' },
  { icon: '✓', title: 'Transparency first', sub: 'Fair game' },
];

const AI_PROVIDERS = ['DeepSeek', 'OpenAI', 'Claude', 'Gemini', 'OpenRouter', 'Ollama', 'Cursor', 'OpenHands'];

const PRODUCT_TRIAD = [
  {
    id: 'founder-os',
    title: 'Founder OS',
    tagline: 'The cockpit',
    subtitle: 'Mission control for serious builders',
    href: '/founder-den',
    cta: 'Open Mission Control →',
    border: 'border-violet-500/35',
    headerBg: 'bg-violet-950/40',
    accent: 'text-violet-300',
    items: [
      { label: 'Mission Control', icon: '◆' },
      { label: 'Tasks', icon: '☑' },
      { label: 'Agents', icon: '🤖' },
      { label: 'Copilot', icon: '✦' },
      { label: 'Progress', icon: '📈' },
    ],
  },
  {
    id: 'founder-node',
    title: 'Founder Node',
    tagline: 'The vault',
    subtitle: 'Self-custody memory & private data',
    href: '/settings/builder',
    cta: 'Download Founder Node →',
    border: 'border-emerald-500/35',
    headerBg: 'bg-emerald-950/35',
    accent: 'text-emerald-300',
    items: [
      { label: 'Self custody', icon: '🔐' },
      { label: 'Encrypted memory', icon: '💾' },
      { label: 'Private data', icon: '🛡' },
      { label: 'TEE protected', icon: '🔒' },
      { label: 'Local vault', icon: '🖥' },
    ],
  },
  {
    id: 'ai-stack',
    title: 'AI Stack',
    tagline: 'The brain',
    subtitle: 'Your keys · your models · we orchestrate',
    href: '/settings/builder',
    cta: 'Connect AI providers →',
    border: 'border-sky-500/35',
    headerBg: 'bg-sky-950/30',
    accent: 'text-sky-300',
    items: [
      { label: 'DeepSeek', icon: '◉' },
      { label: 'OpenAI', icon: '◉' },
      { label: 'Claude', icon: '◉' },
      { label: 'Gemini', icon: '◉' },
      { label: 'Cursor', icon: '◉' },
      { label: 'OpenHands', icon: '◉' },
    ],
  },
] as const;

function LandingProductTriad() {
  return (
    <section className="grid gap-3 lg:grid-cols-3">
      {PRODUCT_TRIAD.map((product) => (
        <Card
          key={product.id}
          className={`flex flex-col border ${product.border} bg-gradient-to-b from-zinc-950/90 to-black/40`}
        >
          <div className={`border-b border-zinc-800/80 px-4 py-3 ${product.headerBg}`}>
            <p className={`text-[10px] font-bold uppercase tracking-[0.22em] ${product.accent}`}>
              {product.tagline}
            </p>
            <h3 className="mt-1 text-lg font-bold text-white">{product.title}</h3>
            <p className="mt-0.5 text-[11px] text-zinc-500">{product.subtitle}</p>
          </div>
          <ul className="flex flex-1 flex-col gap-1 px-3 py-3">
            {product.items.map((item) => (
              <li
                key={item.label}
                className="flex items-center gap-2.5 rounded-lg border border-zinc-800/60 bg-black/25 px-2.5 py-2"
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-zinc-800 bg-zinc-900 text-xs">
                  {item.icon}
                </span>
                <span className="text-[11px] font-medium text-zinc-200">{item.label}</span>
              </li>
            ))}
          </ul>
          <div className="border-t border-zinc-800/80 px-4 py-3">
            <Link href={product.href} className={`text-xs font-semibold ${product.accent} hover:underline`}>
              {product.cta}
            </Link>
          </div>
        </Card>
      ))}
    </section>
  );
}

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
  const { data: session } = useSession();

  return (
    <header className="border-b border-zinc-800/80 bg-[#050508]">
      <div className="mx-auto flex w-full max-w-[88rem] items-center justify-between gap-3 px-4 py-3 lg:px-8">
        <SiteBrand className="text-sm font-bold tracking-tight uppercase" />
        <div className="flex items-center gap-2">
          <NotificationBell />
          {session ? (
            <Link
              href="/account"
              className="rounded-lg border border-zinc-600 px-3 py-1.5 text-sm text-zinc-200 hover:text-white"
            >
              Profile
            </Link>
          ) : (
            <Link href="/login" className="rounded-lg border border-zinc-600 px-3 py-1.5 text-sm text-zinc-200">
              Login
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}

function FounderNodeVisual() {
  return (
    <div className="relative mx-auto flex aspect-square w-full max-w-[340px] items-center justify-center">
      <div className="pointer-events-none absolute inset-[6%] rounded-full border border-violet-500/30 shadow-[0_0_60px_rgba(139,92,246,0.25)]" />
      <div className="pointer-events-none absolute inset-[14%] rounded-full border border-violet-400/15" />
      <div className="pointer-events-none absolute inset-0 rounded-full bg-[radial-gradient(circle,rgba(99,102,241,0.35),transparent_70%)]" />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 320 320" aria-hidden>
        {NODE_ORBIT.map((node) => {
          const rad = (node.angle * Math.PI) / 180;
          return (
            <line
              key={node.label}
              x1="160"
              y1="160"
              x2={160 + Math.cos(rad) * 118}
              y2={160 + Math.sin(rad) * 118}
              stroke="rgba(139,92,246,0.45)"
              strokeWidth="1.5"
            />
          );
        })}
      </svg>
      <div className="relative z-10 flex h-[5.5rem] w-[5.5rem] flex-col items-center justify-center rounded-2xl border border-violet-400/70 bg-gradient-to-br from-zinc-900 via-violet-950 to-indigo-950 shadow-[0_0_64px_rgba(99,102,241,0.55)]">
        <span className="text-[8px] font-bold uppercase tracking-[0.25em] text-violet-200">Founder</span>
        <span className="text-lg font-bold text-white">NODE</span>
        <span className="text-sm" aria-hidden>
          🔒
        </span>
      </div>
      {NODE_ORBIT.map((node) => {
        const rad = (node.angle * Math.PI) / 180;
        const left = 50 + Math.cos(rad) * 39;
        const top = 50 + Math.sin(rad) * 39;
        return (
          <div
            key={node.label}
            className="absolute z-10 w-[86px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-zinc-700/90 bg-[#0a0a12]/95 px-1.5 py-1.5 text-center shadow-lg"
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
    <Card className="border-emerald-500/20 bg-gradient-to-br from-emerald-950/20 to-zinc-950/80 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">Founder Node</p>
      <div className="mt-2 flex h-[4.5rem] items-center justify-center rounded-xl border border-zinc-800 bg-black/50">
        <div className="flex h-12 w-16 items-center justify-center rounded-lg border border-zinc-700 bg-zinc-900 shadow-inner">
          <span className="text-2xl" aria-hidden>
            🖥
          </span>
        </div>
      </div>
      <ul className="mt-2.5 space-y-1 text-[11px] text-zinc-300">
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
      <Link href="/settings/builder" className="mt-2 inline-block text-xs font-semibold text-emerald-300 hover:underline">
        Download Founder Node →
      </Link>
      <p className="mt-0.5 text-[9px] text-zinc-600">Works on Windows, macOS, Linux</p>
    </Card>
  );
}

export function LandingSinglePage({ stats }: { stats: PlatformStats | null }) {
  const pendingReviews = stats?.projectsAwaitingReview ?? 12;

  return (
    <div className="mx-auto w-full max-w-[88rem] space-y-3 px-4 py-3 sm:px-6 lg:space-y-3.5 lg:px-8 lg:py-4">
      {/* Row 1: Hero V3 — privacy → AI → mission → anti-scam */}
      <section>
        <Card className="overflow-hidden border-violet-500/15 bg-gradient-to-b from-violet-950/15 via-zinc-950/80 to-[#050508] p-5 lg:p-8">
          <p className="text-[10px] font-bold uppercase tracking-[0.32em] text-violet-300/90">
            Private by default.
            <span className="text-zinc-500"> · </span>
            Public by proof.
          </p>

          <h2 className="mt-5 max-w-3xl text-2xl font-bold leading-tight text-white sm:text-3xl lg:text-[2.15rem]">
            Use the AI you trust.
            <span className="block bg-gradient-to-r from-violet-200 via-indigo-200 to-sky-200 bg-clip-text text-transparent">
              We orchestrate the rest.
            </span>
          </h2>

          <p className="mt-4 text-base font-medium text-zinc-300 sm:text-lg">
            The operating system for crypto startups.
            <span className="mt-1 block text-sm font-normal text-zinc-500">
              Build with AI · Validate with community · Launch with trust
            </span>
          </p>

          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400">
            Build in public.
            <span className="text-zinc-600"> · </span>
            Validate demand.
            <span className="text-zinc-600"> · </span>
            Launch with trust.
          </p>

          {/* TEE strip */}
          <div className="mt-6 flex flex-col gap-2 rounded-xl border border-emerald-500/25 bg-emerald-950/15 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300/90">
              Powered by Founder Node + Phala Network TEE
            </p>
            <p className="text-xs font-medium text-emerald-100/90">
              Your data.
              <span className="text-emerald-400/60"> · </span>
              Your keys.
              <span className="text-emerald-400/60"> · </span>
              Your AI.
            </p>
          </div>

          <div className="my-8 h-px bg-gradient-to-r from-transparent via-zinc-700/80 to-transparent" />

          <h1 className="text-[1.65rem] font-bold uppercase leading-[0.95] tracking-tight text-white sm:text-[2.1rem] xl:text-[2.45rem]">
            If you want my money,
            <br />
            <span className="bg-gradient-to-r from-red-300 via-rose-200 to-amber-200 bg-clip-text text-transparent">
              show me your face.
            </span>
          </h1>

          <p className="mt-4 text-lg font-semibold text-zinc-200">Trade founders. Not excuses.</p>

          <div className="mt-4 max-w-xl space-y-2 text-sm leading-relaxed text-zinc-400">
            <p>Scammers hide behind anonymity. Builders stand behind their work.</p>
            <p>
              Track doxxed founders, validate demand with DDollar, and back projects building in public.
            </p>
          </div>

          <div className="mt-6 flex flex-wrap gap-2">
            <Link
              href="/founder-den"
              className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 hover:bg-violet-500"
            >
              Open Mission Control →
            </Link>
            <Link
              href="/list-your-project"
              className="rounded-xl border border-zinc-600 px-5 py-2.5 text-sm font-semibold text-white hover:border-zinc-500"
            >
              Apply for listing
              <span className="mt-0.5 block text-[10px] font-normal text-zinc-500">Doxxed founders only</span>
            </Link>
            <Link
              href="/trust-center?tab=scout-voting"
              className="rounded-xl border border-amber-500/40 bg-amber-950/25 px-5 py-2.5 text-sm font-semibold text-amber-100 hover:border-amber-400/60"
            >
              Scout voting ({pendingReviews})
              <span className="mt-0.5 block text-[10px] font-normal text-amber-200/70">
                {pendingReviews} project{pendingReviews === 1 ? '' : 's'} awaiting community review
              </span>
            </Link>
          </div>
        </Card>
      </section>

      {/* Row 1b: Three core products */}
      <LandingProductTriad />

      {/* Row 2: Navigation V5 hub + live preview widgets */}
      <LandingFeatureHub scoutPending={pendingReviews} platformStats={stats} />

      {/* Row 3: Platform stats */}
      <section>
        <PlatformStatsPanel stats={stats} />
      </section>

      {/* Row 4: Why Doxxed exists */}
      <section>
        <Card className="p-4 lg:p-5">
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-zinc-500">Why Doxxed exists</p>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {WHY_DOXXED.map((card) => (
              <div
                key={card.title}
                className={`rounded-xl border bg-black/30 p-4 ${card.border}`}
              >
                <p className={`text-xs font-bold uppercase tracking-wider ${card.accent}`}>{card.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-zinc-300">{card.body}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>

      {/* Row 3: Founder Node — how it works */}
      <section className="grid gap-3 lg:grid-cols-[1.15fr_0.85fr]">
        <Card className="flex flex-col items-center justify-center border-violet-500/15 bg-gradient-to-b from-violet-950/20 to-transparent p-4">
          <p className="mb-2 text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">
            How Founder Node works
          </p>
          <FounderNodeVisual />
          <p className="mt-3 max-w-sm text-center text-[11px] leading-relaxed text-zinc-500">
            Your vault stays on your machine. Founder OS coordinates build feed, scouts, and launch — you keep the keys.
          </p>
        </Card>
        <FounderNodeProductCard />
      </section>

      {/* Row 4: Pipeline */}
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

      {/* Row 5: Shrimp | DDollar */}
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
          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-zinc-800/60 pt-2 text-[9px] text-zinc-500">
            <span>No scams</span>
            <span>·</span>
            <span>No pump & dumps</span>
            <span>·</span>
            <span>No extractors</span>
            <span>·</span>
            <span>No outside money</span>
            <span>·</span>
            <span>Closed system · Fair game</span>
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

      {/* Row 4: Rules 2×4 */}
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
        <p className="mt-2 text-center text-[10px] text-zinc-600">🛡 No bots allowed</p>
      </Card>

      {/* Row 5: Footer modules */}
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
          <p className="text-[9px] uppercase tracking-wider text-zinc-500">Built on Phala Network TEE</p>
          <p className="mt-1 text-sm font-semibold text-emerald-300">Enterprise-grade privacy</p>
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
