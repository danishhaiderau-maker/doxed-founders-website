import { cn, getLaunchQualificationTier, type LaunchQualificationTier } from '@dcf/utils';

const TIER_COLORS: Record<LaunchQualificationTier, string> = {
  ELITE: 'border-fuchsia-500/40 bg-fuchsia-950/20 text-fuchsia-200',
  STRONG: 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200',
  MINIMUM: 'border-amber-500/40 bg-amber-950/20 text-amber-200',
  BELOW: 'border-zinc-600 bg-zinc-800/40 text-zinc-400',
};

type LaunchQualificationBadgeProps = {
  score: number;
  tier?: LaunchQualificationTier;
  className?: string;
};

export function LaunchQualificationBadge({ score, tier, className }: LaunchQualificationBadgeProps) {
  const resolvedTier = tier ?? getLaunchQualificationTier(score);

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
        TIER_COLORS[resolvedTier],
        className,
      )}
      title={`Launch Qualification ${score}/100`}
    >
      LQ {score}
    </span>
  );
}
