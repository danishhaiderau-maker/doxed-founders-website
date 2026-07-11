'use client';

import { cn } from '@dcf/utils';
import {
  DEPLOYMENT_MODES,
  getDeploymentModeMeta,
  type DeploymentModeId,
} from '@dcf/utils';

/**
 * Persistent dashboard header badge showing the project's current deployment
 * mode. Clickable — opens the mode panel. Matches doc §3 wireframe:
 *   gray/blue  = Private
 *   green      = Public
 *   purple     = Hybrid (with a "→ publish" hint when unpublished)
 */
export function DeploymentModeBadge({
  mode,
  published = false,
  onClick,
  size = 'md',
}: {
  mode: DeploymentModeId;
  /** True if a completed publish exists (hides the "→ publish" hint). */
  published?: boolean;
  onClick?: () => void;
  size?: 'sm' | 'md';
}) {
  const meta = getDeploymentModeMeta(mode);
  const accentClasses: Record<typeof meta.accent, string> = {
    slate: 'border-slate-500/40 bg-slate-900/40 text-slate-200 hover:border-slate-400/60',
    emerald:
      'border-emerald-500/40 bg-emerald-950/30 text-emerald-200 hover:border-emerald-400/60',
    violet:
      'border-violet-500/40 bg-violet-950/30 text-violet-200 hover:border-violet-400/60',
  };

  const showPublishHint = mode === 'HYBRID' && !published;

  return (
    <button
      type="button"
      onClick={onClick}
      title={`Deployment mode: ${meta.label}. Click to view or switch.`}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition',
        accentClasses[meta.accent],
        size === 'sm' && 'px-2 py-0.5 text-[11px]',
      )}
    >
      <span aria-hidden>{meta.emoji}</span>
      <span>{meta.label}</span>
      {showPublishHint && (
        <span className="text-violet-300/80">→ publish</span>
      )}
    </button>
  );
}

export { DEPLOYMENT_MODES };
