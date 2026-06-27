'use client';

import Link from 'next/link';
import { formatPercent, formatUsd } from '@dcf/utils';
import type { LandingHighlights } from '@/components/landing/landing-live-highlights';
import type { PlatformStats } from '@/lib/api';

function PulseCell({
  href,
  label,
  value,
  sub,
  accent,
}: {
  href: string;
  label: string;
  value: string;
  sub: string;
  accent: 'emerald' | 'amber' | 'violet' | 'zinc';
}) {
  const ring =
    accent === 'emerald'
      ? 'hover:border-emerald-500/40'
      : accent === 'amber'
        ? 'hover:border-amber-500/40'
        : accent === 'violet'
          ? 'hover:border-violet-500/40'
          : 'hover:border-zinc-600';

  return (
    <Link
      href={href}
      className={`group flex min-w-[9.5rem] flex-1 flex-col rounded-xl border border-zinc-800/80 bg-zinc-950/50 px-3 py-2.5 transition hover:bg-zinc-900/60 ${ring}`}
    >
      <p className="text-[9px] font-bold uppercase tracking-[0.16em] text-zinc-500">{label}</p>
      <p className="mt-0.5 truncate text-base font-bold text-white">{value}</p>
      <p className="mt-0.5 line-clamp-2 text-[10px] leading-snug text-zinc-500 group-hover:text-zinc-400">{sub}</p>
    </Link>
  );
}

export function LandingPulseBar({
  data,
  stats,
  scoutPending = 0,
}: {
  data: LandingHighlights | null;
  stats: PlatformStats | null;
  scoutPending?: number;
}) {
  const agent = data?.topAgent;
  const trader = data?.topTrader;
  const builder = data?.topBuilder;

  return (
    <section aria-label="Live platform pulse" className="rounded-2xl border border-zinc-800/90 bg-[#07070c] p-2 sm:p-3">
      <div className="mb-2 flex items-center justify-between gap-2 px-1">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">Live pulse</p>
        <Link href="/leaderboard" className="text-[10px] font-semibold text-zinc-500 hover:text-white">
          Rankings →
        </Link>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <PulseCell
          href={agent ? `/agent-hub/${agent.slug}` : '/agent-hub'}
          label="BTC agent"
          value={agent ? formatPercent(agent.netReturnPct) : '—'}
          sub={agent ? `${formatUsd(agent.equityUsd)} equity · ${agent.tradeCount} trades` : 'Showcase bot · copy trade'}
          accent="emerald"
        />
        <PulseCell
          href="/leaderboard"
          label="Top trader"
          value={trader ? formatUsd(trader.pnl) : '—'}
          sub={trader ? `${trader.displayName} · ${formatPercent(trader.roi)} ROI` : 'Paper desk · free DDollar'}
          accent="amber"
        />
        <PulseCell
          href="/builder-rewards"
          label="Top builder"
          value={builder ? `${builder.rewardSharePercent.toFixed(2)}%` : '—'}
          sub={builder ? `${builder.displayName} · ${builder.tierLabel}` : 'Airdrop share by contribution'}
          accent="violet"
        />
        <PulseCell
          href="/trust-center?tab=scout-voting"
          label="Scout queue"
          value={String(scoutPending)}
          sub={`Project${scoutPending === 1 ? '' : 's'} awaiting community review`}
          accent="amber"
        />
        <PulseCell
          href="/ddollar"
          label="Ecosystem"
          value={stats ? formatUsd(stats.simulatedCapital, 0) : '—'}
          sub={stats ? `${stats.communityMembers.toLocaleString()} members · simulated capital` : 'DDollar paper economy'}
          accent="zinc"
        />
      </div>
    </section>
  );
}
