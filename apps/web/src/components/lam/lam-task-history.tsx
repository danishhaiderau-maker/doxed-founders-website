'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import { STATUS_META, type LamTask } from './types';

type Props = {
  accessToken: string;
  /** Optional: highlight the actively-selected task. */
  activeId?: string | null;
  onSelect?: (task: LamTask) => void;
};

/**
 * LamTaskHistory — list of past LAM tasks with status, duration, and a
 * cost badge in DDollar. Polls once on mount; the parent can remount it
 * to refresh. Kept read-only — selecting a row surfaces it back to the
 * parent (typically the submitter) for display.
 */
export function LamTaskHistory({ accessToken, activeId, onSelect }: Props) {
  const [tasks, setTasks] = useState<LamTask[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/lam/tasks?limit=15'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) setTasks((await res.json()) as LamTask[]);
    } catch {
      // surfaced by empty state
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4 text-xs text-zinc-600">
        Loading history…
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4 text-xs text-zinc-600">
        No LAM tasks yet. Submit one above.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
      <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        Recent tasks
      </h3>
      <ul className="space-y-1.5">
        {tasks.map((t) => {
          const meta = STATUS_META[t.status] ?? STATUS_META.RUNNING;
          const isActive = activeId === t.id;
          return (
            <li key={t.id}>
              <button
                onClick={() => onSelect?.(t)}
                className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition ${
                  isActive
                    ? 'border-violet-600/60 bg-violet-950/20'
                    : 'border-zinc-800/60 bg-black/20 hover:border-zinc-700'
                }`}
              >
                <span className={`shrink-0 text-[10px] font-semibold uppercase ${meta.color}`}>
                  {meta.label}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-zinc-300">{t.goal}</span>
                {typeof t.elapsedMs === 'number' && (
                  <span className="shrink-0 text-[10px] text-zinc-600">
                    {(t.elapsedMs / 1000).toFixed(1)}s
                  </span>
                )}
                {typeof t.costDdollar === 'number' && (
                  <span className="shrink-0 rounded bg-amber-950/40 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-amber-300">
                    {t.costDdollar} DD
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
