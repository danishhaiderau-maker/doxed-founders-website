'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { SiteNav, getActiveUserId } from '@/components/site-nav';
import { PushNotificationPrompt } from '@/components/push-notification-prompt';
import { LinkifiedText } from '@/components/linkified-text';
import { pushEngagementFlash } from '@/components/engagement-flash-layer';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import {
  formatUsd,
  buildSiteUrl,
  buildFeedShareMessage,
  buildListingShareMessage,
  buildPredictionShareMessage,
  buildHotBuyShareMessage,
} from '@dcf/utils';
import {
  FeedComment,
  FeedPost,
  UnifiedFeedCategory,
  UnifiedFeedItem,
  PlatformPulseItem,
  HotPredictionItem,
  ScoutListingFeedItem,
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
  const origin = useShareOrigin();
  const [category, setCategory] = useState<UnifiedFeedCategory>('all');
  const [items, setItems] = useState<UnifiedFeedItem[]>([]);
  const [pulse, setPulse] = useState<PlatformPulseItem[]>([]);
  const [hotQuestions, setHotQuestions] = useState<HotPredictionItem[]>([]);
  const [scoutListings, setScoutListings] = useState<ScoutListingFeedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);
  const [tradePosts, setTradePosts] = useState<Record<string, FeedPost>>({});

  const load = useCallback(async (cat: UnifiedFeedCategory) => {
    try {
      const data = await fetchUnifiedFeed(cat);
      setItems(data.items);
      setPulse(data.pulse);
      setHotQuestions(data.hotQuestions ?? []);
      setScoutListings(data.scoutListings ?? []);
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
                  <li key={p.id} className="flex items-start justify-between gap-2">
                    {p.link ? (
                      <Link
                        href={p.link}
                        className="flex min-w-0 flex-1 gap-2 rounded-lg px-2 py-1.5 text-sm transition hover:bg-black/30"
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
                      <div className="flex min-w-0 flex-1 gap-2 px-2 py-1.5 text-sm">
                        <span>{p.emoji}</span>
                        <span className="font-medium text-white">{p.headline}</span>
                      </div>
                    )}
                    <ShareOnXButton
                      text={buildFeedShareMessage({ headline: p.headline, detail: p.detail })}
                      url={p.link ? buildSiteUrl(origin, p.link) : buildSiteUrl(origin, '/feed')}
                      label="Share"
                      className="shrink-0"
                    />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {scoutListings.length > 0 && (
            <section className="mb-6 rounded-xl border border-sky-500/25 bg-sky-950/10 p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-sky-300">
                  Scout votes — validate listings
                </h2>
                <Link href="/scout-votes" className="text-xs text-sky-400 hover:underline">
                  Vote now →
                </Link>
              </div>
              <ul className="mt-3 space-y-2">
                {scoutListings.map((s) => (
                  <li key={s.id} className="flex items-start justify-between gap-2">
                    <Link
                      href="/scout-votes"
                      className="block min-w-0 flex-1 rounded-lg border border-sky-500/20 bg-black/20 px-3 py-2.5 transition hover:border-sky-500/40"
                    >
                      <p className="font-medium text-white">
                        {s.projectName} ({s.ticker})
                      </p>
                      <p className="mt-0.5 text-xs text-zinc-500">
                        {s.voteCount} scout vote{s.voteCount === 1 ? '' : 's'}
                        {s.whyList ? ` · ${s.whyList}` : ''}
                      </p>
                    </Link>
                    <ShareOnXButton
                      text={buildListingShareMessage({
                        projectName: s.projectName,
                        ticker: s.ticker,
                        scoutThesis: s.whyList,
                      })}
                      url={buildSiteUrl(origin, '/scout-votes')}
                      label="Share"
                      className="shrink-0 mt-2"
                    />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {hotQuestions.length > 0 && (
            <section className="mb-6 rounded-xl border border-indigo-500/30 bg-indigo-950/15 p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-indigo-300">
                  Blazing predictions — stake paper $
                </h2>
                <Link href="/predict" className="text-xs text-indigo-400 hover:underline">
                  All markets →
                </Link>
              </div>
              <ul className="mt-3 space-y-2">
                {hotQuestions.map((q) => (
                  <li key={q.id} className="flex items-start justify-between gap-2">
                    <Link
                      href="/predict"
                      className="block min-w-0 flex-1 rounded-lg border border-indigo-500/25 bg-black/20 px-3 py-2.5 transition hover:border-indigo-400/50"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-medium uppercase text-indigo-300">
                          {q.projectTicker}
                        </span>
                        {q.heatLabel && (
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                              q.heatLabel === 'Blazing'
                                ? 'bg-orange-500/25 text-orange-200'
                                : 'bg-violet-500/20 text-violet-200'
                            }`}
                          >
                            {q.heatLabel}
                          </span>
                        )}
                        {q.totalPoolUsd > 0 && (
                          <span className="text-xs text-emerald-400">
                            Pool {formatUsd(q.totalPoolUsd, 0)}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-sm text-white">{q.question}</p>
                    </Link>
                    <ShareOnXButton
                      text={buildPredictionShareMessage({
                        projectName: q.projectName,
                        ticker: q.projectTicker,
                        question: q.question,
                        poolUsd: q.totalPoolUsd,
                      })}
                      url={buildSiteUrl(origin, '/predict')}
                      label="Share"
                      className="shrink-0 mt-2"
                    />
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
                <ActivityCard key={item.id} item={item} origin={origin} />
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
                <li>Blazing predictions — stake YES/NO on AI questions</li>
                <li>Scout votes to validate new listings</li>
              </ul>
              <Link
                href="/paper-trading"
                className="mt-4 block rounded-lg bg-[var(--color-accent)] py-2.5 text-center text-sm font-medium text-white"
              >
                Trading Alpha terminal
              </Link>
            </div>
            <Link
              href="/predict"
              className="block rounded-xl border border-indigo-500/30 bg-indigo-950/20 p-4 text-sm text-indigo-200 hover:border-indigo-500/50"
            >
              Predict the future → stake YES/NO on AI questions
            </Link>
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

function ActivityCard({ item, origin }: { item: UnifiedFeedItem; origin: string }) {
  const shareUrl = item.link ? buildSiteUrl(origin, item.link) : buildSiteUrl(origin, '/feed');
  const shareText =
    item.eventType === 'hot_buy' || item.eventType === 'top_trader_buy'
      ? buildHotBuyShareMessage({
          ticker: item.projectTicker ?? 'TOKEN',
          buyerNames: item.recentBuyerNames ?? [],
        })
      : item.eventType === 'listing_live'
        ? buildListingShareMessage({
            projectName:
              item.headline.replace(/^New listing:\s*/, '').replace(/\s*\([^)]*\)\s*$/, '').trim() ||
              item.headline,
            ticker: item.projectTicker ?? '',
            scoutThesis: item.detail,
          })
        : buildFeedShareMessage({ headline: item.headline, detail: item.detail });

  const content = (
    <>
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
    </>
  );

  return (
    <article className={`rounded-xl border p-4 ${tierBorder(item.tier)}`}>
      <div className="flex items-start gap-3">
        {item.link ? (
          <Link href={item.link} className="flex min-w-0 flex-1 items-start gap-3 transition hover:opacity-95">
            {content}
          </Link>
        ) : (
          <div className="flex min-w-0 flex-1 items-start gap-3">{content}</div>
        )}
        <ShareOnXButton text={shareText} url={shareUrl} label="Share" className="shrink-0" />
      </div>
    </article>
  );
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
      pushEngagementFlash({
        emoji: '💬',
        message: 'Comment posted — earn points for research on /predict',
        link: '/predict',
      });
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
