'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useEffect, useState } from 'react';
import type { NotificationBuyerMeta } from '@dcf/utils';
import { formatUsd } from '@dcf/utils';
import { fetchAccountFollowing } from '@/lib/api';
import { FollowTraderButton } from '@/components/follow-trader-button';

type Props = {
  ticker: string;
  slug: string;
  dexscreenerUrl?: string | null;
  buyers: NotificationBuyerMeta['buyers'];
};

export function ProjectRecentBuyersPanel({ ticker, slug, dexscreenerUrl, buyers }: Props) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [followingIds, setFollowingIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!token) return;
    fetchAccountFollowing(token)
      .then((rows) => setFollowingIds(new Set(rows.map((r) => r.userId))))
      .catch(() => setFollowingIds(new Set()));
  }, [token]);

  if (!buyers?.length) return null;

  const names = buyers.slice(0, 3).map((b) => b.displayName);
  const summary =
    buyers.length === 1
      ? names[0]
      : buyers.length === 2
        ? `${names[0]} and ${names[1]}`
        : `${names[0]}, ${names[1]}, and ${buyers.length - 2} more`;

  return (
    <div className="mt-3 space-y-3">
      <p className="rounded-lg border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-sm text-amber-100">
        <strong className="text-amber-50">{summary}</strong> paper-traded {ticker} on the platform.
        This is not a verified Doxxed listing —{' '}
        <Link href="/list-your-project" className="font-medium text-amber-200 underline">
          submit a listing
        </Link>{' '}
        with founder proof for admin approval.
      </p>

      <div className="rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-200/90">
            Who bought {ticker}
          </p>
          <div className="flex gap-2 text-xs">
            <Link href="/leaderboard" className="text-emerald-300 hover:underline">
              Top traders →
            </Link>
            <Link href="/leaderboard?tab=losers" className="text-red-300 hover:underline">
              Top losers →
            </Link>
          </div>
        </div>
        <ul className="mt-3 space-y-2">
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
                onChange={(f) => {
                  setFollowingIds((prev) => {
                    const next = new Set(prev);
                    if (f) next.add(buyer.userId);
                    else next.delete(buyer.userId);
                    return next;
                  });
                }}
              />
            </li>
          ))}
        </ul>
        <Link
          href={`/paper-trading?dex=${encodeURIComponent(dexscreenerUrl ?? slug)}`}
          className="mt-3 inline-block text-xs text-emerald-300 hover:underline"
        >
          Paper trade {ticker} →
        </Link>
      </div>
    </div>
  );
}
