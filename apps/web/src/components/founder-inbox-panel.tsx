'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { buildFeedShareMessage, buildSiteUrl, formatRelativeTime } from '@dcf/utils';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import {
  AppNotification,
  fetchNotifications,
  fetchUnreadNotificationCount,
  markNotificationRead,
} from '@/lib/api';

function inboxAccent(type: string, read: boolean) {
  if (type === 'FOUNDER_EVENT') return 'border-violet-500/40 bg-violet-950/20';
  if (type === 'BUILD_QUEUE') return 'border-violet-500/40 bg-violet-950/20';
  if (type === 'AGENT_RESULT') return 'border-purple-500/40 bg-purple-950/20';
  if (type === 'TRADER_WIN') return 'border-emerald-500/40 bg-emerald-950/20';
  if (type === 'TRADER_LOSS') return 'border-red-500/40 bg-red-950/20';
  if (read) return 'border-zinc-800 bg-zinc-900/30 opacity-75';
  return 'border-emerald-500/30 bg-zinc-900/50';
}

function notificationShareUrl(link: string | null, origin: string) {
  if (link?.startsWith('http')) return link;
  if (link) return buildSiteUrl(origin, link);
  return buildSiteUrl(origin, '/founder-den');
}

export type FounderInboxPanelProps = {
  accessToken: string;
  /** Sidebar compact mode — last 10 alerts with quick actions */
  compact?: boolean;
  /** Full-page notifications view */
  full?: boolean;
};

function NotificationActions({
  notification,
  onRead,
  compact,
}: {
  notification: AppNotification;
  onRead: (id: string) => void;
  compact?: boolean;
}) {
  const origin = useShareOrigin();
  const shareText = buildFeedShareMessage({
    headline: notification.title,
    detail: notification.body,
  });
  const shareUrl = notificationShareUrl(notification.link, origin);

  return (
    <div className={`flex flex-wrap gap-1.5 ${compact ? '' : 'mt-2'}`}>
      <ShareOnXButton
        text={shareText}
        url={shareUrl}
        label={compact ? '𝕏' : 'Share on X'}
        className={compact ? 'px-1.5 py-0.5 text-[10px]' : ''}
        stopPropagation
      />
      {notification.link && (
        <Link
          href={notification.link}
          className={`rounded font-medium text-white ${
            compact
              ? 'bg-violet-600/90 px-1.5 py-0.5 text-[10px]'
              : 'bg-violet-600/90 px-2 py-1 text-[10px]'
          }`}
        >
          Open
        </Link>
      )}
      {!notification.readAt && (
        <button
          type="button"
          onClick={() => onRead(notification.id)}
          className={`rounded border border-zinc-700 text-zinc-500 ${
            compact ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-[10px]'
          }`}
        >
          Mark read
        </button>
      )}
    </div>
  );
}

export function FounderInboxPanel({ accessToken, compact = false, full = false }: FounderInboxPanelProps) {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [notes, unreadRes] = await Promise.all([
        fetchNotifications(accessToken),
        fetchUnreadNotificationCount(accessToken),
      ]);
      const limit = full ? 40 : compact ? 10 : 8;
      setItems(notes.slice(0, limit));
      setUnread(unreadRes.count);
    } catch {
      setItems([]);
      setUnread(0);
    } finally {
      setLoading(false);
    }
  }, [accessToken, compact, full]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load]);

  async function handleRead(id: string) {
    await markNotificationRead(id, accessToken);
    load();
  }

  if (compact) {
    return (
      <div className="border-t border-zinc-800/60 px-2 py-3">
        <div className="flex items-center justify-between gap-2 px-2">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-violet-400">
              Notifications
            </p>
            <p className="mt-0.5 text-[10px] text-zinc-600">Latest · share to X</p>
          </div>
          {unread > 0 && (
            <span className="rounded-full bg-violet-600 px-2 py-0.5 text-[10px] font-semibold text-white">
              {unread}
            </span>
          )}
        </div>
        <ul className="mt-2 max-h-[280px] space-y-1.5 overflow-y-auto px-1">
          {loading && <li className="px-2 text-[11px] text-zinc-500">Loading…</li>}
          {!loading && items.length === 0 && (
            <li className="px-2 text-[11px] text-zinc-500">No notifications yet.</li>
          )}
          {items.map((n) => (
            <li
              key={n.id}
              className={`rounded-lg border p-2 ${inboxAccent(n.type, Boolean(n.readAt))}`}
            >
              <p className="text-[11px] font-medium leading-snug text-white line-clamp-2">{n.title}</p>
              <p className="mt-0.5 text-[10px] text-zinc-500 line-clamp-1">{n.body}</p>
              <div className="mt-1.5">
                <NotificationActions notification={n} onRead={handleRead} compact />
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-white">Notifications</h3>
            {unread > 0 && (
              <span className="rounded-full bg-violet-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                {unread} unread
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Build queue · agents · community · funding — share updates on X
          </p>
        </div>
      </div>

      <ul className="mt-4 space-y-2">
        {loading && <li className="text-sm text-zinc-500">Loading…</li>}
        {!loading && items.length === 0 && (
          <li className="text-sm text-zinc-500">No notifications yet.</li>
        )}
        {items.map((n) => (
          <li
            key={n.id}
            className={`rounded-xl border p-3 ${inboxAccent(n.type, Boolean(n.readAt))}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-white">{n.title}</p>
                <p className="mt-0.5 text-xs text-zinc-500 line-clamp-3">{n.body}</p>
                <p className="mt-1 text-[10px] text-zinc-600">
                  {formatRelativeTime(n.createdAt)}
                </p>
              </div>
            </div>
            <NotificationActions notification={n} onRead={handleRead} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Hook for sidebar unread badge on Notifications nav item */
export function useFounderUnreadCount(accessToken: string) {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const res = await fetchUnreadNotificationCount(accessToken);
        if (!cancelled) setCount(res.count);
      } catch {
        if (!cancelled) setCount(0);
      }
    }
    poll();
    const interval = setInterval(poll, 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [accessToken]);

  return count;
}
