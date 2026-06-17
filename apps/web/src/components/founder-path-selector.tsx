'use client';

import {
  ONBOARDING_PATHS,
  type OnboardingPathId,
} from '@dcf/utils';

type Props = {
  selectedPath: OnboardingPathId | null;
  onSelect: (path: OnboardingPathId) => void;
  disabled?: boolean;
  compact?: boolean;
};

export function FounderPathSelector({ selectedPath, onSelect, disabled, compact }: Props) {
  return (
    <div className={`grid gap-3 ${compact ? 'sm:grid-cols-2 lg:grid-cols-3' : 'sm:grid-cols-2 xl:grid-cols-3'}`}>
      {ONBOARDING_PATHS.map((path) => {
        const active = selectedPath === path.id;
        return (
          <button
            key={path.id}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(path.id)}
            className={`group relative rounded-xl border p-4 text-left transition ${
              active
                ? 'border-violet-400/70 bg-violet-950/40 ring-1 ring-violet-400/40'
                : 'border-zinc-800 bg-zinc-950/50 hover:border-zinc-600 hover:bg-zinc-900/60'
            } disabled:opacity-50`}
          >
            <div className="flex items-start gap-3">
              <span className="text-2xl" aria-hidden>
                {path.icon}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-white">{path.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-zinc-400">{path.tagline}</p>
              </div>
            </div>
            {!compact && (
              <dl className="mt-3 grid gap-1 border-t border-zinc-800/80 pt-3 text-[10px] text-zinc-500">
                <div className="flex justify-between gap-2">
                  <dt>Memory</dt>
                  <dd className="text-right text-zinc-400">{path.topology.memory}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Compute</dt>
                  <dd className="text-right text-zinc-400">{path.topology.compute}</dd>
                </div>
              </dl>
            )}
            {active && (
              <span className="absolute right-3 top-3 rounded-full bg-violet-600 px-2 py-0.5 text-[10px] font-medium text-white">
                Selected
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
