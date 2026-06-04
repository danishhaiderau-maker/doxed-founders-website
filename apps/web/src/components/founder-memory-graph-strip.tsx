'use client';

import { useCallback, useEffect, useState } from 'react';
import type { FounderMemoryGraph, FounderMemoryGraphPatch } from '@dcf/utils';
import { fetchCopilotMemoryGraph, patchCopilotMemoryGraph } from '@/lib/api';

type Props = {
  accessToken: string;
  /** Initial graph from copilot/memory (optional). */
  initial?: FounderMemoryGraph | null;
  onSaved?: () => void;
};

export function FounderMemoryGraphStrip({ accessToken, initial, onSaved }: Props) {
  const [graph, setGraph] = useState<FounderMemoryGraph | null>(initial ?? null);
  const [draft, setDraft] = useState<FounderMemoryGraphPatch>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await fetchCopilotMemoryGraph(accessToken);
      setGraph(g);
      setDraft({});
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load memory');
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
        Loading founder memory…
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
    <section className="rounded-2xl border border-violet-500/25 bg-gradient-to-br from-violet-950/20 to-zinc-900/40 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-400">
            Founder memory
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            Copilot, agents, and Builder read this first — “continue where I left off” uses this graph.
          </p>
        </div>
        <button
          type="button"
          disabled={busy || !Object.keys(draft).length}
          onClick={() => void save()}
          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
        >
          {busy ? 'Saving…' : 'Save memory'}
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {field('Active goal', 'active_goal', 'e.g. Ship Discover bubble engine')}
        {field('Current task', 'current_task', 'What you are doing right now')}
        {field('Blocked by', 'blocked_by', 'Optional blocker')}
        {field('Next action', 'next_action', 'Smallest next step')}
        {field('Branch', 'current_branch', 'feature/my-branch')}
        {field('PR', 'current_pr', 'https://github.com/…/pull/42')}
      </div>

      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <p className="mt-2 text-[10px] text-zinc-600">
        Updated {new Date(graph.updated_at).toLocaleString()}
      </p>
    </section>
  );
}
