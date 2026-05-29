'use client';

const VERIFICATION_LABELS: Record<string, string> = {
  IDENTITY: 'Public identity',
  LINKEDIN: 'LinkedIn verified',
  GITHUB: 'GitHub active',
  KYC: 'KYC documented',
  AUDIT: 'Audit published',
  TEAM_DOXXED: 'Public founder presence',
};

interface FounderBadgesProps {
  verifications: string[];
  compact?: boolean;
}

export function FounderBadges({ verifications, compact }: FounderBadgesProps) {
  if (!verifications.length) return null;

  return (
    <div className={`flex flex-wrap gap-1.5 ${compact ? '' : 'mt-2'}`}>
      {verifications.map((type) => (
        <span
          key={type}
          className="rounded-full border border-emerald-500/30 bg-emerald-950/30 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300"
        >
          {VERIFICATION_LABELS[type] ?? type.replace(/_/g, ' ')}
        </span>
      ))}
    </div>
  );
}
