'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { formatRelativeTime } from '@dcf/utils';
import {
  AppNotification,
  fetchNotifications,
  fetchUnreadNotificationCount,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/lib/api';
import { subscribeInboxRefresh } from '@/lib/inbox-refresh';

function accent(type: string, read: boolean) {
  if (type.includes('AGENT') || type === 'AGENT_RESULT') return 'border-purple-500/40';
  if (type.includes('TRADER') || type === 'TRADER_WIN') return 'border-emerald-500/40';
  if (type.includes('SCAM') || type.includes('INVESTIGATION')) return 'border-amber-500/40';
  if (!read) return 'border-emerald-500/30';
  return 'border-zinc-800';
}

export function NotificationBell() {
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [notes, countRes] = await Promise.all([
        fetchNotifications(token),
        fetchUnreadNotificationCount(token),
      ]);
      setItems(notes.slice(0, 8));
      setUnread(countRes.count);
    } catch {
      setItems([]);
      setUnread(0);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    load();
    const interval = setInterval(load, 30_000);
    const onFocus = () => void load();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    const unsubInbox = subscribeInboxRefresh(() => void load());
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      unsubInbox();
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [token, load]);

  useEffect(() => {
    if (open && token) void load();
  }, [open, token, load]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);

  if (!token) {
    return (
      <Link
        href="/login?callbackUrl=/notifications"
        className="rounded-lg px-2.5 py-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
        title="Sign in for notifications"
      >
        🔔
      </Link>
    );
  }

  async function handleRead(id: string) {
    await markNotificationRead(id, token!);
    load();
  }

  async function handleReadAll() {
    await markAllNotificationsRead(token!);
    load();
  }

  return (
    <div className="relative overflow-visible" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="relative inline-flex overflow-visible rounded-lg px-2.5 py-1.5 text-zinc-300 transition hover:bg-zinc-800 hover:text-white"
        title={unread > 0 ? `${unread} unread notification${unread === 1 ? '' : 's'}` : 'Notifications'}
        aria-label={`Notifications${unread > 0 ? `, ${unread} unread` : ''}`}
      >
        🔔
        {unread > 0 && (
          <span className="pointer-events-none absolute -right-1 -top-1 z-10 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border border-emerald-300/40 bg-emerald-600 px-1 text-[10px] font-bold leading-none text-white shadow-[0_0_8px_rgba(16,185,129,0.45)]">
            {unread > 99 ? '99+' : unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-[min(100vw-2rem,360px)] rounded-xl border border-zinc-700 bg-zinc-950 py-1 shadow-xl">
          <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
            <p className="text-sm font-semibold text-white">Notifications</p>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => void handleReadAll()}
                className="text-xs text-emerald-400 hover:underline"
              >
                Mark all read
              </button>
            )}
          </div>

          {items.length === 0 ? (
            <p className="px-3 py-4 text-sm text-zinc-500">No notifications yet.</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((note) => (
                <li
                  key={note.id}
                  className={`border-b border-zinc-900/80 px-3 py-2 last:border-0 ${accent(note.type, !!note.readAt)} border-l-2`}
                >
                  <p className="text-sm font-medium text-white">{note.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-zinc-400">{note.body}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <span className="text-[10px] text-zinc-600">
                      {formatRelativeTime(note.createdAt)}
                    </span>
                    {note.link && (
                      <Link
                        href={note.link}
                        onClick={() => setOpen(false)}
                        className="text-[10px] font-medium text-violet-400 hover:underline"
                      >
                        Open
                      </Link>
                    )}
                    {!note.readAt && (
                      <button
                        type="button"
                        onClick={() => void handleRead(note.id)}
                        className="text-[10px] text-zinc-500 hover:text-white"
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <Link
            href="/notifications"
            onClick={() => setOpen(false)}
            className="block border-t border-zinc-800 px-3 py-2 text-center text-xs font-medium text-emerald-400 hover:bg-zinc-900"
          >
            View all notifications →
          </Link>
        </div>
      )}
    </div>
  );
}
