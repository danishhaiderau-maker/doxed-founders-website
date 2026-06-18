'use client';

import { formatUsd } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import { BitfinexDerivativesFundingGuide } from '@/components/agent-hub/bitfinex-derivatives-funding-guide';

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-300';
}

/** Live Bitfinex copy — real equity, fees, and wallet placement from API. */
export function AgentLiveExchangeEquity({
  agent,
  exchangeLabel = 'Bitfinex',
  compact = false,
}: {
  agent: TradingAgentSummary;
  exchangeLabel?: string | null;
  compact?: boolean;
}) {
  const freeMargin = agent.exchangeBalanceUsd ?? 0;
  const collateral = agent.balanceUsd ?? freeMargin;
  const equity = agent.equityUsd ?? collateral;
  const unrealized = agent.unrealizedPnlUsd ?? 0;
  const sessionPnl = agent.sessionPnlUsd ?? 0;
  const tradingFees = agent.tradingFeesUsd ?? 0;
  const fundingFees = agent.fundingFeesUsd ?? 0;

  const cells = [
    {
      label: 'Account equity',
      value: formatUsd(equity, 2),
      hint: agent.openPositionSide
        ? `${agent.openPositionSide} open on ${exchangeLabel}`
        : 'Derivatives wallet + mark-to-market',
      accent: 'text-white',
    },
    {
      label: 'Free margin',
      value: formatUsd(freeMargin, 2),
      hint: `Available in Derivatives (need ~$20 per copy trade)`,
    },
    {
      label: 'Unrealized P&L',
      value: `${unrealized >= 0 ? '+' : ''}${formatUsd(unrealized, 2)}`,
      accent: pnlColor(unrealized),
      hint: 'Open position on exchange',
    },
    {
      label: 'Session P&L',
      value: `${sessionPnl >= 0 ? '+' : ''}${formatUsd(sessionPnl, 2)}`,
      accent: pnlColor(sessionPnl),
      hint: 'After trading + funding fees this session',
    },
    {
      label: 'Trading fees',
      value: formatUsd(tradingFees, 2),
      hint: 'Bitfinex trading fees (session)',
    },
    {
      label: 'Funding fees',
      value: formatUsd(fundingFees, 2),
      hint: 'Perp funding paid/received (session)',
    },
  ];

  return (
    <section
      className={`rounded-2xl border border-zinc-800/80 bg-gradient-to-br from-zinc-900/80 via-zinc-950/60 to-emerald-950/15 ${
        compact ? 'px-4 py-3' : 'px-5 py-4'
      }`}
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-emerald-300/80">
        Your {exchangeLabel} live copy
      </p>
      {agent.walletStatusHint && (
        <p
          className={`mt-2 rounded-lg border px-3 py-2 text-xs ${
            agent.fundsInWrongWallet
              ? 'border-amber-500/40 bg-amber-950/30 text-amber-100'
              : 'border-emerald-500/30 bg-emerald-950/20 text-emerald-100'
          }`}
        >
          {agent.walletStatusHint}
        </p>
      )}
      <div
        className={`mt-3 grid gap-3 ${
          compact ? 'sm:grid-cols-2 lg:grid-cols-3' : 'sm:grid-cols-2 lg:grid-cols-3'
        }`}
      >
        {cells.map((cell) => (
          <div key={cell.label}>
            <p className="text-[10px] uppercase tracking-widest text-zinc-500">{cell.label}</p>
            <p className={`mt-1 text-lg font-bold ${cell.accent ?? 'text-white'}`}>{cell.value}</p>
            {cell.hint && <p className="mt-0.5 text-[10px] text-zinc-600">{cell.hint}</p>}
          </div>
        ))}
      </div>
      {(agent.fundsInWrongWallet || compact) && (
        <div className="mt-3">
          <BitfinexDerivativesFundingGuide
            derivativesUsd={freeMargin}
            exchangeUsd={agent.exchangeUsd}
            fundingUsd={agent.fundingUsd}
            compact
          />
        </div>
      )}
    </section>
  );
}
