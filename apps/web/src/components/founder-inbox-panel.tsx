'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { AppNotification, fetchNotifications, markNotificationRead } from '@/lib/api';

function inboxAccent(type: string, read: boolean) {
  if (type === 'FOUNDER_EVENT') return 'border-violet-500/40 bg-violet-950/20';
  if (type === 'BUILD_QUEUE') return 'border-violet-500/40 bg-violet-950/20';
  if (type === 'AGENT_RESULT') return 'border-purple-500/40 bg-purple-950/20';
  if (type === 'TRADER_WIN') return 'border-emerald-500/40 bg-emerald-950/20';
  if (type === 'TRADER_LOSS') return 'border-red-500/40 bg-red-950/20';
  if (read) return 'border-zinc-800 bg-zinc-900/30 opacity-75';
  return 'border-emerald-500/30 bg-zinc-900/50';
}

export type FounderInboxPanelProps = {
  accessToken: string;
  /** Sidebar compact mode — last 10 copilot / platform alerts only */
  compact?: boolean;
};

export function FounderInboxPanel({ accessToken, compact = false }: FounderInboxPanelProps) {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const notes = await fetchNotifications(accessToken, compact ? undefined : undefined);
      setItems(compact ? notes.slice(0, 10) : notes.slice(0, 8));
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [accessToken, compact]);

  useEffect(() => {
    load();
    if (!compact) return undefined;
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, [load, compact]);

  async function handleRead(id: string) {
    await markNotificationRead(id, accessToken);
    load();
  }

  if (compact) {
    return (
      <div className="border-t border-zinc-800/60 px-2 py-3">
        <div className="px-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-violet-400">Copilot alerts</p>
          <p className="mt-0.5 text-[10px] text-zinc-600">Last 10 · from Founder OS</p>
        </div>
        <ul className="mt-2 max-h-[320px] space-y-1.5 overflow-y-auto px-1">
          {loading && <li className="px-2 text-[11px] text-zinc-500">Loading…</li>}
          {!loading && items.length === 0 && (
            <li className="px-2 text-[11px] text-zinc-500">No alerts yet — ask Copilot something.</li>
          )}
          {items.map((n) => (
            <li
              key={n.id}
              className={`rounded-lg border p-2 ${inboxAccent(n.type, Boolean(n.readAt))}`}
            >
              <p className="text-[11px] font-medium leading-snug text-white line-clamp-2">{n.title}</p>
              <p className="mt-0.5 text-[10px] text-zinc-500 line-clamp-1">{n.body}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {n.link && (
                  <Link
                    href={n.link}
                    className="rounded bg-violet-600/90 px-1.5 py-0.5 text-[10px] font-medium text-white"
                  >
                    Open
                  </Link>
                )}
                {!n.readAt && (
                  <button
                    type="button"
                    onClick={() => handleRead(n.id)}
                    className="rounded border border-zinc-700 px-1.5 py-0.5 text-[10px] text-zinc-500"
                  >
                    Read
                  </button>
                )}
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
          <h3 className="font-semibold text-white">Founder Inbox</h3>
          <p className="mt-1 text-xs text-zinc-500">
            Build queue · agent results · community · funding signals
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
