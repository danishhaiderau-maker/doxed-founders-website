'use client';

import type { FounderOnboardingStatus } from '@/lib/api';

const FIRST_PROMPTS = [
  {
    label: 'Setup check',
    prompt: "What's my setup status? Summarize what's connected and what I should do next.",
  },
  {
    label: 'This week',
    prompt: 'What changed this week in my repo? What is the most pressing next step?',
  },
  {
    label: 'Remote build',
    prompt: 'Review my open tasks and suggest one small improvement I can dispatch to Cursor.',
    needsCursor: true,
  },
] as const;

type Props = {
  status: FounderOnboardingStatus;
  onLaunchPrompt: (prompt: string) => void;
  onDismiss?: () => void;
};

export function FounderOnboardingComplete({ status, onLaunchPrompt, onDismiss }: Props) {
  const canBuild = status.remoteBuildReady ?? (status.githubConnected && status.builderConnected);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-emerald-500/40 bg-gradient-to-br from-emerald-950/40 via-[#0a0a12] to-violet-950/20 p-5 sm:p-6">
      <div className="pointer-events-none absolute -left-6 -top-6 h-28 w-28 rounded-full bg-emerald-500/10 blur-2xl" />
      <div className="relative">
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-300">
          Founder OS · ready
        </p>
        <h2 className="mt-1 text-xl font-bold text-white">
          {status.projectName ? `${status.projectName} is live` : 'Your command center is live'}
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-zinc-400">
          Founder Brain is your single interface — research, plan, and build from here or your
          Android app. {status.pathLabel && <>Path: {status.pathLabel}.</>}
        </p>

        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <StatusPill ok={status.llmConnected} label="AI Stack" />
          <StatusPill ok={status.githubConnected} label="GitHub" />
          <StatusPill ok={status.builderConnected} label="Cursor" optional />
          <StatusPill ok={canBuild} label="Remote build" optional />
        </div>

        {!canBuild && (
          <p className="mt-3 text-xs text-amber-200/90">
            Tip: connect GitHub + Cursor API to dispatch code from your phone — same pattern we use
            for the showcase bot (home PC + tunnel instead of Railway).
          </p>
        )}

        <p className="mt-5 text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Try your first command
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {FIRST_PROMPTS.filter((p) => !('needsCursor' in p && p.needsCursor) || canBuild).map(
            (item) => (
              <button
                key={item.prompt}
                type="button"
                onClick={() => onLaunchPrompt(item.prompt)}
                className="rounded-full border border-violet-500/40 bg-violet-950/30 px-4 py-2 text-sm text-violet-100 hover:border-violet-400 hover:bg-violet-900/40"
              >
                {item.label}
              </button>
            ),
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => onLaunchPrompt(FIRST_PROMPTS[0].prompt)}
            className="rounded-xl bg-white px-5 py-2.5 text-sm font-semibold text-black hover:bg-zinc-100"
          >
            Open Development Workspace →
          </button>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="rounded-xl border border-zinc-700 px-5 py-2.5 text-sm text-zinc-400 hover:text-white"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusPill({
  ok,
  label,
  optional,
}: {
  ok?: boolean;
  label: string;
  optional?: boolean;
}) {
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 ${
        ok
          ? 'border-emerald-500/40 bg-emerald-950/40 text-emerald-200'
          : optional
            ? 'border-zinc-700 text-zinc-500'
            : 'border-amber-500/40 bg-amber-950/30 text-amber-200'
      }`}
    >
      {ok ? '✓' : optional ? '○' : '!'} {label}
    </span>
  );
}
