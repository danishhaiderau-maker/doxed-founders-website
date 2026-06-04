'use client';

import type { CopilotUsageLine } from '@/lib/copilot-ai-stack';

type Props = {
  onTryStatus: () => void;
  onTryResume: () => void;
  usageLines?: CopilotUsageLine[];
  compact?: boolean;
};

/** Onboarding strip for new builders at /founder-den Mission Control. */
export function FounderMissionControlQuickstart({
  onTryStatus,
  onTryResume,
  usageLines,
  compact,
}: Props) {
  return (
    <div
      className={`rounded-xl border border-cyan-500/25 bg-cyan-950/15 ${
        compact ? 'px-3 py-2.5' : 'px-4 py-3'
      }`}
    >
      <p className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300/90">
        How to use Mission Control
      </p>
      {!compact && (
        <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">
          Pick your connected model under the chat (e.g. Ollama, DeepSeek, Cursor). Instructions below
          match <strong className="font-medium text-zinc-300">your</strong> AI stack.
        </p>
      )}
      <ol className={`mt-2 space-y-1 text-xs text-zinc-400 ${compact ? '' : 'list-decimal pl-4'}`}>
        {(usageLines ?? [
          { title: 'Resume', detail: 'Sync GitHub + vault briefing (no code changes).' },
          {
            title: "What's the status?",
            detail: 'GitHub-grounded — use an Ask model in chat.',
          },
        ]).map((line) => (
          <li key={line.title}>
            <strong className="text-zinc-300">{line.title}</strong>
            {line.detail ? ` — ${line.detail}` : ''}
          </li>
        ))}
        <li>
          <button
            type="button"
            onClick={onTryStatus}
            className="font-medium text-cyan-300 underline decoration-cyan-500/50 hover:text-cyan-200"
          >
            Try: What&apos;s the status?
          </button>
        </li>
      </ol>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onTryStatus}
          className="rounded-lg bg-cyan-600/80 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-cyan-500"
        >
          Ask: What&apos;s the status?
        </button>
        <button
          type="button"
          onClick={onTryResume}
          className="rounded-lg border border-zinc-600 px-3 py-1.5 text-[11px] text-zinc-300 hover:border-violet-500/50"
        >
          ▶ Resume first
        </button>
      </div>
    </div>
  );
}
