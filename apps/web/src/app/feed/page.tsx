'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav, getActiveUserId } from '@/components/site-nav';
import { PushNotificationPrompt } from '@/components/push-notification-prompt';
import { LinkifiedText } from '@/components/linkified-text';
import { formatUsd } from '@dcf/utils';
import {
  FeedComment,
  FeedPost,
  UnifiedFeedCategory,
  UnifiedFeedItem,
  PlatformPulseItem,
  fetchFeed,
  fetchFeedComments,
  fetchUnifiedFeed,
  postFeedComment,
  postInitialFeedComment,
} from '@/lib/api';

const CATEGORIES: { id: UnifiedFeedCategory; label: string }[] = [
  { id: 'all', label: 'All activity' },
  { id: 'founder', label: 'Founder' },
  { id: 'trading', label: 'Trading' },
  { id: 'market', label: 'Market' },
  { id: 'community', label: 'Community' },
];

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function tierBorder(tier: number) {
  if (tier === 1) return 'border-amber-500/40 bg-amber-950/10';
  if (tier === 2) return 'border-emerald-500/25 bg-emerald-950/5';
  return 'border-[var(--color-border)] bg-[var(--color-card)]';
}

export default function FeedPage() {
  const { data: session } = useSession();
  const [category, setCategory] = useState<UnifiedFeedCategory>('all');
  const [items, setItems] = useState<UnifiedFeedItem[]>([]);
  const [pulse, setPulse] = useState<PlatformPulseItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);
  const [tradePosts, setTradePosts] = useState<Record<string, FeedPost>>({});

  const load = useCallback(async (cat: UnifiedFeedCategory) => {
    try {
      const data = await fetchUnifiedFeed(cat);
      setItems(data.items);
      setPulse(data.pulse);
      setError(null);

      const tradeIds = data.items.filter((i) => i.tradePostId).map((i) => i.tradePostId!);
      if (tradeIds.length > 0) {
        const recent = await fetchFeed('recent');
        const map: Record<string, FeedPost> = {};
        for (const p of recent.posts) {
          if (tradeIds.includes(p.id)) map[p.id] = p;
        }
        setTradePosts(map);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load feed');
    }
  }, []);

  useEffect(() => {
    load(category);
    const interval = setInterval(() => load(category), 60_000);
    return () => clearInterval(interval);
  }, [category, load]);

  return (
    <div className="min-h-screen bg-[#050508]">
      <PushNotificationPrompt />
      <header className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[#050508]/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 md:px-6">
          <div>
            <Link href="/" className="text-xs uppercase tracking-widest text-[var(--color-muted)]">
              Doxxed crypto
            </Link>
            <h1 className="text-xl font-bold">Feed</h1>
            <p className="text-xs text-[var(--color-muted)]">Builders · traders · market pulse</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 md:grid-cols-[1fr_300px] md:px-6">
        <div>
          {pulse.length > 0 && (
            <section className="mb-6 rounded-xl border border-amber-500/30 bg-gradient-to-br from-amber-950/20 to-zinc-950 p-4">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-amber-300">
                Platform pulse
              </h2>
              <ul className="mt-3 space-y-2">
                {pulse.map((p) => (
                  <li key={p.id}>
                    {p.link ? (
                      <Link
                        href={p.link}
                        className="flex gap-2 rounded-lg px-2 py-1.5 text-sm transition hover:bg-black/30"
                      >
                        <span>{p.emoji}</span>
                        <span>
                          <span className="font-medium text-white">{p.headline}</span>
                          {p.detail && (
                            <span className="ml-2 text-xs text-zinc-500">{p.detail}</span>
                          )}
                        </span>
                      </Link>
                    ) : (
                      <div className="flex gap-2 px-2 py-1.5 text-sm">
                        <span>{p.emoji}</span>
                        <span className="font-medium text-white">{p.headline}</span>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="mb-4 flex flex-wrap gap-2">
            {CATEGORIES.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => setCategory(c.id)}
                className={`rounded-full px-4 py-1.5 text-sm ${
                  category === c.id
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:text-white'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>

          {error && <p className="mb-4 text-sm text-red-300">{error}</p>}

          <div className="space-y-3">
            {items.length === 0 && !error && (
              <div className="rounded-xl border border-dashed border-[var(--color-border)] p-12 text-center text-[var(--color-muted)]">
                No activity yet.{' '}
                <Link href="/paper-trading" className="text-[var(--color-accent)]">
                  Open Trading Alpha →
                </Link>
              </div>
            )}
            {items.map((item) =>
              item.tradePostId && tradePosts[item.tradePostId] ? (
                <TradeFeedCard
                  key={item.id}
                  item={item}
                  post={tradePosts[item.tradePostId]}
                  expanded={expandedTradeId === item.tradePostId}
                  onToggle={() =>
                    setExpandedTradeId(
                      expandedTradeId === item.tradePostId ? null : item.tradePostId!,
                    )
                  }
                  userId={getActiveUserId(session?.user?.id)}
                  onRefresh={() => load(category)}
                />
              ) : (
                <ActivityCard key={item.id} item={item} />
              ),
            )}
          </div>
        </div>

        <aside className="hidden md:block">
          <div className="sticky top-24 space-y-4">
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
              <h3 className="font-semibold">Unified activity</h3>
              <ul className="mt-3 space-y-2 text-sm text-[var(--color-muted)]">
                <li>Founder builds, deploys, and raise room signals</li>
                <li>Trader positions and conviction posts</li>
                <li>Hot buys when ≥2% of active traders align</li>
                <li>Scout votes and community follows</li>
              </ul>
              <Link
                href="/paper-trading"
                className="mt-4 block rounded-lg bg-[var(--color-accent)] py-2.5 text-center text-sm font-medium text-white"
              >
                Trading Alpha terminal
              </Link>
            </div>
            <Link
              href="/scout-votes"
              className="block rounded-xl border border-violet-500/30 bg-violet-950/20 p-4 text-sm text-violet-200 hover:border-violet-500/50"
            >
              Scout votes → validate listings before launch
            </Link>
          </div>
        </aside>
      </main>
    </div>
  );
}

function ActivityCard({ item }: { item: UnifiedFeedItem }) {
  const inner = (
    <article className={`rounded-xl border p-4 ${tierBorder(item.tier)}`}>
      <div className="flex items-start gap-3">
        <span className="text-lg">{item.emoji ?? '•'}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 uppercase tracking-wide">
              {item.category}
            </span>
            <span>{timeAgo(item.at)}</span>
            {item.tier === 1 && (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-300">Priority</span>
            )}
          </div>
          <p className="mt-1 font-semibold text-white">{item.headline}</p>
          {item.detail && <p className="mt-1 text-sm text-zinc-400">{item.detail}</p>}
        </div>
      </div>
    </article>
  );

  if (item.link) {
    return (
      <Link href={item.link} className="block transition hover:opacity-95">
        {inner}
      </Link>
    );
  }
  return inner;
}

function TradeFeedCard({
  item,
  post,
  expanded,
  onToggle,
  userId,
  onRefresh,
}: {
  item: UnifiedFeedItem;
  post: FeedPost;
  expanded: boolean;
  onToggle: () => void;
  userId: string | null;
  onRefresh: () => void;
}) {
  const [comments, setComments] = useState<FeedComment[]>([]);
  const [initialComment, setInitialComment] = useState<string | null>(post.initialComment);
  const [reply, setReply] = useState('');
  const [thesis, setThesis] = useState('');
  const [loading, setLoading] = useState(false);
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
      onRefresh();
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
      onRefresh();
    } finally {
      setLoading(false);
    }
  }

  return (
    <article className={`rounded-xl border p-4 ${tierBorder(item.tier)}`}>
      <div className="flex items-start gap-3">
        <span className="text-lg">{item.emoji ?? '📈'}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/portfolio/${post.trader.id}`} className="font-semibold hover:text-[var(--color-accent)]">
              {post.trader.name}
            </Link>
            <span
              className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                isBuy ? 'bg-emerald-500/15 text-emerald-400' : 'bg-orange-500/15 text-orange-400'
              }`}
            >
              {post.side}
            </span>
            <span className="text-xs text-[var(--color-muted)]">{timeAgo(item.at)}</span>
          </div>
          <div className="mt-3 flex items-center gap-3 rounded-lg bg-[var(--color-background)] p-3">
            <div className="flex-1">
              <Link href={`/project/${post.project.slug}`} className="font-bold hover:text-[var(--color-accent)]">
                {post.project.ticker}
              </Link>
              <p className="text-xs text-[var(--color-muted)]">{post.project.name}</p>
            </div>
            <div className="text-right">
              <p className="font-semibold">{formatUsd(post.amountUsd, 0)}</p>
              <p className="text-xs text-[var(--color-muted)]">paper</p>
            </div>
          </div>
          {initialComment && (
            <p className="mt-3 rounded-lg bg-[var(--color-background)] p-3 text-sm italic text-zinc-300">
              &ldquo;<LinkifiedText text={initialComment} />&rdquo;
            </p>
          )}
          <button type="button" onClick={onToggle} className="mt-3 text-sm text-[var(--color-accent)] hover:underline">
            {expanded ? 'Hide' : 'View'} discussion ({post.commentCount})
          </button>
        </div>
      </div>
      {expanded && (
        <div className="mt-4 border-t border-[var(--color-border)] pt-4 pl-10">
          {isOwner && !initialComment && (
            <div className="mb-4">
              <textarea
                value={thesis}
                onChange={(e) => setThesis(e.target.value)}
                rows={2}
                placeholder="Share your conviction thesis…"
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm"
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
          <ul className="space-y-2">
            {comments.map((c) => (
              <li key={c.id} className="text-sm">
                <span className="font-medium text-[var(--color-accent)]">{c.user.name}:</span>{' '}
                <LinkifiedText text={c.body} />
              </li>
            ))}
          </ul>
          {userId ? (
            <div className="mt-4 flex gap-2">
              <input
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="Agree or disagree…"
                className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm"
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
              to join the discussion.
            </p>
          )}
        </div>
      )}
    </article>
  );
}
