'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { formatUsd, formatPercent } from '@dcf/utils';
import { fetchLeaderboard, LeaderboardEntry } from '@/lib/api';

export default function LeaderboardPage() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLeaderboard()
      .then(setEntries)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main className="min-h-screen px-6 py-12">
      <div className="mx-auto max-w-3xl">
        <Link href="/paper-trading" className="text-sm text-[var(--color-muted)] hover:text-white">
          ← Paper Trading
        </Link>
        <h1 className="mt-6 text-3xl font-bold">Paper Trading Leaderboard</h1>
        <p className="mt-2 text-[var(--color-muted)]">
          Ranked by total portfolio value. Everyone starts with $10,000 virtual cash.
        </p>

        {error && <p className="mt-4 text-sm text-[var(--color-danger)]">{error}</p>}

        <div className="mt-8 overflow-hidden rounded-xl border border-[var(--color-border)]">
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-card)] text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3 font-medium">Rank</th>
                <th className="px-4 py-3 font-medium">Trader</th>
                <th className="px-4 py-3 font-medium">Portfolio</th>
                <th className="px-4 py-3 font-medium">P&amp;L</th>
                <th className="px-4 py-3 font-medium">ROI</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-[var(--color-muted)]">
                    No traders yet. Be the first on{' '}
                    <Link href="/paper-trading" className="text-[var(--color-accent)]">
                      Paper Trading
                    </Link>
                    .
                  </td>
                </tr>
              )}
              {entries.map((entry) => (
                <tr
                  key={entry.userId}
                  className="border-t border-[var(--color-border)] bg-[var(--color-background)]"
                >
                  <td className="px-4 py-3 font-semibold">#{entry.rank}</td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/portfolio/${entry.userId}`}
                      className="hover:text-[var(--color-accent)]"
                    >
                      {entry.displayName}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{formatUsd(entry.totalValue)}</td>
                  <td
                    className={`px-4 py-3 ${
                      entry.pnl >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
                    }`}
                  >
                    {formatUsd(entry.pnl)}
                  </td>
                  <td
                    className={`px-4 py-3 ${
                      entry.roi >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'
                    }`}
                  >
                    {formatPercent(entry.roi)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
