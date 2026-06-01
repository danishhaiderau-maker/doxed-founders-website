'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { contributorLevelLabel, VALIDATION_LABELS } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { ReputationBadge } from '@/components/landing/project-spotlight';
import {
  ScoutListing,
  castScoutVote,
  fetchOpenScoutListings,
  fetchVotingStats,
} from '@/lib/api';

function daysLeft(iso: string | null) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return 'Closed';
  const days = Math.ceil(ms / (24 * 60 * 60 * 1000));
  return `${days}d left`;
}

const VALIDATION_OPTIONS = Object.entries(VALIDATION_LABELS) as [
  keyof typeof VALIDATION_LABELS,
  string,
][];

export default function ScoutVotesPage() {
  const { data: session } = useSession();
  const [listings, setListings] = useState<ScoutListing[]>([]);
  const [stats, setStats] = useState<Awaited<ReturnType<typeof fetchVotingStats>> | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [voteForm, setVoteForm] = useState({
    validationCategory: 'LOOKS_LEGIT' as keyof typeof VALIDATION_LABELS,
    comment: '',
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [open, votingStats] = await Promise.all([
      fetchOpenScoutListings(),
      fetchVotingStats(),
    ]);
    setListings(open);
    setStats(votingStats);
  }, []);

  useEffect(() => {
    load().catch(() => setListings([]));
  }, [load]);

  async function handleVote(e: FormEvent, id: string) {
    e.preventDefault();
    if (!session?.accessToken) {
      setError('Sign in to vote');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await castScoutVote(id, voteForm, session.accessToken);
      setVoteForm({ validationCategory: 'LOOKS_LEGIT', comment: '' });
      setExpanded(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Vote failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold">Scout vote board</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Community filters listings before admin review. Build hype with thesis comments.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-10">
        {stats && (
          <div className="mb-8 rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-5 text-sm">
            <p className="font-medium text-emerald-200">Live vote threshold</p>
            <p className="mt-2 text-[var(--color-muted)]">
              {stats.activeUsers} signed-up users → need <strong className="text-white">{stats.requiredVoters}</strong>{' '}
              votes at <strong className="text-white">{stats.minYesPercent}%</strong> yes to reach admin.
              Window: {stats.votingWindowHours} hours. Formula: {stats.formula}
            </p>
            <Link href="/rules" className="mt-3 inline-block text-emerald-400 hover:underline">
              Full rules & vote math →
            </Link>
          </div>
        )}

        {error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        {listings.length === 0 ? (
          <div className="rounded-xl border border-[var(--color-border)] p-10 text-center text-[var(--color-muted)]">
            No listings in community voting right now.{' '}
            <Link href="/list-your-project" className="text-emerald-400 hover:underline">
              Scout a project →
            </Link>
          </div>
        ) : (
          <div className="space-y-6">
            {listings.map((item) => (
              <article
                key={item.id}
                className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)]/40 p-6"
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <h2 className="text-xl font-bold">
                      {item.projectName}{' '}
                      <span className="text-[var(--color-muted)]">({item.ticker})</span>
                    </h2>
                    <p className="mt-1 text-xs text-[var(--color-muted)]">
                      Verification score {item.verificationScore} · {daysLeft(item.votingClosesAt)} ·{' '}
                      {item.tally.yes}/{item.tally.total} yes ({item.tally.yesPercent}%) · need{' '}
                      {item.requiredVoters} votes
                    </p>
                    {item.user && (
                      <p className="mt-2 text-xs text-amber-200/80">
                        Scout: {item.user.name ?? 'Member'}{' '}
                        <ReputationBadge points={item.user.reputationPoints} level={item.user.contributorLevel} />
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                    className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm hover:border-emerald-400"
                  >
                    {expanded === item.id ? 'Close' : session ? 'Vote' : 'Sign in to vote'}
                  </button>
                </div>

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div className="rounded-lg bg-black/30 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
                      Why list this project
                    </p>
                    <p className="mt-2 text-sm text-white/90 whitespace-pre-wrap">{item.whyList}</p>
                  </div>
                  <div className="rounded-lg bg-black/30 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
                      Why founder is doxxed
                    </p>
                    <p className="mt-2 text-sm text-white/90 whitespace-pre-wrap">{item.whyDoxxed}</p>
                  </div>
                </div>

                {item.votes.length > 0 && (
                  <div className="mt-6 border-t border-[var(--color-border)] pt-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-muted)]">
                      Community votes ({item.votes.length})
                    </p>
                    <ul className="mt-3 space-y-3">
                      {item.votes.slice(0, 5).map((v) => (
                        <li key={v.id} className="rounded-lg border border-[var(--color-border)]/60 p-3 text-sm">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={
                                v.vote === 'YES'
                                  ? 'rounded bg-emerald-500/20 px-2 py-0.5 text-xs font-bold text-emerald-300'
                                  : 'rounded bg-red-500/20 px-2 py-0.5 text-xs font-bold text-red-300'
                              }
                            >
                              {v.vote}
                            </span>
                            <span className="text-[var(--color-muted)]">
                              {v.user.name ?? 'Trader'} · {contributorLevelLabel(v.user.contributorLevel)}
                            </span>
                          </div>
                          {v.whyList && (
                            <p className="mt-2 text-xs text-white/80">
                              <span className="text-[var(--color-muted)]">Why list:</span> {v.whyList}
                            </p>
                          )}
                          {v.whyDoxxed && (
                            <p className="mt-1 text-xs text-white/80">
                              <span className="text-[var(--color-muted)]">Why doxxed:</span> {v.whyDoxxed}
                            </p>
                          )}
                          {v.comment && (
                            <p className="mt-1 text-xs italic text-[var(--color-muted)]">{v.comment}</p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {expanded === item.id && session?.accessToken && (
                  <form onSubmit={(e) => handleVote(e, item.id)} className="mt-6 space-y-3 border-t border-[var(--color-border)] pt-4">
                    <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-muted)]">
                      Pick a validation category
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {VALIDATION_OPTIONS.map(([key, label]) => (
                        <button
                          key={key}
                          type="button"
                          onClick={() => setVoteForm((f) => ({ ...f, validationCategory: key }))}
                          className={`rounded-lg border px-3 py-2 text-left text-xs ${
                            voteForm.validationCategory === key
                              ? 'border-emerald-500 bg-emerald-500/10 font-semibold text-emerald-200'
                              : 'border-[var(--color-border)] hover:border-emerald-500/40'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <textarea
                      required={
                        voteForm.validationCategory === 'LOOKS_LEGIT' ||
                        voteForm.validationCategory === 'BUILDING_CONSISTENTLY' ||
                        voteForm.validationCategory === 'COMMUNITY_EXISTS'
                      }
                      minLength={20}
                      placeholder="Your validation review (20+ chars for positive signals)"
                      value={voteForm.comment}
                      onChange={(e) => setVoteForm((f) => ({ ...f, comment: e.target.value }))}
                      className="w-full rounded-lg border border-[var(--color-border)] bg-black/40 px-3 py-2 text-sm"
                      rows={3}
                    />
                    <button
                      type="submit"
                      disabled={busy}
                      className="rounded-lg bg-emerald-500 px-5 py-2 text-sm font-semibold text-black disabled:opacity-50"
                    >
                      Submit validation (+{10} DDollar)
                    </button>
                  </form>
                )}

                {expanded === item.id && !session && (
                  <p className="mt-4 text-sm text-[var(--color-muted)]">
                    <Link href="/login?callbackUrl=/scout-votes" className="text-emerald-400 hover:underline">
                      Sign in
                    </Link>{' '}
                    to vote and earn points.
                  </p>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
