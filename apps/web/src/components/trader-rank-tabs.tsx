'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { formatPercent, formatUsd } from '@dcf/utils';
import {
  BustedTraderEntry,
  fetchAccountFollowing,
  fetchBustedTraders,
  fetchLeaderboard,
  fetchMissedAlphaLeaderboard,
  LeaderboardEntry,
  MissedAlphaLeaderboardEntry,
} from '@/lib/api';
import { FollowTraderButton } from '@/components/follow-trader-button';
import { TraderRankShareButton } from '@/components/trader-rank-share-button';

export type TraderRankTab = 'winners' | 'losers' | 'missed-alpha';

type Props = {
  initialTab?: TraderRankTab;
  compact?: boolean;
};

export function TraderRankTabs({ initialTab = 'winners', compact = false }: Props) {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [tab, setTab] = useState<TraderRankTab>(initialTab);
  const [winners, setWinners] = useState<LeaderboardEntry[]>([]);
  const [losers, setLosers] = useState<BustedTraderEntry[]>([]);
  const [missedAlpha, setMissedAlpha] = useState<MissedAlphaLeaderboardEntry[]>([]);
  const [followingIds, setFollowingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchLeaderboard(), fetchBustedTraders(), fetchMissedAlphaLeaderboard()])
      .then(([w, l, m]) => {
        setWinners(w);
        setLosers(l);
        setMissedAlpha(m);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!token) return;
    fetchAccountFollowing(token)
      .then((rows) => setFollowingIds(new Set(rows.map((r) => r.userId))))
      .catch(() => setFollowingIds(new Set()));
  }, [token]);

  return (
    <div className={compact ? 'space-y-3' : 'space-y-6'}>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setTab('winners')}
          className={`rounded-full px-4 py-2 text-sm font-medium ${
            tab === 'winners'
              ? 'bg-emerald-600 text-white'
              : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-white'
          }`}
        >
          Top traders
        </button>
        <button
          type="button"
          onClick={() => setTab('losers')}
          className={`rounded-full px-4 py-2 text-sm font-medium ${
            tab === 'losers'
              ? 'bg-red-600 text-white'
              : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-white'
          }`}
        >
          Top losers
        </button>
        <button
          type="button"
          onClick={() => setTab('missed-alpha')}
          className={`rounded-full px-4 py-2 text-sm font-medium ${
            tab === 'missed-alpha'
              ? 'bg-amber-600 text-white'
              : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-white'
          }`}
        >
          Alpha left on table
        </button>
      </div>

      {error && <p className="text-sm text-[var(--color-danger)]">{error}</p>}

      <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
        {tab === 'winners' ? (
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-card)] text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3 font-medium">#</th>
                <th className="px-4 py-3 font-medium">Trader</th>
                <th className="px-4 py-3 font-medium">Portfolio</th>
                <th className="px-4 py-3 font-medium">ROI</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {winners.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-[var(--color-muted)]">
                    No ranked traders yet.
                  </td>
                </tr>
              )}
              {winners.map((entry) => (
                <tr key={entry.userId} className="border-t border-[var(--color-border)]">
                  <td className="px-4 py-3 font-semibold">#{entry.rank}</td>
                  <td className="px-4 py-3">
                    <Link href={`/portfolio/${entry.userId}`} className="hover:text-[var(--color-accent)]">
                      {entry.displayName}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{formatUsd(entry.totalValue)}</td>
                  <td className={`px-4 py-3 ${entry.roi >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatPercent(entry.roi)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <TraderRankShareButton
                        compact
                        userId={entry.userId}
                        displayName={entry.displayName}
                        roi={entry.roi}
                        totalValue={entry.totalValue}
                        pnl={entry.pnl}
                        rank={entry.rank}
                      />
                      <FollowTraderButton
                        userId={entry.userId}
                        token={token}
                        initiallyFollowing={followingIds.has(entry.userId)}
                        onChange={(f) => {
                          setFollowingIds((prev) => {
                            const next = new Set(prev);
                            if (f) next.add(entry.userId);
                            else next.delete(entry.userId);
                            return next;
                          });
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : tab === 'losers' ? (
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-card)] text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3 font-medium">#</th>
                <th className="px-4 py-3 font-medium">Trader</th>
                <th className="px-4 py-3 font-medium">Left</th>
                <th className="px-4 py-3 font-medium">Loss</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {losers.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-[var(--color-muted)]">
                    No losing traders yet — everyone is flat or up.
                  </td>
                </tr>
              )}
              {losers.map((entry) => (
                <tr key={entry.userId} className="border-t border-[var(--color-border)]">
                  <td className="px-4 py-3 font-semibold">{entry.rank}</td>
                  <td className="px-4 py-3">
                    <Link href={`/portfolio/${entry.userId}`} className="hover:text-emerald-400">
                      {entry.displayName}
                    </Link>
                    {entry.isBusted && (
                      <span className="ml-2 rounded bg-red-950/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-red-300">
                        Busted
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">{formatUsd(entry.totalValue, 0)}</td>
                  <td className="px-4 py-3 text-red-400">
                    {formatUsd(entry.pnl, 0)} ({formatPercent(entry.roi)})
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <TraderRankShareButton
                        compact
                        userId={entry.userId}
                        displayName={entry.displayName}
                        roi={entry.roi}
                        totalValue={entry.totalValue}
                        pnl={entry.pnl}
                        rank={entry.rank}
                        isLoser
                        isBusted={entry.isBusted}
                      />
                      <FollowTraderButton
                        userId={entry.userId}
                        token={token}
                        initiallyFollowing={followingIds.has(entry.userId)}
                        onChange={(f) => {
                          setFollowingIds((prev) => {
                            const next = new Set(prev);
                            if (f) next.add(entry.userId);
                            else next.delete(entry.userId);
                            return next;
                          });
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-card)] text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3 font-medium">#</th>
                <th className="px-4 py-3 font-medium">Trader</th>
                <th className="px-4 py-3 font-medium">Token</th>
                <th className="px-4 py-3 font-medium">What If I Held?</th>
                <th className="px-4 py-3 font-medium">Missed</th>
              </tr>
            </thead>
            <tbody>
              {missedAlpha.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-[var(--color-muted)]">
                    No early exits recorded yet — close a trade to populate this board.
                  </td>
                </tr>
              )}
              {missedAlpha.map((entry) => (
                <tr key={`${entry.userId}-${entry.ticker}-${entry.closedAt}`} className="border-t border-[var(--color-border)]">
                  <td className="px-4 py-3 font-semibold">#{entry.rank}</td>
                  <td className="px-4 py-3">
                    <Link href={`/portfolio/${entry.userId}`} className="hover:text-amber-300">
                      {entry.displayName}
                    </Link>
                  </td>
                  <td className="px-4 py-3">${entry.ticker}</td>
                  <td className="px-4 py-3 text-amber-300">+{entry.whatIfHeldPct.toFixed(0)}%</td>
                  <td className="px-4 py-3 text-amber-400">+{entry.missedAlphaPct.toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
