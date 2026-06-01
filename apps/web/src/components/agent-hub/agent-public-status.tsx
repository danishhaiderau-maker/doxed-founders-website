'use client';

import type { PublicAgentStatus } from '@/lib/api';

const STATUS_STYLES: Record<
  PublicAgentStatus,
  { border: string; bg: string; text: string; dot: string }
> = {
  online: {
    border: 'border-emerald-500/40',
    bg: 'bg-emerald-950/25',
    text: 'text-emerald-200',
    dot: 'bg-emerald-400',
  },
  offline: {
    border: 'border-zinc-600/40',
    bg: 'bg-zinc-900/40',
    text: 'text-zinc-300',
    dot: 'bg-zinc-500',
  },
  updating: {
    border: 'border-amber-500/35',
    bg: 'bg-amber-950/20',
    text: 'text-amber-200',
    dot: 'bg-amber-400 animate-pulse',
  },
};

export function AgentPublicStatusBanner({
  status,
  label,
  compact,
}: {
  status: PublicAgentStatus;
  label: string;
  compact?: boolean;
}) {
  const s = STATUS_STYLES[status];
  return (
    <div className={`rounded-2xl border px-5 py-3 ${s.border} ${s.bg}`}>
      <p className={`flex items-center gap-2 text-sm font-semibold ${s.text}`}>
        <span className={`inline-block h-2 w-2 rounded-full ${s.dot}`} aria-hidden />
        {label}
      </p>
      {!compact && status === 'online' && (
        <p className="mt-1 text-xs text-emerald-100/70">
          Live trading agent — follow for alerts when positions open or close.
        </p>
      )}
      {!compact && status === 'offline' && (
        <p className="mt-1 text-xs text-zinc-500">
          Demo data shown until the platform agent is back online.
        </p>
      )}
      {!compact && status === 'updating' && (
        <p className="mt-1 text-xs text-amber-100/70">
          The agent is paused or updating. Check back shortly.
        </p>
      )}
    </div>
  );
}

export function AgentPublicStatusInline({
  status,
  label,
}: {
  status: PublicAgentStatus;
  label: string;
}) {
  const s = STATUS_STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${s.text}`}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {label}
    </span>
  );
}
