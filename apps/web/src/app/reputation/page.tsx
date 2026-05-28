'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import {
  AIRDROP_POOL_USD,
  AIRDROP_SUPPLY_PERCENT,
  AIRDROP_TOKEN_POOL,
  LEVEL_THRESHOLDS,
  LAUNCH_FDV_USD,
  POINT_ACTIONS,
  POINTS,
  TOKEN_SUPPLY,
  contributorLevelLabel,
  computeVotingThreshold,
  formatTokenAmount,
  formatUsd,
  pointsToNextLevel,
  VOTING_MIN_YES_PERCENT,
  VOTING_WINDOW_HOURS,
} from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { ReputationBadge } from '@/components/landing/project-spotlight';
import { useEffect, useState } from 'react';
import {
  fetchReputationLeaderboard,
  fetchReputationMe,
  fetchVotingStats,
  ReputationLeaderboardEntry,
  ReputationMe,
} from '@/lib/api';

export default function ReputationPage() {
  const { data: session } = useSession();
  const [activeUsers, setActiveUsers] = useState(10);
  const [leaderboard, setLeaderboard] = useState<ReputationLeaderboardEntry[]>([]);
  const [totalParticipants, setTotalParticipants] = useState(0);
  const [me, setMe] = useState<ReputationMe | null>(null);
  const [error, setError] = useState<string | null>(null);

  const exampleThreshold = computeVotingThreshold(activeUsers);
  const sortedActions = [...POINT_ACTIONS].sort((a, b) => b.amount - a.amount);
  const progress = me ? pointsToNextLevel(me.reputationPoints) : null;

  useEffect(() => {
    fetchVotingStats()
      .then((s) => setActiveUsers(s.activeUsers))
      .catch(() => setActiveUsers(10));
  }, []);

  useEffect(() => {
    fetchReputationLeaderboard(50)
      .then((data) => {
        setLeaderboard(data.entries);
        setTotalParticipants(data.totalParticipants);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!session?.accessToken) {
      setMe(null);
      return;
    }
    fetchReputationMe(session.accessToken)
      .then(setMe)
      .catch(() => setMe(null));
  }, [session?.accessToken]);

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Reputation & points</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Earn points, climb the board, and see your projected token airdrop share.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl space-y-12 px-6 py-12">
        <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/30 to-transparent p-6">
          <h2 className="text-lg font-semibold text-violet-200">Token airdrop preview</h2>
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            At launch we plan a{' '}
            <strong className="text-white">{AIRDROP_SUPPLY_PERCENT}% community airdrop</strong> on a{' '}
            <strong className="text-white">{formatUsd(LAUNCH_FDV_USD, 0)} FDV</strong> (
            {TOKEN_SUPPLY.toLocaleString()} tokens). That is{' '}
            <strong className="text-violet-200">
              {formatTokenAmount(AIRDROP_TOKEN_POOL)} tokens (~{formatUsd(AIRDROP_POOL_USD, 0)})
            </strong>{' '}
            split proportionally by lifetime reputation points. Numbers below are estimates — not
            guaranteed allocations.
          </p>
        </section>

        {session && me && (
          <section className="rounded-2xl border border-amber-500/40 bg-amber-950/20 p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-widest text-amber-400/80">Your standing</p>
                <h2 className="mt-1 text-xl font-bold">{me.displayName}</h2>
                <div className="mt-3">
                  <ReputationBadge points={me.reputationPoints} level={me.contributorLevel} />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Rank" value={me.rank ? `#${me.rank}` : '—'} />
                <Stat label="Points" value={me.reputationPoints.toLocaleString()} />
                <Stat
                  label="Est. airdrop"
                  value={formatTokenAmount(me.estimatedTokens)}
                  hint={`${me.supplyPercent.toFixed(4)}% of supply`}
                />
                <Stat
                  label="Est. value @ launch"
                  value={formatUsd(me.estimatedUsd, 0)}
                  hint={`${me.airdropPoolPercent.toFixed(2)}% of pool`}
                />
              </div>
            </div>
            {progress?.nextLevel && (
              <p className="mt-4 text-sm text-[var(--color-muted)]">
                {progress.pointsNeeded.toLocaleString()} pts to{' '}
                {contributorLevelLabel(progress.nextLevel)} (level {progress.nextLevel})
              </p>
            )}
            {!me.rank && me.reputationPoints === 0 && (
              <p className="mt-4 text-sm text-amber-200/90">
                No points yet. Scout a listing (signed in), vote, trade, or comment to start earning.
              </p>
            )}
            <Link
              href={`/portfolio/${me.userId}`}
              className="mt-4 inline-block text-sm text-amber-300 hover:underline"
            >
              View public profile →
            </Link>
          </section>
        )}

        {!session && (
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]/40 p-5 text-sm text-[var(--color-muted)]">
            <Link href="/login" className="font-medium text-[var(--color-accent)] hover:underline">
              Sign in
            </Link>{' '}
            to see your live points, rank, and projected airdrop. Scout submissions must be signed
            in to earn points.
          </section>
        )}

        <section>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">Top 50 — points & airdrop share</h2>
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                Ranked by lifetime reputation points among{' '}
                {me?.totalParticipants ?? totalParticipants} active contributors.
              </p>
            </div>
            <Link
              href="/leaderboard"
              className="text-sm text-[var(--color-muted)] hover:text-white"
            >
              Paper trading leaderboard →
            </Link>
          </div>

          {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--color-border)]">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-[var(--color-card)] text-xs uppercase tracking-wider text-[var(--color-muted)]">
                <tr>
                  <th className="px-4 py-3">Rank</th>
                  <th className="px-4 py-3">Contributor</th>
                  <th className="px-4 py-3">Points</th>
                  <th className="px-4 py-3">Level</th>
                  <th className="px-4 py-3">Pool %</th>
                  <th className="px-4 py-3">Supply %</th>
                  <th className="px-4 py-3">Est. tokens</th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.length === 0 && !error && (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-muted)]">
                      No points earned yet. Be the first to{' '}
                      <Link href="/list-your-project" className="text-[var(--color-accent)]">
                        scout a project
                      </Link>
                      .
                    </td>
                  </tr>
                )}
                {leaderboard.map((entry) => {
                  const isYou = session?.user?.id === entry.userId;
                  return (
                    <tr
                      key={entry.userId}
                      className={`border-t border-[var(--color-border)] ${
                        isYou ? 'bg-amber-950/25' : 'bg-[var(--color-background)]'
                      }`}
                    >
                      <td className="px-4 py-3 font-semibold">#{entry.rank}</td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/portfolio/${entry.userId}`}
                          className="font-medium hover:text-[var(--color-accent)]"
                        >
                          {entry.displayName}
                          {isYou && (
                            <span className="ml-2 text-xs text-amber-300">(you)</span>
                          )}
                        </Link>
                        {entry.twitterHandle && (
                          <a
                            href={`https://x.com/${entry.twitterHandle}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-2 text-xs text-sky-400 hover:underline"
                          >
                            @{entry.twitterHandle}
                          </a>
                        )}
                      </td>
                      <td className="px-4 py-3 font-medium text-emerald-400">
                        {entry.reputationPoints.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-[var(--color-muted)]">
                        {contributorLevelLabel(entry.contributorLevel)}
                      </td>
                      <td className="px-4 py-3">{entry.airdropPoolPercent.toFixed(2)}%</td>
                      <td className="px-4 py-3">{entry.supplyPercent.toFixed(4)}%</td>
                      <td className="px-4 py-3 text-violet-200">
                        {formatTokenAmount(entry.estimatedTokens)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

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

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)]/60 px-4 py-3">
      <div className="text-xs uppercase tracking-wider text-[var(--color-muted)]">{label}</div>
      <div className="mt-1 text-lg font-bold text-white">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-[var(--color-muted)]">{hint}</div>}
    </div>
  );
}
