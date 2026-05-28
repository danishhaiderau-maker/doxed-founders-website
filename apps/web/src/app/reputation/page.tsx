'use client';

import Link from 'next/link';
import {
  LEVEL_THRESHOLDS,
  POINT_ACTIONS,
  POINTS,
  contributorLevelLabel,
  computeVotingThreshold,
  VOTING_MIN_YES_PERCENT,
  VOTING_WINDOW_HOURS,
} from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { useEffect, useState } from 'react';
import { fetchVotingStats } from '@/lib/api';

export default function ReputationPage() {
  const [activeUsers, setActiveUsers] = useState(10);
  const exampleThreshold = computeVotingThreshold(activeUsers);

  useEffect(() => {
    fetchVotingStats()
      .then((s) => setActiveUsers(s.activeUsers))
      .catch(() => setActiveUsers(10));
  }, []);

  const sortedActions = [...POINT_ACTIONS].sort((a, b) => b.amount - a.amount);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Reputation & points</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Full math — no guesswork. Earn conviction before you earn capital.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-12 px-6 py-12">
        <section className="rounded-2xl border border-amber-500/30 bg-gradient-to-br from-amber-950/20 to-transparent p-6">
          <h2 className="text-lg font-semibold text-amber-200">Biggest reward: scout verified listings</h2>
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            The hardest bottleneck for this platform is verified, doxxed projects. If you scout a
            founder, submit proof, pass community vote, and admin approves — you earn{' '}
            <strong className="text-amber-300">{POINTS.LISTING_SCOUT_APPROVED.toLocaleString()} points</strong>.
            That is intentionally the highest action on the site.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold">Every action & point value</h2>
          <div className="mt-4 overflow-hidden rounded-xl border border-[var(--color-border)]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--color-card)] text-xs uppercase tracking-wider text-[var(--color-muted)]">
                <tr>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Points</th>
                  <th className="hidden px-4 py-3 sm:table-cell">Repeat?</th>
                </tr>
              </thead>
              <tbody>
                {sortedActions.map((action) => (
                  <tr key={action.key} className="border-t border-[var(--color-border)]">
                    <td className="px-4 py-3">
                      <div className="font-medium text-white">{action.label}</div>
                      <div className="mt-1 text-xs text-[var(--color-muted)]">{action.description}</div>
                    </td>
                    <td className="px-4 py-3 font-bold text-emerald-400">+{action.amount}</td>
                    <td className="hidden px-4 py-3 text-[var(--color-muted)] sm:table-cell">
                      {action.repeatable ? 'Yes' : 'Once'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <h2 className="text-lg font-semibold">Contributor levels</h2>
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            Your level updates automatically from total lifetime points on your public profile.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {LEVEL_THRESHOLDS.map((row) => (
              <div
                key={row.level}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]/50 px-4 py-3"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{contributorLevelLabel(row.level)}</span>
                  <span className="text-xs text-[var(--color-muted)]">Level {row.level}</span>
                </div>
                <div className="mt-1 text-sm text-emerald-400">
                  {row.minPoints === 0 ? '0+' : `${row.minPoints.toLocaleString()}+`} pts
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-emerald-500/25 bg-emerald-950/10 p-6">
          <h2 className="text-lg font-semibold text-emerald-200">Community vote math (scout listings)</h2>
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            After you submit a listing, traders vote for {VOTING_WINDOW_HOURS} hours. Admin can
            fast-track approve anytime; after 48h every listing is in the admin inbox.
          </p>
          <ul className="mt-4 space-y-2 text-sm text-white/90">
            <li>
              <span className="text-emerald-400">Required voters:</span>{' '}
              max(3, min(50, ceil(√activeUsers × 1.5)))
            </li>
            <li>
              <span className="text-emerald-400">Pass rule:</span> total votes ≥ required AND yes% ≥
              {VOTING_MIN_YES_PERCENT}%
            </li>
            <li>
              <span className="text-emerald-400">Right now ({activeUsers} users):</span>{' '}
              need {exampleThreshold.requiredVoters} votes at {exampleThreshold.minYesPercent}% yes
            </li>
          </ul>
          <Link
            href="/scout-votes"
            className="mt-6 inline-block rounded-lg bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            Vote on scout listings →
          </Link>
        </section>

        <section>
          <h2 className="text-lg font-semibold">Example: one successful scout</h2>
          <div className="mt-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]/40 p-5 text-sm">
            <ol className="list-decimal space-y-2 pl-5 text-[var(--color-muted)]">
              <li>Sign up → +{POINTS.REGISTER}</li>
              <li>Submit scout listing (signed in) → +{POINTS.LISTING_SUBMIT}</li>
              <li>Community votes (you cannot vote on your own)</li>
              <li>Admin approves → +{POINTS.LISTING_SCOUT_APPROVED}</li>
            </ol>
            <p className="mt-4 font-semibold text-amber-300">
              Total from one approved scout:{' '}
              {POINTS.REGISTER + POINTS.LISTING_SUBMIT + POINTS.LISTING_SCOUT_APPROVED} points
              (plus any votes/comments/trades you do along the way)
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
