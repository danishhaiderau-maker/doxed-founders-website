'use client';

import {
  TRADING_AGENT_KIND_LABELS,
  TRADING_AGENT_STATUS_LABELS,
  formatPercent,
  formatUsd,
} from '@dcf/utils';
import Link from 'next/link';
import type { TradingAgentSummary } from '@/lib/api';

const STATUS_DOT: Record<string, string> = {
  TESTING: 'bg-amber-400',
  LIVE: 'bg-emerald-400',
  PAUSED: 'bg-zinc-500',
  RETIRED: 'bg-red-400',
};

export function AgentWarningBanner() {
  return (
    <div className="rounded-2xl border border-red-500/40 bg-red-950/30 px-5 py-4">
      <p className="text-sm font-bold text-red-200">⚠ EXPERIMENTAL AGENT</p>
      <ul className="mt-2 space-y-1 text-sm text-red-100/90">
        <li>This agent is currently in research mode.</li>
        <li>Performance is not guaranteed.</li>
        <li>Historical performance does not predict future results.</li>
        <li>Use DDollar only.</li>
        <li>Live deployment is not yet enabled.</li>
      </ul>
    </div>
  );
}

export function TradingAgentCard({
  agent,
  onFollow,
  followBusy,
}: {
  agent: TradingAgentSummary;
  onFollow?: () => void;
  followBusy?: boolean;
}) {
  const isPaused = agent.status === 'PAUSED';
  const returnColor = agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <article className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-white">{agent.name}</h3>
          {isPaused ? (
            <p className="mt-1 text-xs text-zinc-500">Coming soon</p>
          ) : (
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
              <span className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[agent.status] ?? 'bg-zinc-500'}`} />
              Status: {TRADING_AGENT_STATUS_LABELS[agent.status] ?? agent.status}
              {agent.botConnected && (
                <span className="rounded bg-emerald-950/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-300">
                  Bot live
                </span>
              )}
            </p>
          )}
        </div>
        {!isPaused && (
          <span className="rounded-lg bg-amber-950/50 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-amber-200">
            {agent.assetSymbol}
          </span>
        )}
      </div>

      {!isPaused && (
        <>
          <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Live since</dt>
              <dd className="font-medium text-zinc-200">{agent.liveSinceDays} days</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current balance</dt>
              <dd className="font-medium text-zinc-200">{formatUsd(agent.balanceUsd, 0)}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current equity</dt>
              <dd className="font-medium text-zinc-200">{formatUsd(agent.equityUsd, 0)}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Net return</dt>
              <dd className={`font-semibold ${returnColor}`}>{formatPercent(agent.netReturnPct)}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Trades</dt>
              <dd className="font-medium text-zinc-200">{agent.tradeCount}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Win rate</dt>
              <dd className="font-medium text-zinc-200">{agent.winRatePct.toFixed(0)}%</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current position</dt>
              <dd className="font-medium text-zinc-200">{agent.currentPosition ?? 'NONE'}</dd>
            </div>
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-zinc-500">Current action</dt>
              <dd className="font-medium text-amber-200">{agent.currentAction ?? 'WAITING'}</dd>
            </div>
          </dl>

          <p className="mt-4 text-xs text-zinc-500">
            Cost: <span className="font-semibold text-violet-300">{agent.costDdollarDay.toLocaleString()} DDollar / day</span>
          </p>

          <div className="mt-5 flex flex-wrap gap-2">
            <Link
              href={`/agent-hub/${agent.slug}`}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
            >
              View Live Dashboard
            </Link>
            {onFollow && (
              <button
                type="button"
                onClick={onFollow}
                disabled={followBusy}
                className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-200 hover:border-violet-500/50 hover:text-white disabled:opacity-50"
              >
                {agent.following ? 'Following' : 'Follow Agent'}
              </button>
            )}
          </div>
        </>
      )}
    </article>
  );
}

export function AgentHubKindTabs({
  kinds,
  active,
  onChange,
}: {
  kinds: string[];
  active: string;
  onChange: (kind: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        onClick={() => onChange('')}
        className={`rounded-lg px-3 py-1.5 text-xs ${!active ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
      >
        All
      </button>
      {kinds.map((kind) => (
        <button
          key={kind}
          type="button"
          onClick={() => onChange(kind)}
          className={`rounded-lg px-3 py-1.5 text-xs ${active === kind ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          {TRADING_AGENT_KIND_LABELS[kind] ?? kind}
        </button>
      ))}
    </div>
  );
}
