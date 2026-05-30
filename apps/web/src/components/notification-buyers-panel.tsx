'use client';

import Link from 'next/link';
import type { NotificationBuyerMeta } from '@dcf/utils';
import { formatUsd } from '@dcf/utils';
import { FollowTraderButton } from '@/components/follow-trader-button';

type Props = {
  metadata: NotificationBuyerMeta | null | undefined;
  token?: string | null;
  followingIds: Set<string>;
  onFollowChange: (userId: string, following: boolean) => void;
};

export function NotificationBuyersPanel({
  metadata,
  token,
  followingIds,
  onFollowChange,
}: Props) {
  const buyers = metadata?.buyers ?? [];
  if (buyers.length === 0) return null;

  return (
    <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-950/15 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-amber-200/90">
        Who bought {metadata?.projectTicker ?? ''}
      </p>
      <ul className="mt-2 space-y-2">
        {buyers.map((buyer) => (
          <li
            key={buyer.userId}
            className="flex flex-wrap items-center justify-between gap-2 text-sm"
          >
            <div className="min-w-0">
              <Link
                href={`/portfolio/${buyer.userId}`}
                className="font-medium text-white hover:text-emerald-400"
              >
                {buyer.displayName}
              </Link>
              <span className="ml-2 text-xs text-[var(--color-muted)]">
                {formatUsd(buyer.amountUsd, 0)}
              </span>
              {buyer.twitterHandle && (
                <a
                  href={`https://x.com/${buyer.twitterHandle}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-2 text-xs text-sky-400 hover:underline"
                >
                  @{buyer.twitterHandle}
                </a>
              )}
            </div>
            <FollowTraderButton
              userId={buyer.userId}
              token={token}
              initiallyFollowing={followingIds.has(buyer.userId)}
              onChange={(f) => onFollowChange(buyer.userId, f)}
            />
          </li>
        ))}
      </ul>
      {metadata?.projectSlug && (
        <Link
          href={`/project/${metadata.projectSlug}`}
          className="mt-2 inline-block text-xs text-amber-300 hover:underline"
        >
          View project →
        </Link>
      )}
    </div>
  );
}
