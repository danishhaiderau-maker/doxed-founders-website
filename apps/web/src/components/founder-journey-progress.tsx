'use client';

import { LIFECYCLE_STAGES, computeJourneyProgress } from '@dcf/utils';

export function FounderJourneyProgress({
  currentStage,
  label = 'Your journey',
}: {
  currentStage: string;
  label?: string;
}) {
  const progress = computeJourneyProgress(currentStage);
  const current = LIFECYCLE_STAGES.find((s) => s.key === currentStage) ?? LIFECYCLE_STAGES[0];
  const currentIdx = LIFECYCLE_STAGES.findIndex((s) => s.key === currentStage);

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">{label}</p>
          <p className="mt-2 text-2xl font-bold text-white">
            {current.emoji} {current.label}
          </p>
          <p className="mt-1 text-sm text-zinc-400">Progress through the founder-to-market pipeline</p>
        </div>
        <div className="text-right">
          <p className="text-3xl font-bold text-emerald-400">{progress}%</p>
          <p className="text-xs text-zinc-500">complete</p>
        </div>
      </div>

      <div className="mt-5 h-2 overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-blue-600 via-emerald-500 to-purple-500 transition-all duration-700"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="mt-6 overflow-x-auto pb-1">
        <div className="flex min-w-max gap-1">
          {LIFECYCLE_STAGES.map((stage, idx) => {
            const done = idx < currentIdx;
            const active = idx === currentIdx;
            return (
              <div key={stage.key} className="flex items-center">
                <div className="flex w-[72px] flex-col items-center text-center">
                  <div
                    className={`flex h-9 w-9 items-center justify-center rounded-full text-base ${
                      active
                        ? 'bg-emerald-500/30 ring-2 ring-emerald-400/70'
                        : done
                          ? 'bg-zinc-700/80'
                          : 'bg-zinc-900/80 opacity-40'
                    }`}
                  >
                    {stage.emoji}
                  </div>
                  <p className={`mt-1.5 text-[9px] leading-tight ${active ? 'font-semibold text-emerald-200' : 'text-zinc-600'}`}>
                    {stage.label}
                  </p>
                </div>
                {idx < LIFECYCLE_STAGES.length - 1 && (
                  <div className={`mb-5 h-px w-4 ${done ? 'bg-emerald-600/60' : 'bg-zinc-800'}`} />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
