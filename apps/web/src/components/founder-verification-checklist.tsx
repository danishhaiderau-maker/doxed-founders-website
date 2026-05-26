'use client';

import {
  FOUNDER_VERIFICATION_LABELS,
  FounderVerificationInput,
  scoreFounderVerification,
} from '@dcf/utils';

interface FounderVerificationChecklistProps {
  input: FounderVerificationInput;
}

export function FounderVerificationChecklist({
  input,
}: FounderVerificationChecklistProps) {
  const { score, criteria, meetsThreshold } = scoreFounderVerification(input);

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] p-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium">Doxxed founder verification</p>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            meetsThreshold
              ? 'bg-emerald-950/60 text-[var(--color-success)]'
              : 'bg-amber-950/40 text-amber-300'
          }`}
        >
          {score}/6 · {meetsThreshold ? 'Eligible' : 'Need 2+ criteria'}
        </span>
      </div>
      <p className="mt-2 text-xs text-[var(--color-muted)]">
        Public founders need at least 2 proof points — video interview, LinkedIn,
        name, GitHub, or company details.
      </p>
      <ul className="mt-4 space-y-2">
        {(
          Object.keys(FOUNDER_VERIFICATION_LABELS) as Array<
            keyof typeof FOUNDER_VERIFICATION_LABELS
          >
        ).map((key) => {
          const met = criteria.includes(key);
          return (
            <li
              key={key}
              className="flex items-center gap-2 text-sm text-[var(--color-muted)]"
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-xs ${
                  met
                    ? 'bg-[var(--color-success)]/20 text-[var(--color-success)]'
                    : 'bg-[var(--color-border)] text-[var(--color-muted)]'
                }`}
              >
                {met ? '✓' : '·'}
              </span>
              <span className={met ? 'text-white' : ''}>
                {FOUNDER_VERIFICATION_LABELS[key]}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
