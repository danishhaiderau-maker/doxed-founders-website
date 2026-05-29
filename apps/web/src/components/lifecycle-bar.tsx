'use client';

import { LIFECYCLE_STAGES } from '@dcf/utils';

export function ProjectLifecycleBar({ currentStage }: { currentStage: string }) {
  const currentIdx = LIFECYCLE_STAGES.findIndex((s) => s.key === currentStage);

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">Project lifecycle</p>
      <div className="mt-4 overflow-x-auto pb-2">
        <div className="flex min-w-max items-start gap-1">
          {LIFECYCLE_STAGES.map((stage, idx) => {
            const active = idx === currentIdx;
            const done = idx < currentIdx;
            return (
              <div key={stage.key} className="flex items-center">
                <div className="flex w-[88px] flex-col items-center text-center">
                  <div
                    className={`flex h-10 w-10 items-center justify-center rounded-full text-lg transition ${
                      active
                        ? 'bg-emerald-500/25 ring-2 ring-emerald-400/60'
                        : done
                          ? 'bg-zinc-800 opacity-80'
                          : 'bg-zinc-900 opacity-50'
                    }`}
                  >
                    {stage.emoji}
                  </div>
                  <p
                    className={`mt-2 text-[10px] leading-tight ${
                      active ? 'font-semibold text-emerald-200' : 'text-zinc-500'
                    }`}
                  >
                    {stage.label}
                  </p>
                </div>
                {idx < LIFECYCLE_STAGES.length - 1 && (
                  <div
                    className={`mx-0.5 mb-6 h-0.5 w-6 shrink-0 ${
                      done ? 'bg-emerald-600/50' : 'bg-zinc-800'
                    }`}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
      {currentIdx >= 0 && (
        <p className="mt-2 text-sm text-zinc-300">
          Current stage:{' '}
          <span className="font-semibold text-emerald-300">
            {LIFECYCLE_STAGES[currentIdx].emoji} {LIFECYCLE_STAGES[currentIdx].label}
          </span>
        </p>
      )}
    </div>
  );
}
