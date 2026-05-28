'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import {
  AppNotification,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/lib/api';

export default function NotificationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [error, setError] = useState<string | null>(null);

  const token = session?.accessToken;

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setItems(await fetchNotifications(token));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load notifications');
    }
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/notifications');
      return;
    }
    load();
  }, [status, load, router]);

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
            <h1 className="text-xl font-bold">Notifications</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <p className="text-sm text-[var(--color-muted)]">
            Founder updates, points earned, and platform alerts.
          </p>
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

        {error && <p className="text-sm text-[var(--color-danger)]">{error}</p>}

        <div className="space-y-3">
          {items.length === 0 && !error && (
            <p className="text-[var(--color-muted)]">No notifications yet.</p>
          )}
          {items.map((n) => (
            <article
              key={n.id}
              className={`rounded-xl border p-4 ${
                n.readAt
                  ? 'border-[var(--color-border)] bg-[var(--color-card)]/50 opacity-80'
                  : 'border-emerald-500/30 bg-[var(--color-card)]'
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="font-semibold">{n.title}</h2>
                  <p className="mt-1 text-sm text-[var(--color-muted)]">{n.body}</p>
                  <p className="mt-2 text-xs text-[var(--color-muted)]">
                    {new Date(n.createdAt).toLocaleString()}
                  </p>
                </div>
                <div className="flex gap-2">
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
          ))}
        </div>
      </main>
    </div>
  );
}
