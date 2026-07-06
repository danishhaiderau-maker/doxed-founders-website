'use client';

import Link from 'next/link';
import { cn } from '@dcf/utils';

export type ComplianceTimelineStep = {
  key: string;
  label: string;
  status: 'pending' | 'active' | 'complete' | 'blocked';
  date: string | null;
  blockerReason: string | null;
  remediationLink: string | null;
};

export type ComplianceTimelineData = {
  enabled: boolean;
  slug: string;
  launchStage: string;
  launchQualification: { score: number; tier: string; passes: boolean };
  regulatoryClass: string;
  steps: ComplianceTimelineStep[];
  progressiveUnlock?: {
    nextStage: string | null;
    nextHint: string | null;
  };
};

const STATUS_STYLES: Record<ComplianceTimelineStep['status'], string> = {
  complete: 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200',
  active: 'border-amber-500/40 bg-amber-950/20 text-amber-100',
  blocked: 'border-red-500/40 bg-red-950/20 text-red-200',
  pending: 'border-zinc-700 bg-zinc-900/30 text-zinc-400',
};

export function ComplianceTimeline({ data }: { data: ComplianceTimelineData | null }) {
  if (!data?.enabled) {
    return (
      <p className="text-sm text-zinc-500">
        Compliance timeline activates when Phase 1.5 Trust Layer is enabled.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-400">
        <span>
          Launch score:{' '}
          <strong className="text-white">{data.launchQualification.score}/100</strong> ({data.launchQualification.tier})
        </span>
        <span>Regulatory: {data.regulatoryClass.replace(/_/g, ' ')}</span>
        <span>Stage: {data.launchStage.replace(/_/g, ' ')}</span>
      </div>

      {data.progressiveUnlock?.nextHint && (
        <p className="rounded-lg border border-violet-500/30 bg-violet-950/15 px-3 py-2 text-xs text-violet-200">
          What comes next: {data.progressiveUnlock.nextHint}
        </p>
      )}

      <ol className="relative space-y-3 border-l border-zinc-800 pl-4">
        {data.steps.map((step) => (
          <li key={step.key} className={cn('rounded-lg border px-3 py-2', STATUS_STYLES[step.status])}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium">{step.label}</span>
              <span className="text-[10px] uppercase tracking-wide">{step.status}</span>
            </div>
            {step.date && (
              <p className="mt-1 text-[11px] opacity-70">{new Date(step.date).toLocaleDateString()}</p>
            )}
            {step.blockerReason && (
              <p className="mt-1 text-xs">{step.blockerReason}</p>
            )}
            {step.remediationLink && step.status !== 'complete' && (
              <Link href={step.remediationLink} className="mt-1 inline-block text-xs underline opacity-80">
                Remediation
              </Link>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
