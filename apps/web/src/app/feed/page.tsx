'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav, getActiveUserId } from '@/components/site-nav';
import { LinkifiedText } from '@/components/linkified-text';
import { formatUsd } from '@dcf/utils';
import {
  FeedComment,
  FeedPost,
  FounderUpdate,
  fetchFeed,
  fetchFeedComments,
  fetchPinnedFounderUpdates,
  postFeedComment,
  postInitialFeedComment,
} from '@/lib/api';

type Filter = 'recent' | 'discussed' | 'highlighted';

function formatMc(value: number | null) {
  if (value == null) return null;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B MC`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M MC`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K MC`;
  return `$${value.toFixed(0)} MC`;
}

function formatAmount(usd: number) {
  if (usd >= 1000) return `$${(usd / 1000).toFixed(1)}K`;
  return formatUsd(usd, 0);
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

export default function FeedPage() {
  const { data: session } = useSession();
  const [filter, setFilter] = useState<Filter>('recent');
  const [posts, setPosts] = useState<FeedPost[]>([]);
  const [highlighted, setHighlighted] = useState<FeedPost[]>([]);
  const [pinnedUpdates, setPinnedUpdates] = useState<FounderUpdate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async (f: Filter) => {
    try {
      const data = await fetchFeed(f);
      setPosts(data.posts);
      setError(null);
      if (f !== 'highlighted') {
        const hot = await fetchFeed('highlighted');
        setHighlighted(hot.posts);
      } else {
        setHighlighted(data.posts);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load feed');
    }
  }, []);

  useEffect(() => {
    load(filter);
    fetchPinnedFounderUpdates()
      .then(setPinnedUpdates)
      .catch(() => setPinnedUpdates([]));
    const interval = setInterval(() => {
      load(filter);
      fetchPinnedFounderUpdates()
        .then(setPinnedUpdates)
        .catch(() => setPinnedUpdates([]));
    }, 60000);
    return () => clearInterval(interval);
  }, [filter, load]);

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[#050508]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/" className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
              Doxxed crypto
            </Link>
            <h1 className="text-xl font-bold">Trading Feed</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 md:grid-cols-[1fr_320px] md:px-6">
        <div>
          {pinnedUpdates.length > 0 && (
            <section className="mb-6 rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-amber-300">
                📌 Pinned · Doxxed founder updates (X)
              </h2>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                Refreshed every 6 hours when X API sync is enabled · project-relevant only
              </p>
              <div className="mt-4 space-y-3">
                {pinnedUpdates.map((update) => (
                  <article
                    key={update.id}
                    className="rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-card)] p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
                      {update.project && (
                        <Link
                          href={`/project/${update.project.slug}`}
                          className="font-semibold text-emerald-300 hover:underline"
                        >
                          {update.project.name} ({update.project.ticker})
                        </Link>
                      )}
                      {update.founder && <span>· {update.founder.name}</span>}
                    </div>
                    <p className="mt-2 text-sm font-semibold leading-snug tracking-wide text-white">
                      {update.headline}
                    </p>
                    {update.summary && (
                      <p className="mt-2 line-clamp-2 text-xs text-[var(--color-muted)]">
                        {update.summary}
                      </p>
                    )}
                    <a
                      href={update.sourceUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-3 inline-block text-xs text-[var(--color-accent)] hover:underline"
                    >
                      View on X →
                    </a>
                  </article>
                ))}
              </div>
            </section>
          )}

          {highlighted.length > 0 && filter !== 'highlighted' && (
            <section className="mb-6">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-emerald-400">🔥 Most discussed (6h spotlight)</h2>
                <button
                  type="button"
                  onClick={() => setFilter('highlighted')}
                  className="text-xs text-[var(--color-accent)] hover:underline"
                >
                  View all
                </button>
              </div>
              <div className="flex gap-3 overflow-x-auto pb-2">
                {highlighted.map((post) => (
                  <button
                    key={post.id}
                    type="button"
                    onClick={() => setExpandedId(post.id)}
                    className="min-w-[220px] shrink-0 rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-4 text-left hover:border-emerald-500/60"
                  >
                    <p className="text-xs text-emerald-300">{post.trader.name}</p>
                    <p className="mt-1 font-semibold">{post.project.ticker}</p>
                    <p className="mt-2 text-lg font-bold text-emerald-400">
                      {post.commentCount} comments
                    </p>
                  </button>
                ))}
              </div>
            </section>
          )}

          <div className="mb-4 flex flex-wrap gap-2">
            {(
              [
                ['recent', 'Recent'],
                ['discussed', 'Most discussed'],
                ['highlighted', 'Hot now'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                className={`rounded-full px-4 py-1.5 text-sm ${
                  filter === key
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {error && <p className="mb-4 text-sm text-red-300">{error}</p>}

          <div className="space-y-3">
            {posts.length === 0 && !error && (
              <div className="rounded-xl border border-dashed border-[var(--color-border)] p-12 text-center text-[var(--color-muted)]">
                No trades yet.{' '}
                <Link href="/paper-trading" className="text-[var(--color-accent)]">
                  Make the first paper trade →
                </Link>
              </div>
            )}
            {posts.map((post) => (
              <FeedCard
                key={post.id}
                post={post}
                expanded={expandedId === post.id}
                onToggle={() => setExpandedId(expandedId === post.id ? null : post.id)}
                userId={getActiveUserId(session?.user?.id)}
                onCommentPosted={() => load(filter)}
              />
            ))}
          </div>
        </div>

        <aside className="hidden md:block">
          <div className="sticky top-24 space-y-4">
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
              <h3 className="font-semibold">How the feed works</h3>
              <ul className="mt-3 space-y-2 text-sm text-[var(--color-muted)]">
                <li>Every paper trade appears here.</li>
                <li>Add your thesis when you buy — others can debate it.</li>
                <li>Top commented trades get a 6-hour spotlight.</li>
                <li>Rankings refresh as new discussions heat up.</li>
              </ul>
              <Link
                href="/paper-trading"
                className="mt-4 block rounded-lg bg-[var(--color-accent)] py-2.5 text-center text-sm font-medium text-white"
              >
                Open terminal
              </Link>
            </div>
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 text-sm text-[var(--color-muted)]">
              Inspired by social trading apps — customized for curated, doxxed-founder intelligence on web.
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}

function FeedCard({
  post,
  expanded,
  onToggle,
  userId,
  onCommentPosted,
}: {
  post: FeedPost;
  expanded: boolean;
  onToggle: () => void;
  userId: string | null;
  onCommentPosted: () => void;
}) {
  const [comments, setComments] = useState<FeedComment[]>([]);
  const [initialComment, setInitialComment] = useState<string | null>(post.initialComment);
  const [reply, setReply] = useState('');
  const [loading, setLoading] = useState(false);
  const [thesis, setThesis] = useState('');
  const isBuy = post.side === 'BUY';
  const isOwner = userId === post.trader.id;

  useEffect(() => {
    if (!expanded) return;
    fetchFeedComments(post.id)
      .then((data) => {
        setComments(data.comments);
        setInitialComment(data.initialComment);
      })
      .catch(() => {});
  }, [expanded, post.id]);

  async function submitReply() {
    if (!userId || !reply.trim()) return;
    setLoading(true);
    try {
      const c = await postFeedComment(post.id, userId, reply.trim());
      setComments((prev) => [...prev, c]);
      setReply('');
      onCommentPosted();
    } finally {
      setLoading(false);
    }
  }

  async function submitThesis() {
    if (!userId || !thesis.trim() || !isOwner) return;
    setLoading(true);
    try {
      await postInitialFeedComment(post.id, userId, thesis.trim());
      setInitialComment(thesis.trim());
      setThesis('');
      onCommentPosted();
    } finally {
      setLoading(false);
    }
  }

  return (
    <article
      className={`rounded-xl border bg-[var(--color-card)] p-4 transition ${
        post.highlighted
          ? 'border-emerald-500/40 shadow-[0_0_24px_rgba(16,185,129,0.08)]'
          : 'border-[var(--color-border)]'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/20 text-sm font-bold text-[var(--color-accent)]">
          {post.trader.name.slice(0, 2).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/portfolio/${post.trader.id}`}
              className="font-semibold hover:text-[var(--color-accent)]"
            >
              {post.trader.name}
            </Link>
            <span
              className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                isBuy
                  ? 'bg-emerald-500/15 text-emerald-400'
                  : 'bg-orange-500/15 text-orange-400'
              }`}
            >
              {post.side}
            </span>
            <span className="text-xs text-[var(--color-muted)]">{timeAgo(post.createdAt)}</span>
            {post.highlighted && (
              <span className="rounded-md bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-300">
                Hot
              </span>
            )}
          </div>

          <div className="mt-3 flex items-center gap-3 rounded-lg bg-[var(--color-background)] p-3">
            {post.project.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={post.project.logoUrl} alt="" className="h-10 w-10 rounded-full" />
            ) : (
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-border)] text-xs font-bold">
                {post.project.ticker.slice(0, 2)}
              </div>
            )}
            <div className="flex-1">
              {post.project.slug ? (
                <Link
                  href={`/project/${post.project.slug}`}
                  className="font-bold hover:text-[var(--color-accent)]"
                >
                  {post.project.ticker}
                </Link>
              ) : (
                <p className="font-bold">{post.project.ticker}</p>
              )}
              <p className="text-xs text-[var(--color-muted)]">
                {formatMc(post.project.marketCap) ?? post.project.name}
              </p>
            </div>
            <div className="text-right">
              <p className="font-semibold">{formatAmount(post.amountUsd)}</p>
              <p className="text-xs text-[var(--color-muted)]">paper trade</p>
            </div>
          </div>

          {initialComment && (
            <p className="mt-3 rounded-lg bg-[var(--color-background)] p-3 text-sm italic text-zinc-300">
              &ldquo;
              <LinkifiedText text={initialComment} />
              &rdquo;
            </p>
          )}

          <button
            type="button"
            onClick={onToggle}
            className="mt-3 text-sm text-[var(--color-accent)] hover:underline"
          >
            {expanded ? 'Hide' : 'View'} {post.commentCount} comment
            {post.commentCount === 1 ? '' : 's'}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-4 border-t border-[var(--color-border)] pt-4 pl-[52px]">
          {isOwner && !initialComment && (
            <div className="mb-4">
              <p className="mb-2 text-xs text-[var(--color-muted)]">
                Share why you made this trade — your thesis hooks the discussion.
              </p>
              <textarea
                value={thesis}
                onChange={(e) => setThesis(e.target.value)}
                rows={2}
                placeholder="I think this could run because… Paste an X post link for context."
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
              />
              <button
                type="button"
                disabled={loading || !thesis.trim()}
                onClick={submitThesis}
                className="mt-2 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                Post thesis
              </button>
            </div>
          )}

          <ul className="space-y-3">
            {comments.map((c) => (
              <li key={c.id} className="flex gap-2 text-sm">
                <span className="font-medium text-[var(--color-accent)]">{c.user.name}:</span>
                <span className="text-zinc-300">
                  <LinkifiedText text={c.body} />
                </span>
              </li>
            ))}
          </ul>

          {userId ? (
            <div className="mt-4 flex gap-2">
              <input
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="Why do you agree or disagree? Paste X/Twitter links — they’ll open in a new tab."
                className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
              />
              <button
                type="button"
                disabled={loading || !reply.trim()}
                onClick={submitReply}
                className="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                Reply
              </button>
            </div>
          ) : (
            <p className="mt-4 text-sm text-[var(--color-muted)]">
              <Link href="/login" className="text-[var(--color-accent)]">
                Sign in
              </Link>{' '}
              or start paper trading to join the discussion.
            </p>
          )}
        </div>
      )}
    </article>
  );
}
