'use client';

import type { CopilotUsageLine } from '@/lib/copilot-ai-stack';

type Props = {
  onTakeFullControl: () => void;
  onBuildWithCursor: () => void;
  usageLines?: CopilotUsageLine[];
  compact?: boolean;
};

/** Onboarding strip for new builders at /founder-den Development Workspace. */
export function FounderMissionControlQuickstart({
  onTakeFullControl,
  onBuildWithCursor,
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
        Founder OS commands
      </p>
      {!compact && (
        <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">
          Promo-tagged models are platform-billed for your first month. Pick an action below the chat —{' '}
          <strong className="font-medium text-zinc-300">Ask DeepSeek</strong>,{' '}
          <strong className="font-medium text-zinc-300">Ask Gemini</strong>, or{' '}
          <strong className="font-medium text-zinc-300">Build with Cursor</strong>.
        </p>
      )}
      <ol className={`mt-2 space-y-1 text-xs text-zinc-400 ${compact ? '' : 'list-decimal pl-4'}`}>
        {(usageLines ?? [
          {
            title: 'Take full control',
            detail: 'Sync GitHub + vault and push updates to your Sovereign stack.',
          },
          {
            title: 'Build with Cursor',
            detail: 'Direct code agent — implements in your GitHub repo.',
          },
        ]).map((line) => (
          <li key={line.title}>
            <strong className="text-zinc-300">{line.title}</strong>
            {line.detail ? ` — ${line.detail}` : ''}
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onTakeFullControl}
          className="rounded-lg bg-violet-600/90 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-violet-500"
        >
          Take full control
        </button>
        <button
          type="button"
          onClick={onBuildWithCursor}
          className="rounded-lg bg-emerald-600/80 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-emerald-500"
        >
          Build with Cursor
        </button>
      </div>
    </div>
  );
}
