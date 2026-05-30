'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import type { NotificationBuyerMeta } from '@dcf/utils';
import { buildSiteUrl, buildFeedShareMessage, buildHotBuyShareMessage, buildGrowthHotBuyTweet, buildListingShareMessage } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { NotificationBuyersPanel } from '@/components/notification-buyers-panel';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { FollowTraderButton } from '@/components/follow-trader-button';
import {
  AppNotification,
  fetchAccountFollowing,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/lib/api';

function parseBuyerMeta(raw: unknown): NotificationBuyerMeta | null {
  if (!raw || typeof raw !== 'object') return null;
  const m = raw as NotificationBuyerMeta;
  if (!m.buyers && !m.projectTicker) return null;
  return m;
}

export default function NotificationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const origin = useShareOrigin();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [category, setCategory] = useState<string>('all');
  const [error, setError] = useState<string | null>(null);
  const [followingIds, setFollowingIds] = useState<Set<string>>(new Set());

  const token = session?.accessToken;

  const CATEGORIES = [
    { id: 'all', label: 'All' },
    { id: 'following', label: 'Following' },
    { id: 'projects', label: 'Projects' },
    { id: 'market', label: 'Market' },
    { id: 'platform', label: 'Platform' },
  ] as const;

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [notes, following] = await Promise.all([
        fetchNotifications(token, category === 'all' ? undefined : category),
        fetchAccountFollowing(token),
      ]);
      setItems(notes);
      setFollowingIds(new Set(following.map((f) => f.userId)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load notifications');
    }
  }, [token, category]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/notifications');
      return;
    }
    load();
  }, [status, load, router, category]);

  function handleFollowChange(userId: string, following: boolean) {
    setFollowingIds((prev) => {
      const next = new Set(prev);
      if (following) next.add(userId);
      else next.delete(userId);
      return next;
    });
  }

  async function handleRead(id: string) {
    if (!token) return;
    await markNotificationRead(id, token);
    await load();
  }

  async function handleReadAll() {
    if (!token) return;
    await markAllNotificationsRead(token);
    await load();
  }

  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Home
            </Link>
            <h1 className="text-xl font-bold">Alerts</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-8">
        <div className="mb-4 flex flex-wrap gap-2">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setCategory(c.id)}
              className={`rounded-full px-3 py-1 text-sm ${
                category === c.id
                  ? 'bg-[var(--color-accent)] text-white'
                  : 'border border-[var(--color-border)] text-[var(--color-muted)]'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-[var(--color-muted)]">
            See who bought, follow traders, and track hot markets.
          </p>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/leaderboard"
              className="rounded-full border border-emerald-500/40 bg-emerald-950/30 px-3 py-1 text-xs font-medium text-emerald-300 hover:bg-emerald-950/50"
            >
              Top traders
            </Link>
            <Link
              href="/leaderboard?tab=losers"
              className="rounded-full border border-red-500/40 bg-red-950/30 px-3 py-1 text-xs font-medium text-red-300 hover:bg-red-950/50"
            >
              Top losers
            </Link>
            {items.some((n) => !n.readAt) && (
              <button
                type="button"
                onClick={handleReadAll}
                className="text-sm text-[var(--color-accent)] hover:underline"
              >
                Mark all read
              </button>
            )}
          </div>
        </div>

        {error && <p className="text-sm text-[var(--color-danger)]">{error}</p>}

        <div className="space-y-3">
          {items.length === 0 && !error && (
            <p className="text-[var(--color-muted)]">No notifications yet.</p>
          )}
          {items.map((n) => {
            const isWin = n.type === 'TRADER_WIN';
            const isLoss = n.type === 'TRADER_LOSS';
            const isHotBuy = n.type === 'TRENDING_BUYS';
            const isBuild = n.type === 'BUILD_QUEUE';
            const isAgent = n.type === 'AGENT_RESULT';
            const buyerMeta = parseBuyerMeta(n.metadata);
            const traderMeta = n.metadata as { traderUserId?: string; displayName?: string } | null;
            const displayBody =
              buyerMeta?.buyers?.length && isHotBuy
                ? `${buyerMeta.buyers.map((b) => b.displayName).slice(0, 5).join(', ')} paper-traded $${buyerMeta.projectTicker ?? 'token'}`
                : n.body;
            const shareUrl = n.link ? buildSiteUrl(origin, n.link) : buildSiteUrl(origin, '/notifications');
            const projectSlugMatch = n.link?.match(/\/project\/([^/?#]+)/);
            const projectSlug = buyerMeta?.projectSlug ?? projectSlugMatch?.[1];
            const shareText = isHotBuy && buyerMeta?.buyers?.length
              ? projectSlug
                ? buildGrowthHotBuyTweet({
                    ticker: buyerMeta.projectTicker ?? 'TOKEN',
                    projectName: buyerMeta.projectName ?? buyerMeta.projectTicker ?? 'Token',
                    projectSlug,
                    buyerNames: buyerMeta.buyers.map((b) => b.displayName),
                    origin,
                    scoutThesis: buyerMeta.scoutThesis ?? undefined,
                  })
                : buildHotBuyShareMessage({
                    ticker: buyerMeta.projectTicker ?? 'TOKEN',
                    buyerNames: buyerMeta.buyers.map((b) => b.displayName),
                  })
              : n.title.toLowerCase().includes('listing')
                ? buildListingShareMessage({
                    projectName: n.title.replace(/^.*?:\s*/, '').trim(),
                    ticker: buyerMeta?.projectTicker ?? '',
                    scoutThesis: n.body,
                  })
                : buildFeedShareMessage({ headline: n.title, detail: n.body });

            const accent = isHotBuy
              ? 'border-amber-500/40 bg-amber-950/20'
              : isBuild
                ? 'border-violet-500/40 bg-violet-950/20'
                : isAgent
                  ? 'border-purple-500/40 bg-purple-950/20'
                  : isWin
                    ? 'border-emerald-500/40 bg-emerald-950/20'
                    : isLoss
                      ? 'border-red-500/40 bg-red-950/20'
                      : n.readAt
                        ? 'border-[var(--color-border)] bg-[var(--color-card)]/50 opacity-80'
                        : 'border-emerald-500/30 bg-[var(--color-card)]';

            return (
              <article key={n.id} className={`rounded-xl border p-4 ${accent}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <h2 className="font-semibold">{n.title}</h2>
                    <p className="mt-1 text-sm text-[var(--color-muted)]">{displayBody}</p>

                    {buyerMeta && (
                      <NotificationBuyersPanel
                        metadata={buyerMeta}
                        token={token}
                        followingIds={followingIds}
                        onFollowChange={handleFollowChange}
                      />
                    )}

                    {traderMeta?.traderUserId && !buyerMeta && (
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Link
                          href={`/portfolio/${traderMeta.traderUserId}`}
                          className="text-sm font-medium text-emerald-400 hover:underline"
                        >
                          {traderMeta.displayName ?? 'View trader'}
                        </Link>
                        <FollowTraderButton
                          userId={traderMeta.traderUserId}
                          token={token}
                          initiallyFollowing={followingIds.has(traderMeta.traderUserId)}
                          onChange={(f) => handleFollowChange(traderMeta.traderUserId!, f)}
                        />
                      </div>
                    )}

                    <p className="mt-2 text-xs text-[var(--color-muted)]">
                      {new Date(n.createdAt).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <ShareOnXButton text={shareText} url={shareUrl} label="Share on X" />
                    {n.link && (
                      <Link
                        href={n.link}
                        className="rounded-lg bg-[var(--color-accent)]/90 px-3 py-1.5 text-xs font-medium text-white"
                      >
                        Open
                      </Link>
                    )}
                    {!n.readAt && (
                      <button
                        type="button"
                        onClick={() => handleRead(n.id)}
                        className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-muted)] hover:text-white"
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </main>
    </div>
  );
}
