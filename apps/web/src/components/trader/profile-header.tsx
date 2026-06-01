'use client';

import { formatPercent, formatUsd } from '@dcf/utils';
import { FollowTraderButton } from '@/components/follow-trader-button';
import { TraderRankShareButton } from '@/components/trader-rank-share-button';
import type { PublicPortfolio } from '@/lib/api';

type Props = {
  portfolio: PublicPortfolio;
  isSelf: boolean;
  following: boolean;
  accessToken?: string;
  onFollowChange: (following: boolean) => void;
};

export function TraderProfileHeader({
  portfolio,
  isSelf,
  following,
  accessToken,
  onFollowChange,
}: Props) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-muted)]">
            Trader profile
          </p>
          <h2 className="mt-1 text-2xl font-bold">{portfolio.displayName}</h2>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Paper trading journey · decision record, not just returns
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TraderRankShareButton
            userId={portfolio.userId}
            displayName={portfolio.displayName}
            roi={portfolio.roi}
            totalValue={portfolio.totalValue}
            pnl={portfolio.pnl}
            isLoser={portfolio.pnl < 0}
            isBusted={portfolio.totalValue < 1000}
          />
          {!isSelf && accessToken && (
            <FollowTraderButton
              userId={portfolio.userId}
              token={accessToken}
              initiallyFollowing={following}
              size="md"
              onChange={onFollowChange}
            />
          )}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Return"
          value={formatPercent(portfolio.roi)}
          accent={portfolio.roi >= 0 ? 'green' : 'red'}
        />
        <Stat label="DDollar balance" value={formatUsd(portfolio.totalValue, 0)} />
        <Stat label="Followers" value={portfolio.followersCount.toLocaleString()} />
        <Stat label="Trust" value={`${portfolio.trustScore}`} />
        <Stat label="Conviction" value={`${portfolio.convictionScore}`} />
        <Stat label="Open positions" value={String(portfolio.positionCount)} />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'green' | 'red';
}) {
  const color =
    accent === 'green'
      ? 'text-[var(--color-success)]'
      : accent === 'red'
        ? 'text-[var(--color-danger)]'
        : 'text-white';

  return (
    <div className="rounded-lg bg-[var(--color-background)] px-3 py-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-muted)]">
        {label}
      </p>
      <p className={`mt-1 text-lg font-semibold ${color}`}>{value}</p>
    </div>
  );
}
