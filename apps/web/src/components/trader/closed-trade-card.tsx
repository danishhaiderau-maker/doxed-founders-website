'use client';

import { formatPercent, formatTokenPrice, formatUsd, postExitOutcomeLabel } from '@dcf/utils';
import { TradeCloseShareButtons } from '@/components/trade-close-share-buttons';
import { useShareOrigin } from '@/components/share-on-x-button';
import { buildPortfolioShareUrl } from '@dcf/utils';
import type { ClosedTradeCard as ClosedTrade } from '@/lib/api';

type Props = {
  trade: ClosedTrade;
  userId: string;
};

export function TraderClosedTradeCard({ trade, userId }: Props) {
  const origin = useShareOrigin();
  const portfolioUrl = buildPortfolioShareUrl(origin, userId);
  const closedDate = new Date(trade.closedAt).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  const outcome = postExitOutcomeLabel({
    narrative: trade.exitNarrative,
    missedAfterExitPct: trade.missedAfterExitPct,
    avoidedLossPct: trade.avoidedLossPct ?? 0,
    currentVsExitPct: trade.currentVsExitPct ?? 0,
  });

  return (
    <li className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {trade.logoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={trade.logoUrl} alt="" className="h-9 w-9 rounded-full" />
          ) : (
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--color-border)] text-xs font-bold">
              {trade.ticker.slice(0, 2)}
            </div>
          )}
          <div>
            <p className="font-semibold">{trade.ticker}</p>
            <p className="text-xs text-[var(--color-muted)]">Closed {closedDate}</p>
          </div>
        </div>
        <div className="text-right">
          <p
            className={`text-lg font-bold ${
              trade.realizedReturnPct >= 0
                ? 'text-[var(--color-success)]'
                : 'text-[var(--color-danger)]'
            }`}
          >
            {formatPercent(trade.realizedReturnPct)}
          </p>
          <p className="text-xs text-[var(--color-muted)]">{outcome.emoji} {outcome.title}</p>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-[var(--color-muted)]">Entry</dt>
          <dd className="mt-0.5 font-medium">{formatTokenPrice(trade.entryPriceUsd)}</dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Exit</dt>
          <dd className="mt-0.5 font-medium">{formatTokenPrice(trade.exitPriceUsd)}</dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Net</dt>
          <dd className="mt-0.5 font-medium text-emerald-300">
            {formatUsd(trade.proceedsUsd - trade.investedUsd)}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Since exit</dt>
          <dd className="mt-0.5 font-medium text-amber-300">{outcome.detail}</dd>
        </div>
      </dl>

      {(trade.missedAfterExitPct > 0 || trade.avoidedLossPct > 0) && (
        <div
          className={`mt-4 rounded-lg border px-3 py-2.5 text-sm ${
            trade.exitNarrative === 'smart'
              ? 'border-sky-500/25 bg-sky-950/20'
              : trade.exitNarrative === 'regret'
                ? 'border-amber-500/25 bg-amber-950/20'
                : 'border-zinc-600/40 bg-zinc-900/40'
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
            {outcome.emoji} {outcome.title}
          </p>
          <p className="mt-1 font-bold text-white">{outcome.detail}</p>
          {trade.pumpAfterExitPct > 0 && trade.exitNarrative === 'regret' && (
            <p className="mt-1 text-xs text-amber-200/80">
              Peak since exit: +{trade.pumpAfterExitPct.toFixed(0)}% to{' '}
              {formatTokenPrice(trade.postExitPeakPriceUsd)}
            </p>
          )}
        </div>
      )}

      {trade.thesis && (
        <p className="mt-3 text-xs leading-relaxed text-[var(--color-muted)]">
          <span className="font-medium text-zinc-400">Thesis: </span>
          &ldquo;{trade.thesis}&rdquo;
        </p>
      )}

      <TradeCloseShareButtons
        ticker={trade.ticker}
        investedUsd={trade.investedUsd}
        proceedsUsd={trade.proceedsUsd}
        realizedReturnPct={trade.realizedReturnPct}
        whatIfHeldReturnPct={trade.pumpAfterExitPct}
        missedAlphaPct={trade.missedAfterExitPct}
        portfolioUrl={portfolioUrl}
        postExitPeakPriceUsd={trade.postExitPeakPriceUsd}
        exitPriceUsd={trade.exitPriceUsd}
        postExitTroughPriceUsd={trade.postExitTroughPriceUsd}
        dropAfterExitPct={trade.dropAfterExitPct}
        pumpAfterExitPct={trade.pumpAfterExitPct}
        currentVsExitPct={trade.currentVsExitPct}
        avoidedLossPct={trade.avoidedLossPct}
        exitNarrative={trade.exitNarrative}
      />
    </li>
  );
}
