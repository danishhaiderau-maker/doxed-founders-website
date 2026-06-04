'use client';

import Link from 'next/link';
import { formatDdollar, formatPercent } from '@dcf/utils';
import { FollowTraderButton } from '@/components/follow-trader-button';
import { TraderRankShareButton } from '@/components/trader-rank-share-button';
import { TwitterIdentityLink } from '@/components/account/twitter-identity-link';
import { CopyTraderButton } from '@/components/copy-trader-button';
import { MessageTraderButton } from '@/components/message-trader-button';
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
  const topPosition = portfolio.positions[0];

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-muted)]">
            Trader profile
          </p>
          <h2 className="mt-1 text-2xl font-bold">{portfolio.displayName}</h2>
          {portfolio.messagingAddress && (
            <p className="mt-1 font-mono text-xs text-zinc-500">{portfolio.messagingAddress}</p>
          )}
          <div className="mt-2">
            <TwitterIdentityLink handle={portfolio.twitterHandle} url={portfolio.twitterUrl} />
          </div>
          {portfolio.platformHandle && portfolio.twitterHandle && (
            <p className="mt-1 text-xs text-zinc-500">
              Platform handle: <span className="text-zinc-400">{portfolio.platformHandle}</span>
            </p>
          )}
          <p className="mt-2 text-sm text-[var(--color-muted)]">
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
          {!isSelf && (
            <>
              <Link
                href="/leaderboard"
                className="rounded-lg border border-zinc-600 px-3 py-1.5 text-sm text-zinc-300 hover:text-white"
              >
                Rankings
              </Link>
              {accessToken && (
                <>
                  <FollowTraderButton
                    userId={portfolio.userId}
                    token={accessToken}
                    initiallyFollowing={following}
                    size="md"
                    onChange={onFollowChange}
                  />
                  <MessageTraderButton userId={portfolio.userId} />
                </>
              )}
              <CopyTraderButton
                userId={portfolio.userId}
                dexscreenerUrl={topPosition?.dexscreenerUrl}
                amountUsd={topPosition?.marketValue}
                thesis={topPosition?.convictionThesis}
              />
            </>
          )}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Return"
          value={formatPercent(portfolio.roi)}
          accent={portfolio.roi >= 0 ? 'green' : 'red'}
        />
        <Stat label="Paper Ddollar" value={formatDdollar(portfolio.totalValue, 0)} />
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
