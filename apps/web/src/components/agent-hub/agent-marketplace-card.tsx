'use client';

import Link from 'next/link';
import { formatPercent, formatUsd, TRADING_AGENT_STATUS_LABELS } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

const STATUS_STYLE: Record<string, string> = {
  LIVE: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  TESTING: 'bg-amber-500/20 text-amber-200 border-amber-500/40',
  PAUSED: 'bg-zinc-700/40 text-zinc-400 border-zinc-600',
};

export function AgentMarketplaceCard({
  agent,
  onFollow,
  followBusy,
  featured,
}: {
  agent: TradingAgentSummary;
  onFollow?: () => void;
  followBusy?: boolean;
  featured?: boolean;
}) {
  const isPaused = agent.status === 'PAUSED';
  const statusClass = STATUS_STYLE[agent.status] ?? STATUS_STYLE.PAUSED;

  return (
    <article
      className={`group relative overflow-hidden rounded-2xl border bg-gradient-to-br from-zinc-900/80 to-black/60 p-5 transition hover:border-emerald-500/40 ${
        featured ? 'border-emerald-500/30 shadow-lg shadow-emerald-950/30' : 'border-zinc-800'
      }`}
    >
      {agent.isExperimental && (
        <span className="absolute right-3 top-3 rounded-full border border-amber-500/40 bg-amber-950/60 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-200">
          Beta
        </span>
      )}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-white">{agent.name}</h3>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${statusClass}`}>
              {TRADING_AGENT_STATUS_LABELS[agent.status] ?? agent.status}
            </span>
            {agent.botConnected && (
              <span className="flex items-center gap-1 text-[10px] text-emerald-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                Live
              </span>
            )}
            {agent.currentAction === 'ORDER PENDING' && (
              <span className="rounded-full border border-blue-500/40 bg-blue-950/40 px-2 py-0.5 text-[10px] font-bold uppercase text-blue-200">
                Limit pending
              </span>
            )}
            {agent.hired && agent.instanceMode === 'copy' && (
              <span className="rounded-full border border-violet-500/40 bg-violet-950/40 px-2 py-0.5 text-[10px] font-bold uppercase text-violet-200">
                Your $500 track
              </span>
            )}
          </div>
        </div>
        <span className="rounded-lg bg-zinc-800 px-2 py-1 text-xs font-semibold text-zinc-300">
          {agent.assetSymbol}
        </span>
      </div>

      {!isPaused && (
        <>
          <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Win rate</dt>
              <dd className="text-lg font-bold text-white">{agent.winRatePct.toFixed(0)}%</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">30D return</dt>
              <dd className={`text-lg font-bold ${agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {formatPercent(agent.netReturnPct)}
              </dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Trades</dt>
              <dd className="text-lg font-bold text-white">{agent.tradeCount}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Followers</dt>
              <dd className="font-semibold text-zinc-200">{agent.followerCount.toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Equity</dt>
              <dd className="font-semibold text-zinc-200">{formatUsd(agent.equityUsd, 0)}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Position</dt>
              <dd className="font-semibold text-amber-200">{agent.currentPosition ?? 'FLAT'}</dd>
            </div>
            {agent.currentAction && agent.currentAction !== 'WAITING' && (
              <div className="col-span-2 sm:col-span-3">
                <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Status</dt>
                <dd className="text-sm font-semibold text-blue-300">{agent.currentAction}</dd>
              </div>
            )}
          </dl>

          <p className="mt-3 text-xs text-zinc-500">
            Exchange: Bitfinex (recommended) · Tested for hire
          </p>

          <div className="mt-5 flex flex-wrap gap-2">
            <Link
              href={`/agent-hub/${agent.slug}`}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
            >
              Observe live
            </Link>
            <Link
              href={`/agent-hub/${agent.slug}/hire`}
              className="rounded-lg border border-violet-500/50 bg-violet-950/30 px-4 py-2 text-sm font-semibold text-violet-100 hover:bg-violet-900/40"
            >
              Hire agent
            </Link>
            {onFollow && (
              <button
                type="button"
                onClick={onFollow}
                disabled={followBusy}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:border-zinc-500 disabled:opacity-50"
              >
                {agent.following ? 'Following' : 'Follow'}
              </button>
            )}
          </div>
        </>
      )}

      {isPaused && (
        <p className="mt-4 text-sm text-zinc-500">Coming soon — builder onboarding in progress.</p>
      )}
    </article>
  );
}

export function AgentBetaWarningBanner() {
  return (
    <div className="rounded-2xl border border-amber-500/40 bg-amber-950/25 px-5 py-4">
      <p className="text-sm font-bold text-amber-100">Experimental beta — max $500 allocation</p>
      <p className="mt-1 text-xs text-amber-200/80">
        Agents are under active testing. Past performance does not guarantee future results. High risk.
      </p>
    </div>
  );
}
