'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { AppNotification, fetchNotifications, markNotificationRead } from '@/lib/api';

type InboxFilter = 'all' | 'build' | 'agents' | 'community' | 'funding';

const FILTERS: { id: InboxFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'build', label: 'Build queue' },
  { id: 'agents', label: 'Agent results' },
  { id: 'community', label: 'Community' },
  { id: 'funding', label: 'Funding' },
];

function inboxAccent(type: string, read: boolean) {
  if (type === 'BUILD_QUEUE') return 'border-violet-500/40 bg-violet-950/20';
  if (type === 'AGENT_RESULT') return 'border-purple-500/40 bg-purple-950/20';
  if (type === 'TRADER_WIN') return 'border-emerald-500/40 bg-emerald-950/20';
  if (type === 'TRADER_LOSS') return 'border-red-500/40 bg-red-950/20';
  if (read) return 'border-zinc-800 bg-zinc-900/30 opacity-75';
  return 'border-emerald-500/30 bg-zinc-900/50';
}

export type FounderInboxPanelProps = {
  accessToken: string;
};

export function FounderInboxPanel({ accessToken }: FounderInboxPanelProps) {
  const [filter, setFilter] = useState<InboxFilter>('all');
  const [items, setItems] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await fetchNotifications(accessToken, filter === 'all' ? undefined : filter));
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [accessToken, filter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleRead(id: string) {
    await markNotificationRead(id, accessToken);
    load();
  }

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-white">Founder Inbox</h3>
          <p className="mt-1 text-xs text-zinc-500">
            Build queue · agent results · community · funding signals
          </p>
        </div>
        <Link href="/notifications" className="text-xs text-emerald-400 hover:underline">
          View all →
        </Link>
      </div>

      <nav className="mt-4 flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={`rounded-lg px-2.5 py-1 text-[11px] font-medium ${
              filter === f.id ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {f.label}
          </button>
        ))}
      </nav>

      <ul className="mt-4 space-y-2">
        {loading && <li className="text-sm text-zinc-500">Loading…</li>}
        {!loading && items.length === 0 && (
          <li className="text-sm text-zinc-500">No notifications in this filter yet.</li>
        )}
        {items.slice(0, 8).map((n) => (
          <li
            key={n.id}
            className={`rounded-xl border p-3 ${inboxAccent(n.type, Boolean(n.readAt))}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="text-sm font-medium text-white">{n.title}</p>
                <p className="mt-0.5 text-xs text-zinc-500 line-clamp-2">{n.body}</p>
                <p className="mt-1 text-[10px] text-zinc-600">
                  {new Date(n.createdAt).toLocaleString()}
                </p>
              </div>
              <div className="flex gap-2">
                {n.link && (
                  <Link
                    href={n.link}
                    className="rounded bg-emerald-600/90 px-2 py-1 text-[10px] font-medium text-white"
                  >
                    Open
                  </Link>
                )}
                {!n.readAt && (
                  <button
                    type="button"
                    onClick={() => handleRead(n.id)}
                    className="rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500"
                  >
                    Read
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
