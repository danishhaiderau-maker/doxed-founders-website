'use client';

import Link from 'next/link';
import type { TradingAgentSummary } from '@/lib/api';
import { formatUsd } from '@dcf/utils';

export function AgentHubBottomBanner({ agents }: { agents: TradingAgentSummary[] }) {
  const live = agents.filter((a) => a.status !== 'PAUSED');
  const activeCount = live.length;
  const avgWin =
    live.length > 0 ? live.reduce((s, a) => s + a.winRatePct, 0) / live.length : 0;
  const totalVolume = live.reduce((s, a) => s + a.equityUsd, 0);

  return (
    <section className="mt-8 overflow-hidden rounded-2xl border border-violet-500/30 bg-gradient-to-r from-violet-950/50 via-zinc-950 to-indigo-950/40 px-6 py-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-lg font-black tracking-wide text-white sm:text-xl">
            HIRE · COPY · PROFIT
          </p>
          <p className="mt-1 text-xs text-zinc-400">
            {activeCount} active agents · {formatUsd(totalVolume, 0)} volume · {avgWin.toFixed(0)}% avg win rate
          </p>
        </div>
        <Link
          href="/agent-hub"
          className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
        >
          Explore all agents
        </Link>
      </div>
    </section>
  );
}
