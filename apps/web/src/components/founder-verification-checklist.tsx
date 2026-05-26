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
  const { score, criteria, meetsSubmissionThreshold } =
    scoreFounderVerification(input);

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] p-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium">Public founder proof</p>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            meetsSubmissionThreshold
              ? 'bg-emerald-950/60 text-[var(--color-success)]'
              : 'bg-amber-950/40 text-amber-300'
          }`}
        >
          {meetsSubmissionThreshold ? 'Ready to submit' : 'Need video or interview'}
        </span>
      </div>
      <p className="mt-2 text-xs text-[var(--color-muted)]">
        You do not need to be the founder. If you found a public on-camera video or
        podcast/interview, paste the link — that is enough to submit. LinkedIn, GitHub,
        and audit are optional extras ({score}/6).
      </p>
      <ul className="mt-4 space-y-2">
        {(
          ['FOUNDER_VIDEO', 'PUBLIC_INTERVIEW'] as const
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
                    : 'bg-[var(--color-accent)]/20 text-[var(--color-accent)]'
                }`}
              >
                {met ? '✓' : '!'}
              </span>
              <span className={met ? 'font-medium text-white' : 'text-white'}>
                {FOUNDER_VERIFICATION_LABELS[key]} (required — one of)
              </span>
            </li>
          );
        })}
        {(
          ['FOUNDER_NAME', 'LINKEDIN', 'GITHUB', 'COMPANY_DETAILS'] as const
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
                {FOUNDER_VERIFICATION_LABELS[key]} (optional)
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
