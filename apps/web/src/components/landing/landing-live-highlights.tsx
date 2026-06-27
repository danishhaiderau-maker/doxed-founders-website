'use client';

import Link from 'next/link';
import { formatPercent, formatUsd } from '@dcf/utils';
import type {
  BuilderRewardsEntry,
  LeaderboardEntry,
  TradingAgentSummary,
} from '@/lib/api';

export type LandingHighlights = {
  topAgent: TradingAgentSummary | null;
  topTrader: LeaderboardEntry | null;
  topBuilder: BuilderRewardsEntry | null;
  agents: TradingAgentSummary[];
  traders: LeaderboardEntry[];
};

function HighlightCard({
  href,
  accent,
  eyebrow,
  title,
  metric,
  metricLabel,
  detail,
}: {
  href: string;
  accent: 'emerald' | 'amber' | 'violet' | 'sky';
  eyebrow: string;
  title: string;
  metric: string;
  metricLabel: string;
  detail: string;
}) {
  const border =
    accent === 'emerald'
      ? 'border-emerald-500/25 hover:border-emerald-400/45'
      : accent === 'amber'
        ? 'border-amber-500/25 hover:border-amber-400/45'
        : accent === 'violet'
          ? 'border-violet-500/25 hover:border-violet-400/45'
          : 'border-sky-500/25 hover:border-sky-400/45';
  const eyebrowColor =
    accent === 'emerald'
      ? 'text-emerald-400'
      : accent === 'amber'
        ? 'text-amber-400'
        : accent === 'violet'
          ? 'text-violet-400'
          : 'text-sky-400';

  return (
    <Link
      href={href}
      className={`group flex flex-col rounded-xl border bg-zinc-950/60 p-3.5 transition hover:bg-zinc-900/50 ${border}`}
    >
      <p className={`text-[9px] font-bold uppercase tracking-[0.18em] ${eyebrowColor}`}>{eyebrow}</p>
      <p className="mt-1 truncate text-sm font-semibold text-white group-hover:text-zinc-50">{title}</p>
      <p className="mt-2 text-xl font-bold text-white">{metric}</p>
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{metricLabel}</p>
      <p className="mt-2 flex-1 text-[11px] leading-snug text-zinc-500">{detail}</p>
      <span className="mt-2 text-[10px] font-semibold text-zinc-400 group-hover:text-white">View →</span>
    </Link>
  );
}

export function LandingLiveHighlights({ data }: { data: LandingHighlights | null }) {
  const agent = data?.topAgent;
  const trader = data?.topTrader;
  const builder = data?.topBuilder;

  return (
    <section aria-label="Live platform highlights">
      <div className="mb-2 flex items-end justify-between gap-3 px-0.5">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">Live on platform</p>
          <p className="mt-0.5 text-sm text-zinc-400">Real rankings — agents, traders, builders</p>
        </div>
        <Link href="/leaderboard" className="shrink-0 text-[11px] font-semibold text-zinc-500 hover:text-white">
          All rankings →
        </Link>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <HighlightCard
          href={agent ? `/agent-hub/${agent.slug}` : '/agent-hub'}
          accent="emerald"
          eyebrow="Agent"
          title={agent?.name ?? 'BTC Conservative Agent'}
          metric={agent ? formatPercent(agent.netReturnPct) : '—'}
          metricLabel="Net return · live showcase"
          detail={
            agent
              ? `${formatUsd(agent.equityUsd)} equity · ${agent.tradeCount} trades · hire with DDollar`
              : 'Copy-trade the admin showcase bot — verified PnL on-chain exchange'
          }
        />
        <HighlightCard
          href="/leaderboard"
          accent="amber"
          eyebrow="Top trader"
          title={trader?.displayName ?? 'Paper desk leader'}
          metric={trader ? formatUsd(trader.pnl) : '—'}
          metricLabel="Simulated PnL · verified rank"
          detail={
            trader
              ? `${formatPercent(trader.roi)} ROI · ${formatUsd(trader.totalValue)} portfolio value`
              : 'Paper trade with free DDollar — skill over capital'
          }
        />
        <HighlightCard
          href={trader ? `/portfolio/${trader.userId}` : '/paper-trading'}
          accent="sky"
          eyebrow="Portfolio"
          title={trader?.displayName ?? 'Top portfolio'}
          metric={trader ? formatPercent(trader.roi) : '—'}
          metricLabel="Return on simulated capital"
          detail={
            trader
              ? `Rank #${trader.rank} on the leaderboard — follow conviction, not hype`
              : 'See how top traders allocate DDollar paper capital'
          }
        />
        <HighlightCard
          href="/builder-rewards"
          accent="violet"
          eyebrow="Builder rewards"
          title={builder?.displayName ?? 'Top builder'}
          metric={builder ? `${builder.rewardSharePercent.toFixed(2)}%` : '—'}
          metricLabel="Estimated airdrop share"
          detail={
            builder
              ? `Score ${Math.round(builder.builderScore)} · ${builder.tierLabel} tier · ship in public to climb`
              : 'Builders who contribute earn share of the community airdrop pool'
          }
        />
      </div>
    </section>
  );
}
