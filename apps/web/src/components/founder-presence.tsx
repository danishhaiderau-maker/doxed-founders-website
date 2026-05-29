'use client';

import { PRESENCE_LEVEL_META, type FounderPresenceLevel } from '@dcf/utils';

export function FounderPresenceBadge({
  level,
  compact,
}: {
  level: FounderPresenceLevel | string;
  compact?: boolean;
}) {
  const meta = PRESENCE_LEVEL_META[level as FounderPresenceLevel] ?? PRESENCE_LEVEL_META.UNVERIFIED;

  const colorClass =
    meta.color === 'amber'
      ? 'border-amber-500/40 bg-amber-950/30 text-amber-200'
      : meta.color === 'emerald'
        ? 'border-emerald-500/40 bg-emerald-950/30 text-emerald-200'
        : meta.color === 'sky'
          ? 'border-sky-500/40 bg-sky-950/30 text-sky-200'
          : meta.color === 'violet'
            ? 'border-violet-500/40 bg-violet-950/30 text-violet-200'
            : 'border-zinc-600 bg-zinc-900/50 text-zinc-400';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-medium ${colorClass} ${
        compact ? 'text-[10px]' : 'text-xs'
      }`}
      title={meta.description}
    >
      <span>{meta.emoji}</span>
      {meta.label}
    </span>
  );
}

export function TrustRing({
  score,
  breakdown,
  size = 'md',
}: {
  score: number;
  breakdown?: {
    videoActivity: number;
    githubActivity: number;
    communityTrust: number;
    productDelivery: number;
    consistency: number;
  };
  size?: 'sm' | 'md' | 'lg';
}) {
  const dim = size === 'lg' ? 'h-36 w-36' : size === 'sm' ? 'h-20 w-20' : 'h-28 w-28';
  const text = size === 'lg' ? 'text-4xl' : size === 'sm' ? 'text-xl' : 'text-3xl';
  const color =
    score >= 80 ? 'border-emerald-500/50 text-emerald-400' : score >= 50 ? 'border-amber-500/50 text-amber-400' : 'border-red-500/40 text-red-400';

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
      <div
        className={`relative flex ${dim} shrink-0 items-center justify-center rounded-full border-4 bg-zinc-950 ${color}`}
      >
        <div className="text-center">
          <p className={`font-bold ${text}`}>{score}</p>
          <p className="text-[10px] uppercase tracking-wider text-zinc-500">Trust</p>
        </div>
      </div>
      {breakdown && (
        <dl className="flex-1 space-y-1.5 text-sm">
          {(
            [
              ['Video activity', breakdown.videoActivity],
              ['GitHub activity', breakdown.githubActivity],
              ['Community trust', breakdown.communityTrust],
              ['Product delivery', breakdown.productDelivery],
              ['Consistency', breakdown.consistency],
            ] as const
          ).map(([label, val]) => (
            <div key={label} className="flex justify-between gap-4 border-b border-zinc-800 py-1.5">
              <dt className="text-zinc-500">{label}</dt>
              <dd className="font-medium text-white">{val}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function BuildHeatmap({ cells }: { cells: { date: string; count: number }[] }) {
  const max = Math.max(1, ...cells.map((c) => c.count));
  return (
    <div className="flex flex-wrap gap-1">
      {cells.slice(-84).map((c) => (
        <div
          key={c.date}
          title={`${c.date}: ${c.count} update(s)`}
          className="h-3 w-3 rounded-sm"
          style={{
            backgroundColor:
              c.count === 0
                ? 'rgb(39 39 42)'
                : `rgba(16, 185, 129, ${0.25 + (c.count / max) * 0.75})`,
          }}
        />
      ))}
    </div>
  );
}
