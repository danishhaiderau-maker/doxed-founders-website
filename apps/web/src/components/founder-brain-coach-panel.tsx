'use client';

import type { SmartQuickPrompt } from '@dcf/utils';

type Props = {
  prompts: SmartQuickPrompt[];
  onSelect: (prompt: string, kind: SmartQuickPrompt['kind']) => void;
};

export function FounderBrainCoachPanel({ prompts, onSelect }: Props) {
  if (prompts.length === 0) return null;

  return (
    <div className="rounded-xl border border-violet-500/25 bg-violet-950/15 px-3 py-3 text-left">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300/90">
        Your expert guide — pick a starting point
      </p>
      <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">
        I will ask what you need, explain each integration, and offer Sovereign (local vault) vs cloud when you are
        ready.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {prompts.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onSelect(p.prompt, p.kind)}
            className={`rounded-full border px-2.5 py-1 text-[10px] font-medium transition hover:text-white ${
              p.kind === 'build'
                ? 'border-emerald-500/40 bg-emerald-950/30 text-emerald-200 hover:border-emerald-400'
                : p.kind === 'action'
                  ? 'border-cyan-500/40 bg-cyan-950/25 text-cyan-200 hover:border-cyan-400'
                  : 'border-violet-500/35 bg-violet-950/25 text-violet-200 hover:border-violet-400'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
