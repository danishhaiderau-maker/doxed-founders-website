import {
  cn,
  getProjectMaturityLabel,
  mapLifecycleToMaturity,
  type ProjectMaturity,
} from '@dcf/utils';

const MATURITY_COLORS: Record<ProjectMaturity, string> = {
  IDEA: 'border-zinc-600/40 bg-zinc-800/40 text-zinc-400',
  BUILDING: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
  VALIDATED: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  COMMUNITY: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  READY: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  LAUNCHING: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  TRADING: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300',
  GROWING: 'border-purple-500/30 bg-purple-500/10 text-purple-300',
};

type ProjectMaturityBadgeProps = {
  maturity?: ProjectMaturity;
  lifecycleStage?: string;
  isLiveToken?: boolean;
  hasActiveRaise?: boolean;
  size?: 'sm' | 'md';
  className?: string;
};

export function ProjectMaturityBadge({
  maturity,
  lifecycleStage,
  isLiveToken,
  hasActiveRaise,
  size = 'sm',
  className,
}: ProjectMaturityBadgeProps) {
  const stage =
    maturity ??
    (lifecycleStage
      ? mapLifecycleToMaturity(lifecycleStage, { isLiveToken, hasActiveRaise })
      : 'IDEA');

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border font-semibold uppercase tracking-wider',
        size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-3 py-1 text-xs',
        MATURITY_COLORS[stage],
        className,
      )}
      title={getProjectMaturityLabel(stage)}
    >
      {getProjectMaturityLabel(stage)}
    </span>
  );
}
