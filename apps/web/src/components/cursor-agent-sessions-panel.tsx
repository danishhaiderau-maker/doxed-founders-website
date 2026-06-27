'use client';

import Link from 'next/link';
import type { FounderAgentRunRecord } from '@/lib/api';

type Props = {
  cursorAgentUrl?: string | null;
  cursorAgentId?: string | null;
  cursorLatestRunId?: string | null;
  activeRun?: FounderAgentRunRecord | null;
  onFollowUp?: (prompt: string) => void;
};

export function CursorAgentSessionsPanel({
  cursorAgentUrl,
  cursorAgentId,
  cursorLatestRunId,
  activeRun,
  onFollowUp,
}: Props) {
  const agentUrl =
    activeRun?.agentUrl ??
    cursorAgentUrl ??
    (cursorAgentId ? `https://cursor.com/agents/${cursorAgentId}` : null);

  if (!agentUrl && !activeRun) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-3 py-2.5">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Cursor agents</p>
        <p className="mt-1 text-[11px] text-zinc-600">
          Connect Cursor in onboarding or Settings → dispatch a build from Brain.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-indigo-500/30 bg-indigo-950/20 px-3 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-indigo-300">Cursor agents</p>
        {activeRun && (
          <span className="rounded-full border border-violet-500/40 bg-violet-950/50 px-2 py-0.5 text-[9px] text-violet-200">
            {activeRun.status}
          </span>
        )}
      </div>
      {activeRun && (
        <p className="mt-1 line-clamp-2 text-[11px] text-zinc-400">{activeRun.task}</p>
      )}
      {agentUrl && (
        <a
          href={agentUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex text-[11px] font-medium text-indigo-200 underline hover:text-white"
        >
          Open in Cursor ↗
        </a>
      )}
      {cursorLatestRunId && cursorAgentId && (
        <p className="mt-1 text-[9px] text-zinc-600">
          Run {cursorLatestRunId.slice(0, 8)}…
        </p>
      )}
      {onFollowUp && agentUrl && (
        <button
          type="button"
          onClick={() => onFollowUp('Continue the last Cursor agent task from where we left off.')}
          className="mt-2 block text-[10px] text-zinc-500 underline hover:text-zinc-300"
        >
          Follow up in Brain
        </button>
      )}
      <Link
        href="/settings/builder#remote-builder"
        className="mt-1 block text-[9px] text-zinc-600 hover:text-zinc-400"
      >
        Agent settings →
      </Link>
    </div>
  );
}
