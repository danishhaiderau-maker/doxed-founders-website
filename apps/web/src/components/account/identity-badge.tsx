'use client';

type Props = {
  badge: string | null;
  xVerified?: boolean;
  hasTwitterConnected?: boolean;
};

export function IdentityBadge({ badge, xVerified, hasTwitterConnected }: Props) {
  if (!badge && !hasTwitterConnected) return null;

  const label = badge ?? (hasTwitterConnected ? 'X account holder' : null);
  if (!label) return null;

  const tone = xVerified
    ? 'border-cyan-500/40 bg-cyan-950/40 text-cyan-200'
    : hasTwitterConnected
      ? 'border-emerald-500/35 bg-emerald-950/30 text-emerald-200'
      : 'border-zinc-700 bg-zinc-900/60 text-zinc-400';

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone}`}
    >
      {xVerified ? (
        <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
        </svg>
      ) : null}
      {label}
    </span>
  );
}
