'use client';

import Link from 'next/link';
import type { PlatformPulseItem } from '@/lib/api';

type Props = {
  pulse: PlatformPulseItem[];
};

export function FeedHubPulseStrip({ pulse }: Props) {
  if (!pulse.length) return null;

  return (
    <div className="mt-4 flex gap-3 overflow-x-auto pb-1">
      {pulse.slice(0, 6).map((p) => (
        <Link
          key={p.id}
          href={p.link ?? '/feed'}
          className="min-w-[10rem] shrink-0 rounded-xl border border-zinc-800/80 bg-zinc-900/50 px-3 py-2 transition hover:border-amber-500/30"
        >
          <p className="text-lg leading-none">{p.emoji}</p>
          <p className="mt-1 line-clamp-2 text-[11px] font-medium text-zinc-200">{p.headline}</p>
          {p.detail && (
            <p className="mt-0.5 line-clamp-1 text-[10px] text-zinc-500">{p.detail}</p>
          )}
        </Link>
      ))}
    </div>
  );
}
