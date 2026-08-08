'use client';

import Link from 'next/link';

type Props = {
  userId: string;
  label?: string;
  className?: string;
  compact?: boolean;
};

export function MessageTraderButton({ userId, label, className = '', compact = false }: Props) {
  const q = new URLSearchParams({ dm: userId });
  if (label) q.set('label', label);
  return (
    <Link
      href={`/chat?${q.toString()}`}
      className={
        className ||
        `inline-flex items-center justify-center rounded-lg border border-cyan-500/40 bg-cyan-950/30 font-medium text-cyan-100 transition hover:bg-cyan-950/50 ${
          compact ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm'
        }`
      }
    >
      Message
    </Link>
  );
}
