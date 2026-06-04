'use client';

import Link from 'next/link';

type Props = {
  userId: string;
  className?: string;
  compact?: boolean;
};

export function MessageTraderButton({ userId, className = '', compact = false }: Props) {
  return (
    <Link
      href={`/account?tab=messages&with=${encodeURIComponent(userId)}`}
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
