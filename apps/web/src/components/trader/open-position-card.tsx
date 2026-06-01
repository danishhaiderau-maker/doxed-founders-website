'use client';

import { formatPercent, formatTokenPrice, formatUsd } from '@dcf/utils';
import { SharePosition } from '@/components/share-portfolio';
import type { PublicPortfolio } from '@/lib/api';

type Position = PublicPortfolio['positions'][number];

type Props = {
  pos: Position;
  portfolio: PublicPortfolio;
  accessToken?: string;
  onOpenIntel: (pos: Position) => void;
};

export function TraderOpenPositionCard({ pos, portfolio, accessToken, onOpenIntel }: Props) {
  const thesis = pos.convictionThesis || pos.convictionCatalyst;

  return (
    <li className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] p-4 text-sm">
      <div
        className="flex cursor-pointer items-start justify-between gap-3"
        onClick={() => onOpenIntel(pos)}
        onKeyDown={(e) => e.key === 'Enter' && onOpenIntel(pos)}
        role="button"
        tabIndex={0}
      >
        <div className="flex items-start gap-3">
          {pos.logoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={pos.logoUrl} alt="" className="h-10 w-10 rounded-full" />
          ) : (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-border)] text-xs font-bold">
              {pos.ticker.slice(0, 2)}
            </div>
          )}
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-base font-semibold">{pos.ticker}</p>
              <span className="rounded-full bg-emerald-950/50 px-2 py-0.5 text-[10px] font-semibold uppercase text-emerald-300">
                Open
              </span>
              {pos.convictionLevel && (
                <span className="rounded-full border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-400">
                  Conviction: {pos.convictionLevel}
                </span>
              )}
            </div>
            <p className="text-xs text-[var(--color-muted)]">{pos.name}</p>
          </div>
        </div>
        <div className="text-right">
          <p
            className={`font-semibold ${
              pos.pnl >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
            }`}
          >
            {formatPercent(pos.pnlPercent)}
          </p>
          <p className="text-xs text-[var(--color-muted)]">{formatUsd(pos.marketValue)}</p>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-[var(--color-border)] pt-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-[var(--color-muted)]">Entry</dt>
          <dd className="mt-0.5 font-medium">
            {pos.avgBuyPrice != null ? formatTokenPrice(pos.avgBuyPrice) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Current</dt>
          <dd className="mt-0.5 font-medium">
            {pos.priceUsd != null ? formatTokenPrice(pos.priceUsd) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">PnL</dt>
          <dd
            className={`mt-0.5 font-medium ${
              pos.pnl >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
            }`}
          >
            {formatUsd(pos.pnl)} ({formatPercent(pos.pnlPercent)})
          </dd>
        </div>
        <div>
          <dt className="text-[var(--color-muted)]">Days held</dt>
          <dd className="mt-0.5 font-medium">{pos.daysHeld ?? 0}</dd>
        </div>
      </dl>

      {thesis && (
        <div className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-950/15 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400/80">
            Thesis
          </p>
          <p className="mt-1 text-xs leading-relaxed text-emerald-100/90">
            {pos.convictionThesis || pos.convictionCatalyst}
          </p>
          {pos.convictionThesis && pos.convictionCatalyst && (
            <p className="mt-1 text-[11px] text-emerald-200/60">
              Catalyst: {pos.convictionCatalyst}
            </p>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
        <SharePosition
          userId={portfolio.userId}
          projectId={pos.projectId ?? ''}
          accessToken={accessToken}
          displayName={portfolio.displayName}
          ticker={pos.ticker}
          projectName={pos.name}
          investedUsd={(pos.quantity ?? 0) * (pos.avgBuyPrice ?? 0)}
          pnlUsd={pos.pnl}
          pnlPercent={pos.pnlPercent}
          entryPrice={pos.avgBuyPrice}
          currentPrice={pos.priceUsd}
          thesis={pos.convictionThesis}
          catalyst={pos.convictionCatalyst}
          targetPrice={pos.convictionTargetUsd}
          timeHorizon={pos.convictionTimeHorizon}
          recordedAt={pos.convictionRecordedAt}
          positionOpenedAt={pos.positionOpenedAt}
          portfolioRoi={portfolio.roi}
        />
      </div>
    </li>
  );
}
