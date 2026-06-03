'use client';

import type { GamifiedRole } from '@dcf/utils';
import { cn } from '@dcf/utils';

const ROLE_COLORS: Record<string, string> = {
  rose: 'bg-rose-500/20 text-rose-100 ring-rose-500/40',
  violet: 'bg-violet-500/20 text-violet-100 ring-violet-500/40',
  sky: 'bg-sky-500/20 text-sky-100 ring-sky-500/40',
  emerald: 'bg-emerald-500/20 text-emerald-100 ring-emerald-500/40',
  amber: 'bg-amber-500/20 text-amber-100 ring-amber-500/40',
  cyan: 'bg-cyan-500/20 text-cyan-100 ring-cyan-500/40',
  zinc: 'bg-zinc-700/50 text-zinc-200 ring-zinc-600/40',
};

export function GamifiedRoleBadge({
  role,
  className,
  size = 'sm',
}: {
  role: GamifiedRole;
  className?: string;
  size?: 'sm' | 'md';
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full font-medium ring-1',
        ROLE_COLORS[role.color] ?? ROLE_COLORS.zinc,
        size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs',
        className,
      )}
      title={role.description}
    >
      {role.label ?? 'Member'}
    </span>
  );
}

export function BuilderStatusBadge({ badge }: { badge: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-100 ring-1 ring-emerald-500/30">
      {badge}
    </span>
  );
}
