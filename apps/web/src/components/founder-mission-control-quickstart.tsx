'use client';

type Props = {
  onTryStatus: () => void;
  onTryResume: () => void;
  compact?: boolean;
};

/** Onboarding strip for new builders at /founder-den Mission Control. */
export function FounderMissionControlQuickstart({ onTryStatus, onTryResume, compact }: Props) {
  return (
    <div
      className={`rounded-xl border border-cyan-500/25 bg-cyan-950/15 ${
        compact ? 'px-3 py-2.5' : 'px-4 py-3'
      }`}
    >
      <p className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300/90">
        New here? Start in 30 seconds
      </p>
      {!compact && (
        <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">
          Founder OS is a <strong className="font-medium text-zinc-300">command center</strong>, not a
          generic chatbot. It reads your <strong className="font-medium text-zinc-300">GitHub repo</strong>{' '}
          and <strong className="font-medium text-zinc-300">Founder Vault</strong> — then routes questions
          to Founder Brain (status) or Cursor (code) only when you ask.
        </p>
      )}
      <ol className={`mt-2 space-y-1 text-xs text-zinc-400 ${compact ? '' : 'list-decimal pl-4'}`}>
        <li>
          <strong className="text-zinc-300">Resume</strong> — sync GitHub + vault briefing (no auto-build).
        </li>
        <li>
          <strong className="text-zinc-300">Ask</strong> — try:{' '}
          <button
            type="button"
            onClick={onTryStatus}
            className="font-medium text-cyan-300 underline decoration-cyan-500/50 hover:text-cyan-200"
          >
            What&apos;s the status?
          </button>{' '}
          (GitHub-grounded answer; stay on <span className="text-violet-300">Ask</span>, not Build).
        </li>
        <li>
          <strong className="text-zinc-300">Run build</strong> — only when you want Cursor to edit the repo.
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
