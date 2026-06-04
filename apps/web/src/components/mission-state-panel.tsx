'use client';

import { useCallback, useEffect, useState } from 'react';
import type { FounderMemoryGraph, FounderMemoryGraphPatch } from '@dcf/utils';
import { fetchCopilotMemoryGraph, patchCopilotMemoryGraph } from '@/lib/api';

type Props = {
  accessToken: string;
  initial?: FounderMemoryGraph | null;
  lastCommit?: string | null;
  openTaskCount?: number;
  onSaved?: () => void;
};

export function MissionStatePanel({
  accessToken,
  initial,
  lastCommit,
  openTaskCount = 0,
  onSaved,
}: Props) {
  const [graph, setGraph] = useState<FounderMemoryGraph | null>(initial ?? null);
  const [draft, setDraft] = useState<FounderMemoryGraphPatch>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      const g = await fetchCopilotMemoryGraph(accessToken);
      setGraph(g);
      setDraft({});
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load mission state');
    }
  }, [accessToken]);

  useEffect(() => {
    if (!initial) void load();
    else setGraph(initial);
  }, [initial, load]);

  async function save() {
    if (!Object.keys(draft).length) return;
    setBusy(true);
    setErr(null);
    try {
      const g = await patchCopilotMemoryGraph(draft, accessToken);
      setGraph(g);
      setDraft({});
      setEditing(false);
      onSaved?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  }

  if (!graph) {
    return (
      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-4 text-sm text-zinc-500">
        Loading mission state…
      </div>
    );
  }

  const field = (
    label: string,
    key: keyof FounderMemoryGraphPatch,
    placeholder: string,
    multiline = false,
  ) => {
    const value =
      (draft[key] as string | null | undefined) ??
      (graph[key as keyof FounderMemoryGraph] as string | null) ??
      '';
    return (
      <label className="block text-xs">
        <span className="font-medium uppercase tracking-wider text-zinc-500">{label}</span>
        {multiline ? (
          <textarea
            rows={2}
            value={value}
            placeholder={placeholder}
            onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value || null }))}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950/80 px-2 py-1.5 text-sm text-white"
          />
        ) : (
          <input
            type="text"
            value={value}
            placeholder={placeholder}
            onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value || null }))}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950/80 px-2 py-1.5 text-sm text-white"
          />
        )}
      </label>
    );
  };

  return (
    <section className="rounded-2xl border border-cyan-500/25 bg-gradient-to-br from-cyan-950/15 to-zinc-900/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-400">
            Mission State
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            Resume Work and Continue last task read this first — then GitHub and builder agents.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setEditing((e) => !e)}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-cyan-500/40"
          >
            {editing ? 'Hide editor' : 'Edit'}
          </button>
          {editing && (
            <button
              type="button"
              disabled={busy || !Object.keys(draft).length}
              onClick={() => void save()}
              className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>
      </div>

      <dl className="mt-4 grid gap-2 rounded-xl border border-zinc-800/80 bg-black/30 px-4 py-3 text-sm sm:grid-cols-2">
        <div className="sm:col-span-2">
          <dt className="text-[10px] uppercase tracking-wider text-zinc-500">Goal</dt>
          <dd className="font-medium text-white">{graph.active_goal}</dd>
        </div>
        {graph.current_sprint && (
          <div>
            <dt className="text-[10px] uppercase tracking-wider text-zinc-500">Sprint</dt>
            <dd className="text-zinc-200">{graph.current_sprint}</dd>
          </div>
        )}
        {graph.current_task && (
          <div>
            <dt className="text-[10px] uppercase tracking-wider text-zinc-500">Task</dt>
            <dd className="text-zinc-200">{graph.current_task}</dd>
          </div>
        )}
        {graph.blocked_by && (
          <div className="sm:col-span-2">
            <dt className="text-[10px] uppercase tracking-wider text-amber-500/90">Blocked by</dt>
            <dd className="text-amber-100/90">{graph.blocked_by}</dd>
          </div>
        )}
        {graph.next_action && (
          <div className="sm:col-span-2">
            <dt className="text-[10px] uppercase tracking-wider text-emerald-500/90">Next action</dt>
            <dd className="text-emerald-100/90">{graph.next_action}</dd>
          </div>
        )}
        {(graph.current_branch || graph.current_pr) && (
          <div className="sm:col-span-2 flex flex-wrap gap-3 text-xs text-zinc-500">
            {graph.current_branch && <span>Branch: {graph.current_branch}</span>}
            {graph.current_pr && (
              <a href={graph.current_pr} className="text-cyan-400 hover:underline" target="_blank" rel="noreferrer">
                PR linked
              </a>
            )}
          </div>
        )}
        {(lastCommit || openTaskCount > 0) && (
          <div className="sm:col-span-2 text-[10px] text-zinc-600">
            {lastCommit ? `Last commit on record · ` : ''}
            {openTaskCount > 0 ? `${openTaskCount} open queue items` : ''}
          </div>
        )}
      </dl>

      {editing && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {field('Goal', 'active_goal', 'North-star outcome')}
          {field('Sprint', 'current_sprint', 'This week / milestone')}
          {field('Current task', 'current_task', 'What you are doing now')}
          {field('Blocked by', 'blocked_by', 'Optional blocker')}
          {field('Next action', 'next_action', 'Smallest next step')}
          {field('Branch', 'current_branch', 'feature/…')}
          {field('PR', 'current_pr', 'https://github.com/…/pull/N')}
        </div>
      )}

      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </section>
  );
}
