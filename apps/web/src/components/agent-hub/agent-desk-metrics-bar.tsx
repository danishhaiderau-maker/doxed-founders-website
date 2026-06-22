'use client';

import { formatPercent, formatUsd } from '@dcf/utils';
import type { CopyRelaySimState } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import type { AgentDeskId } from '@/components/agent-hub/agent-desk-switcher';

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-300';
}

type MetricCell = { label: string; value: string; hint?: string; accent?: string };

export function AgentDeskMetricsBar({
  activeDesk,
  userAgent,
  showcaseAgent,
  copyRelaySim,
  exchangeLabel,
  isLiveSession,
  instanceStatus,
}: {
  activeDesk: AgentDeskId;
  userAgent: TradingAgentSummary;
  showcaseAgent: TradingAgentSummary;
  copyRelaySim?: CopyRelaySimState | null;
  exchangeLabel?: string | null;
  isLiveSession: boolean;
  instanceStatus?: string | null;
}) {
  const exchange = exchangeLabel ?? 'Bitfinex';
  let title = '';
  let borderClass = '';
  let badgeClass = '';
  let cells: MetricCell[] = [];

  if (activeDesk === 'showcase') {
    const runway = showcaseAgent.startingBalance || 500;
    const equity = showcaseAgent.equityUsd ?? runway;
    const sessionPnl = showcaseAgent.sessionPnlUsd ?? equity - runway;
    const dailyPnl = showcaseAgent.dailyPnlUsd ?? sessionPnl;
    title = 'Research showcase · home bot';
    borderClass = 'border-violet-500/30 from-violet-950/20';
    badgeClass = 'text-violet-300';
    cells = [
      { label: 'Paper runway', value: formatUsd(runway, 0), hint: 'Admin research session' },
      { label: 'Current equity', value: formatUsd(equity, 0), hint: 'Cash + mark-to-market' },
      {
        label: "Today's P&L",
        value: `${dailyPnl >= 0 ? '+' : ''}${formatUsd(dailyPnl, 2)}`,
        accent: pnlColor(dailyPnl),
        hint: 'UTC session day',
      },
      {
        label: 'Session P&L',
        value: `${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`,
        accent: pnlColor(sessionPnl),
        hint: formatPercent(showcaseAgent.netReturnPct ?? 0),
      },
    ];
  } else if (activeDesk === 'relay-sim') {
    const sim = copyRelaySim;
    const showcasePnl = showcaseAgent.sessionPnlUsd ?? sim?.showcasePnlUsd ?? 0;
    const startingUsd = sim?.ledger?.startingUsd ?? 500;
    const simPnl = sim?.sessionPnlUsd ?? 0;
    const paperBalance = sim?.ledger?.derivativesUsd ?? startingUsd;
    const paperEquity = startingUsd + simPnl;
    title = `${exchange} relay simulation`;
    borderClass = 'border-sky-500/30 from-sky-950/20';
    badgeClass = 'text-sky-300';
    cells = [
      { label: 'Paper balance', value: formatUsd(paperBalance, 2), hint: 'Sim derivatives wallet' },
      { label: 'Sim equity', value: formatUsd(paperEquity, 2), hint: '$500 start + session P&L' },
      {
        label: 'Sim session P&L',
        value: `${simPnl >= 0 ? '+' : ''}${formatUsd(simPnl, 2)}`,
        accent: pnlColor(simPnl),
        hint: sim?.active ? 'Simulation running' : 'Start sim to track',
      },
      {
        label: 'Showcase P&L (ref)',
        value: `${showcasePnl >= 0 ? '+' : ''}${formatUsd(showcasePnl, 2)}`,
        accent: pnlColor(showcasePnl),
        hint: 'Compare drift vs admin bot',
      },
    ];
  } else if (isLiveSession) {
    const freeMargin = userAgent.exchangeBalanceUsd ?? 0;
    const equity = userAgent.equityUsd ?? freeMargin;
    const sessionPnl = userAgent.sessionPnlUsd ?? 0;
    const unrealized = userAgent.unrealizedPnlUsd ?? 0;
    const paused = instanceStatus === 'PAUSED';
    title = paused ? `${exchange} live relay · paused` : `${exchange} live copy`;
    borderClass = paused ? 'border-amber-500/30 from-amber-950/15' : 'border-emerald-500/30 from-emerald-950/20';
    badgeClass = paused ? 'text-amber-300' : 'text-emerald-300';
    cells = [
      { label: 'Derivatives free', value: formatUsd(freeMargin, 2), hint: 'USDT available for copy' },
      { label: 'Account equity', value: formatUsd(equity, 2), hint: 'Live exchange account' },
      {
        label: 'Unrealized P&L',
        value: `${unrealized >= 0 ? '+' : ''}${formatUsd(unrealized, 2)}`,
        accent: pnlColor(unrealized),
        hint: userAgent.openPositionSide ? `${userAgent.openPositionSide} open` : 'Flat',
      },
      {
        label: 'Session P&L',
        value: `${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`,
        accent: pnlColor(sessionPnl),
        hint: formatPercent(userAgent.netReturnPct ?? 0),
      },
    ];
  } else {
    title = `Connect ${exchange} to copy`;
    borderClass = 'border-zinc-800 from-zinc-950/40';
    badgeClass = 'text-emerald-300';
    cells = [
      { label: 'Showcase equity', value: formatUsd(showcaseAgent.equityUsd ?? 500, 0), hint: 'Research bot reference' },
      { label: 'Showcase P&L', value: formatUsd(showcaseAgent.sessionPnlUsd ?? 0, 2), hint: 'Admin session' },
      { label: 'Your copy', value: '—', hint: 'Connect API to start' },
      { label: 'Return', value: '—', hint: 'Live relay after hire' },
    ];
  }

  return (
    <section
      className={`rounded-xl border bg-gradient-to-br to-zinc-950/60 px-4 py-3 ${borderClass}`}
    >
      <p className={`text-[10px] font-bold uppercase tracking-[0.15em] ${badgeClass}`}>{title}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cells.map((cell) => (
          <div key={cell.label}>
            <p className="text-[10px] uppercase tracking-widest text-zinc-500">{cell.label}</p>
            <p className={`mt-0.5 text-lg font-bold ${cell.accent ?? 'text-white'}`}>{cell.value}</p>
            {cell.hint ? <p className="mt-0.5 text-[10px] text-zinc-600">{cell.hint}</p> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
