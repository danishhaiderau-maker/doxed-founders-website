'use client';

import { formatPercent, formatUsd } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

/** Compact global showcase bot strip — shown above live copy / relay sim desks. */
export function ShowcaseReferenceBar({
  showcaseAgent,
  botConnected,
}: {
  showcaseAgent: TradingAgentSummary;
  botConnected?: boolean;
}) {
  const runway = showcaseAgent.startingBalance || 500;
  const equity = showcaseAgent.equityUsd ?? runway;
  const sessionPnl = showcaseAgent.sessionPnlUsd ?? equity - runway;
  const online = botConnected !== false;

  return (
    <section className="rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-950/25 to-zinc-950/60 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-violet-300">
            Global showcase bot · :7002
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            Admin research bot reference — same signals your copy or sim mirrors
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
            online
              ? 'bg-emerald-500/20 text-emerald-200'
              : 'bg-red-500/20 text-red-200'
          }`}
        >
          {online ? 'Online' : 'Offline'}
        </span>
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-4">
        <Metric label="Equity" value={formatUsd(equity, 0)} />
        <Metric
          label="Session P&L"
          value={`${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`}
          accent={sessionPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}
        />
        <Metric label="Return" value={formatPercent(showcaseAgent.netReturnPct ?? 0)} />
        <Metric label="Trades" value={String(showcaseAgent.tradeCount ?? 0)} />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  accent = 'text-white',
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-zinc-500">{label}</p>
      <p className={`mt-0.5 text-base font-bold ${accent}`}>{value}</p>
    </div>
  );
}
