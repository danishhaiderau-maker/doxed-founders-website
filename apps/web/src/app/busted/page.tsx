'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { formatPercent, formatUsd } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { fetchBustedTraders, BustedTraderEntry } from '@/lib/api';

export default function BustedTradersPage() {
  const [entries, setEntries] = useState<BustedTraderEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchBustedTraders()
      .then(setEntries)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/paper-trading" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Paper trading
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Poor judgment hall</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Traders who blew their $10,000 paper account. Sign in with X — your record is public.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="rounded-xl border border-red-500/25 bg-red-950/10 p-4 text-sm text-red-100/90">
          Talent should be rewarded — not deep pockets. Paper trading separates skill from wallet
          size. These accounts hit zero. Learn from their calls or join the leaderboard instead.
        </p>

        {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

        <div className="mt-8 overflow-hidden rounded-xl border border-[var(--color-border)]">
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-card)] text-xs uppercase tracking-wider text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3">#</th>
                <th className="px-4 py-3">Trader</th>
                <th className="px-4 py-3">X</th>
                <th className="px-4 py-3">Left</th>
                <th className="px-4 py-3">Loss</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 && !error && (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-[var(--color-muted)]">
                    No busted accounts yet. Someone will fumble the bag eventually.
                  </td>
                </tr>
              )}
              {entries.map((entry) => (
                <tr key={entry.userId} className="border-t border-[var(--color-border)]">
                  <td className="px-4 py-3 font-semibold">{entry.rank}</td>
                  <td className="px-4 py-3">
                    <Link href={`/portfolio/${entry.userId}`} className="hover:text-emerald-400">
                      {entry.displayName}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-[var(--color-muted)]">
                    {entry.twitterHandle ? (
                      <a
                        href={`https://x.com/${entry.twitterHandle}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sky-400 hover:underline"
                      >
                        @{entry.twitterHandle}
                      </a>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-4 py-3">{formatUsd(entry.totalValue, 0)}</td>
                  <td className="px-4 py-3 text-red-400">
                    {formatUsd(entry.pnl, 0)} ({formatPercent(entry.roi)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-8 flex flex-wrap gap-4">
          <Link
            href="/login?callbackUrl=/paper-trading"
            className="rounded-lg bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            Sign in with X — prove your skill
          </Link>
          <Link
            href="/leaderboard"
            className="rounded-lg border border-[var(--color-border)] px-5 py-2.5 text-sm hover:border-emerald-400"
          >
            Top traders →
          </Link>
        </div>
      </div>
    </main>
  );
}
