'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { formatRelativeTime } from '@dcf/utils';
import {
  fetchMessageThreads,
  fetchUnreadMessageCount,
  resolveMessageRecipient,
  sendPlatformMessage,
  type MessageThread,
} from '@/lib/api';
import { subscribeInboxRefresh } from '@/lib/inbox-refresh';

const POLL_MS = 15_000;

function sumThreadUnread(threads: MessageThread[]): number {
  return threads.reduce((n, t) => n + (t.unreadCount > 0 ? t.unreadCount : 0), 0);
}

export function useUnreadMessageCount(token: string | undefined) {
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    if (!token) {
      setCount(0);
      return;
    }
    try {
      const res = await fetchUnreadMessageCount(token);
      setCount(res.count);
    } catch {
      try {
        const threads = await fetchMessageThreads(token);
        setCount(sumThreadUnread(threads));
      } catch {
        setCount((prev) => prev);
      }
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      setCount(0);
      return;
    }
    void refresh();
    const interval = setInterval(() => void refresh(), POLL_MS);
    const onFocus = () => void refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    const unsubInbox = subscribeInboxRefresh(() => void refresh());
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      unsubInbox();
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [token, refresh]);

  return count;
}

function MessageCountBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="pointer-events-none absolute -right-1 -top-1 z-10 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border border-violet-300/40 bg-violet-600 px-1 text-[10px] font-bold leading-none text-white shadow-[0_0_8px_rgba(139,92,246,0.55)]"
      aria-hidden
    >
      {count > 99 ? '99+' : count > 9 ? '9+' : count}
    </span>
  );
}

export function PlatformMessagesBell() {
  const { data: session, status } = useSession();
  const token = session?.accessToken;
  const unread = useUnreadMessageCount(token);
  const [open, setOpen] = useState(false);
  const [threads, setThreads] = useState<MessageThread[]>([]);
  const [recipientQuery, setRecipientQuery] = useState('');
  const [resolvedLabel, setResolvedLabel] = useState<string | null>(null);
  const [resolvedUserId, setResolvedUserId] = useState<string | null>(null);
  const [composeBody, setComposeBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const data = await fetchMessageThreads(token);
      setThreads(data.slice(0, 6));
      setErr(null);
    } catch {
      setThreads([]);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    load();
    const interval = setInterval(load, POLL_MS);
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

  if (status === 'loading') {
    return (
      <span
        className="relative inline-flex rounded-lg px-2.5 py-1.5 text-zinc-500"
        title="Loading messages…"
        aria-hidden
      >
        ✉️
      </span>
    );
  }

  if (!token) {
    const signedIn = status === 'authenticated';
    return (
      <Link
        href={signedIn ? '/account?tab=messages' : '/login?callbackUrl=/account?tab=messages'}
        className="relative inline-flex overflow-visible rounded-lg px-2.5 py-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
        title={
          signedIn
            ? 'Open messages (refresh sign-in if counts do not appear)'
            : 'Sign in for messages'
        }
      >
        ✉️
      </Link>
    );
  }

  async function handleResolve() {
    if (!recipientQuery.trim()) return;
    setBusy(true);
    setErr(null);
    setResolvedLabel(null);
    setResolvedUserId(null);
    try {
      const res = await resolveMessageRecipient(recipientQuery.trim(), token!);
      setResolvedUserId(res.userId);
      setResolvedLabel(res.label);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'User not found');
    } finally {
      setBusy(false);
    }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!resolvedUserId || !composeBody.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await sendPlatformMessage(resolvedUserId, composeBody.trim(), token!);
      setComposeBody('');
      setRecipientQuery('');
      setResolvedLabel(null);
      setResolvedUserId(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Send failed');
    } finally {
      setBusy(false);
    }
  }

  const threadUnread = sumThreadUnread(threads);
  const badgeCount = Math.max(unread, threadUnread);

  return (
    <div className="relative overflow-visible" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="relative inline-flex overflow-visible rounded-lg px-2.5 py-1.5 text-zinc-300 transition hover:bg-zinc-800 hover:text-white"
        title={badgeCount > 0 ? `${badgeCount} unread message${badgeCount === 1 ? '' : 's'}` : 'Messages'}
        aria-label={`Messages${badgeCount > 0 ? `, ${badgeCount} unread` : ''}`}
      >
        ✉️
        <MessageCountBadge count={badgeCount} />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-[min(100vw-2rem,380px)] rounded-xl border border-zinc-700 bg-zinc-950 py-1 shadow-xl">
          <div className="border-b border-zinc-800 px-3 py-2">
            <p className="text-sm font-semibold text-white">Platform messages</p>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              Message traders by @X handle or messaging address.
            </p>
          </div>

          <form onSubmit={handleSend} className="border-b border-zinc-800 px-3 py-3">
            <label className="text-[11px] font-medium text-zinc-400">Recipient @handle or address</label>
            <div className="mt-1 flex gap-1">
              <input
                value={recipientQuery}
                onChange={(e) => {
                  setRecipientQuery(e.target.value);
                  setResolvedLabel(null);
                  setResolvedUserId(null);
                }}
                placeholder="@trader or handle#TAG"
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-2 py-1.5 text-xs text-white"
              />
              <button
                type="button"
                disabled={busy || !recipientQuery.trim()}
                onClick={() => void handleResolve()}
                className="shrink-0 rounded-lg bg-zinc-800 px-2 py-1.5 text-xs text-white disabled:opacity-50"
              >
                Find
              </button>
            </div>
            {resolvedLabel && resolvedUserId && (
              <p className="mt-1.5 text-xs text-cyan-200">
                To: {resolvedLabel}
                <span className="mt-0.5 block font-mono text-[10px] text-zinc-500">{resolvedUserId}</span>
              </p>
            )}
            <textarea
              value={composeBody}
              onChange={(e) => setComposeBody(e.target.value)}
              rows={2}
              placeholder="Write a message…"
              className="mt-2 w-full rounded-lg border border-zinc-700 bg-black px-2 py-1.5 text-xs text-white"
            />
            <button
              type="submit"
              disabled={busy || !resolvedUserId || !composeBody.trim()}
              className="mt-2 w-full rounded-lg bg-cyan-700 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
            >
              Send message
            </button>
          </form>

          {err && <p className="px-3 py-2 text-xs text-red-300">{err}</p>}

          {threads.length === 0 ? (
            <p className="px-3 py-3 text-xs text-zinc-500">No conversations yet.</p>
          ) : (
            <ul className="max-h-48 overflow-y-auto">
              {threads.map((t) => (
                <li key={t.otherUserId} className="border-b border-zinc-900/80 last:border-0">
                  <Link
                    href={`/account?tab=messages&with=${encodeURIComponent(t.otherUserId)}`}
                    onClick={() => setOpen(false)}
                    className="block px-3 py-2 hover:bg-zinc-900"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium text-white">{t.otherUserLabel}</p>
                      {t.unreadCount > 0 && (
                        <span className="rounded-full bg-violet-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
                          {t.unreadCount}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 line-clamp-1 text-xs text-zinc-500">{t.lastBody}</p>
                    <p className="mt-0.5 text-[10px] text-zinc-600">
                      {formatRelativeTime(t.lastAt)}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <Link
            href="/account?tab=messages"
            onClick={() => setOpen(false)}
            className="block border-t border-zinc-800 px-3 py-2 text-center text-xs font-medium text-cyan-400 hover:bg-zinc-900"
          >
            Open full inbox →
          </Link>
        </div>
      )}
    </div>
  );
}
