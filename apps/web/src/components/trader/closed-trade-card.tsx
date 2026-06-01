'use client';

import { formatPercent, formatTokenPrice, formatUsd } from '@dcf/utils';
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
          <p className="text-xs text-[var(--color-muted)]">Profit</p>
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
        {trade.peakPriceUsd != null && trade.peakPriceUsd > trade.exitPriceUsd && (
          <div>
            <dt className="text-[var(--color-muted)]">Peak after entry</dt>
            <dd className="mt-0.5 font-medium text-amber-300">
              {formatTokenPrice(trade.peakPriceUsd)}
            </dd>
          </div>
        )}
      </dl>

      {(trade.whatIfHeldPct > 0 || trade.missedAlphaPct > 0) && (
        <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-950/20 px-3 py-2.5">
          <div className="flex flex-wrap gap-4 text-sm">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-400/80">
                What If I Held?
              </p>
              <p className="mt-0.5 font-bold text-amber-200">
                +{trade.whatIfHeldPct.toFixed(0)}%
              </p>
            </div>
            {trade.missedAlphaPct > 0 && (
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-400/80">
                  Missed alpha
                </p>
                <p className="mt-0.5 font-bold text-amber-300">
                  +{trade.missedAlphaPct.toFixed(0)}%
                </p>
              </div>
            )}
          </div>
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
        whatIfHeldReturnPct={trade.whatIfHeldPct}
        missedAlphaPct={trade.missedAlphaPct}
        portfolioUrl={portfolioUrl}
      />
    </li>
  );
}
