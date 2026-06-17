'use client';

import Link from 'next/link';
import type { FounderOnboardingStatus } from '@/lib/api';

type Props = {
  status: FounderOnboardingStatus;
  onResumeWizard?: () => void;
};

export function FounderSetupRail({ status, onResumeWizard }: Props) {
  const required = status.steps.filter((s) => !s.optional);
  const done = required.filter((s) => s.complete).length;
  const pct = required.length ? Math.round((done / required.length) * 100) : 0;

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-white">Setup · {pct}% complete</p>
            {status.pathLabel && (
              <span className="rounded-full border border-violet-500/30 bg-violet-950/40 px-2 py-0.5 text-[10px] text-violet-200">
                {status.pathLabel}
              </span>
            )}
          </div>
          {status.topology && (
            <p className="mt-1 text-[11px] text-zinc-500">
              Memory · {status.topology.memory} · Compute · {status.topology.compute}
            </p>
          )}
        </div>
        {onResumeWizard && (
          <button
            type="button"
            onClick={onResumeWizard}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:text-white"
          >
            Continue setup
          </button>
        )}
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-violet-600 to-cyan-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      {!status.requiredComplete && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {status.steps
            .filter((s) => !s.complete && !s.optional)
            .slice(0, 4)
            .map((step) => (
              <li key={step.id}>
                {step.href ? (
                  <Link
                    href={step.href}
                    className="rounded-full border border-zinc-700 px-2.5 py-0.5 text-[10px] text-zinc-400 hover:border-zinc-500 hover:text-white"
                  >
                    {step.label}
                  </Link>
                ) : (
                  <span className="rounded-full border border-zinc-800 px-2.5 py-0.5 text-[10px] text-zinc-500">
                    {step.label}
                  </span>
                )}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
