'use client';

import { formatPercent, formatUsd } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-300';
}

/** Paper runway vs live equity — avoids "$500 volume" looking like nothing is happening. */
export function AgentShowcaseEquity({
  agent,
  title = 'Showcase paper desk',
  compact = false,
  mode = 'showcase',
  exchangeLabel,
}: {
  agent: TradingAgentSummary;
  title?: string;
  compact?: boolean;
  mode?: 'showcase' | 'copy' | 'live';
  exchangeLabel?: string | null;
}) {
  const runway = agent.startingBalance || 500;
  const equity = agent.equityUsd ?? runway;
  const cash = agent.balanceUsd ?? equity;
  const dailyPnl = agent.dailyPnlUsd ?? agent.sessionPnlUsd ?? equity - runway;
  const sessionPnl = agent.sessionPnlUsd ?? equity - runway;
  const unrealized = agent.unrealizedPnlUsd ?? Math.max(0, equity - cash);

  const cells =
    mode === 'live'
      ? [
          {
            label: `${exchangeLabel ?? 'Exchange'} balance`,
            value: formatUsd(cash, 2),
            hint: 'Available margin from your connected account',
          },
          {
            label: 'Current equity',
            value: formatUsd(equity, 2),
            hint: 'Live account — not paper DDollar',
          },
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
            hint: formatPercent(agent.netReturnPct),
          },
        ]
      : [
          {
            label: 'Paper runway',
            value: formatUsd(runway, 0),
            hint: 'Starting allocation this session',
          },
          {
            label: 'Current equity',
            value: formatUsd(equity, 0),
            hint:
              unrealized > 0.01
                ? `${formatUsd(cash, 0)} cash + ${formatUsd(unrealized, 0)} open`
                : 'Cash + mark-to-market',
          },
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
            hint: formatPercent(agent.netReturnPct),
          },
        ];

  return (
    <section
      className={`rounded-2xl border border-zinc-800/80 bg-gradient-to-br from-zinc-900/80 via-zinc-950/60 to-emerald-950/15 ${
        compact ? 'px-4 py-3' : 'px-5 py-4'
      }`}
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-emerald-300/80">{title}</p>
      <div className={`mt-3 grid gap-3 ${compact ? 'sm:grid-cols-2 lg:grid-cols-4' : 'sm:grid-cols-2 lg:grid-cols-4'}`}>
        {cells.map((cell) => (
          <div key={cell.label}>
            <p className="text-[10px] uppercase tracking-widest text-zinc-500">{cell.label}</p>
            <p className={`mt-1 text-xl font-bold ${cell.accent ?? 'text-white'}`}>{cell.value}</p>
            {cell.hint && <p className="mt-0.5 text-[10px] text-zinc-600">{cell.hint}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}
