'use client';

import { useCallback, useEffect, useState } from 'react';
import { copilotResume, fetchCopilotMemory, fetchCopilotStandup, ProjectMemory } from '@/lib/api';

type FounderCopilotBriefingProps = {
  accessToken: string;
  onMessage?: (msg: string) => void;
  onRefresh?: () => void;
};

export function FounderCopilotBriefing({
  accessToken,
  onMessage,
  onRefresh,
}: FounderCopilotBriefingProps) {
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [standup, setStandup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const [mem, stand] = await Promise.all([
        fetchCopilotMemory(accessToken),
        fetchCopilotStandup(accessToken),
      ]);
      setMemory(mem);
      setStandup(stand.standup);
    } catch {
      setMemory(null);
      setStandup(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleResume() {
    setBusy(true);
    try {
      const result = await copilotResume(accessToken);
      onMessage?.(result.message);
      load();
      onRefresh?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Could not resume');
    } finally {
      setBusy(false);
    }
  }

  async function copyCursor() {
    if (!memory?.cursorCopy) return;
    await navigator.clipboard.writeText(memory.cursorCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (!memory) {
    return (
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6 text-sm text-zinc-500">
        Loading project memory…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-emerald-500/35 bg-gradient-to-br from-emerald-950/40 to-zinc-950 p-5 sm:p-6">
        <p className="text-lg font-semibold text-white">{memory.welcomeMessage}</p>
        <p className="mt-1 text-xs text-emerald-300/80">Founder Copilot · persistent project memory</p>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Project</p>
            <p className="mt-0.5 font-semibold text-white">{memory.project?.name ?? 'Activate founder profile'}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Current goal</p>
            <p className="mt-0.5 font-medium text-violet-200 line-clamp-2">{memory.currentGoal}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-zinc-500">Progress</p>
            <div className="mt-1 flex items-center gap-2">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${memory.progressPercent}%` }}
                />
              </div>
              <span className="text-sm font-bold text-emerald-300">{memory.progressPercent}%</span>
            </div>
          </div>
        </div>

        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div className="rounded-lg bg-black/30 px-3 py-2">
            <p className="text-[10px] uppercase text-zinc-600">Last activity</p>
            <p className="text-zinc-300">{memory.lastActivityLabel}</p>
          </div>
          <div className="rounded-lg bg-black/30 px-3 py-2">
            <p className="text-[10px] uppercase text-zinc-600">Last commit</p>
            <p className="truncate text-zinc-300">{memory.lastCommit ?? 'Connect GitHub to sync'}</p>
          </div>
        </div>

        {memory.openTasks.length > 0 && (
          <div className="mt-4">
            <p className="text-[10px] uppercase text-zinc-500">Remaining</p>
            <ul className="mt-2 space-y-1">
              {memory.openTasks.map((t) => (
                <li key={t.id} className="flex items-start gap-2 text-sm text-zinc-300">
                  <span className="text-zinc-600">□</span>
                  {t.title}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2">
          <p className="text-[10px] uppercase text-amber-400/80">Suggested next step</p>
          <p className="mt-0.5 text-sm font-medium text-amber-100">{memory.suggestedNextStep}</p>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={handleResume}
            className="rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy ? 'Loading…' : 'Continue where I left off'}
          </button>
          <button
            type="button"
            onClick={copyCursor}
            className="rounded-xl border border-indigo-500/40 px-4 py-2.5 text-sm text-indigo-200"
          >
            {copied ? 'Copied!' : 'Copy for builder'}
          </button>
        </div>

        {(memory.deployments.length > 0 || memory.raiseStatus) && (
          <div className="mt-4 flex flex-wrap gap-2 text-[11px]">
            {memory.deployments.map((d) => (
              <span
                key={d.provider}
                className="rounded-full border border-emerald-500/30 bg-emerald-950/30 px-2 py-0.5 text-emerald-300"
              >
                {d.label} · healthy
              </span>
            ))}
            {memory.raiseStatus && (
              <span className="rounded-full border border-violet-500/30 px-2 py-0.5 text-violet-300">
                Raise ${memory.raiseStatus.allocatedUsd.toLocaleString()} / $
                {memory.raiseStatus.goalUsd.toLocaleString()}
              </span>
            )}
          </div>
        )}
      </section>

      {standup && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Daily standup</p>
          <pre className="mt-2 whitespace-pre-wrap text-sm text-zinc-300">{standup}</pre>
        </section>
      )}
    </div>
  );
}
