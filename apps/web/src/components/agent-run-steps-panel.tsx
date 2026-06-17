'use client';

import type { FounderAgentRunRecord } from '@/lib/api';
import type { AgentRuntimeStep } from '@dcf/utils';

type Props = {
  run: FounderAgentRunRecord;
};

export function AgentRunStepsPanel({ run }: Props) {
  const steps: AgentRuntimeStep[] = run.steps ?? [];
  const title = run.adapterLabel ?? 'Builder Agent';
  const activeStep = steps.find((s) => s.active);
  const doneCount = steps.filter((s) => s.done).length;

  if (steps.length === 0) {
    return (
      <div className="rounded-xl border border-violet-500/25 bg-violet-950/15 px-4 py-3">
        <p className="text-sm font-semibold text-violet-100">{title}</p>
        <p className="mt-1 text-xs text-zinc-400">{run.status} · {run.task.slice(0, 120)}</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-950/25 to-zinc-950/80 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300">{title}</p>
          <p className="mt-0.5 text-sm font-medium text-white">
            {activeStep ? `${activeStep.index}/${activeStep.total} ${activeStep.label}` : run.status}
          </p>
          <p className="mt-1 line-clamp-2 text-[11px] text-zinc-500">{run.task}</p>
        </div>
        <span className="rounded-full border border-violet-500/30 bg-violet-950/50 px-2 py-0.5 text-[10px] text-violet-200">
          {doneCount}/{steps.length} steps
        </span>
      </div>

      <ol className="mt-3 space-y-1.5">
        {steps.map((step) => (
          <li
            key={step.index}
            className={`flex items-start gap-2 rounded-lg px-2 py-1 text-[11px] ${
              step.active
                ? 'bg-violet-600/20 text-violet-100'
                : step.done
                  ? 'text-emerald-300/90'
                  : 'text-zinc-600'
            }`}
          >
            <span className="mt-0.5 w-4 shrink-0 font-mono text-[10px]">
              {step.done ? '✓' : step.active ? '→' : '○'}
            </span>
            <span>
              <span className="font-medium">
                {step.index}/{step.total}
              </span>{' '}
              {step.label}
              {step.detail ? <span className="block text-[10px] opacity-80">{step.detail}</span> : null}
            </span>
          </li>
        ))}
      </ol>

      {run.repository && (
        <p className="mt-2 text-[10px] text-zinc-600">
          Repo: <span className="text-zinc-400">{run.repository}</span>
        </p>
      )}
    </div>
  );
}
