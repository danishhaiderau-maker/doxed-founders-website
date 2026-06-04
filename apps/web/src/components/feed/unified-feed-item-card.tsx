'use client';

import Link from 'next/link';
import type { UnifiedFeedItem } from '@/lib/api';

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const CATEGORY_LABEL: Record<UnifiedFeedItem['category'], string> = {
  founder: 'Founder',
  trading: 'Trading',
  market: 'Market',
  community: 'Community',
};

type Props = {
  item: UnifiedFeedItem;
};

export function UnifiedFeedItemCard({ item }: Props) {
  const href = item.link ?? (item.projectSlug ? `/project/${item.projectSlug}` : '/feed');

  return (
    <article className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 transition hover:border-zinc-700">
      <div className="flex gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-base">
          {item.emoji ?? '📣'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-zinc-400">
              {CATEGORY_LABEL[item.category]}
            </span>
            {item.pinned && (
              <span className="text-[9px] font-medium text-amber-300">Pinned</span>
            )}
            <span className="text-[10px] text-zinc-600">{timeAgo(item.at)}</span>
          </div>
          <Link href={href} className="mt-1 block font-semibold text-white hover:text-amber-100">
            {item.headline}
          </Link>
          {item.detail && (
            <p className="mt-1 text-sm text-zinc-400">{item.detail}</p>
          )}
          {item.traderName && item.amountUsd != null && (
            <p className="mt-1 text-xs text-zinc-500">
              {item.traderName} · ${Math.round(item.amountUsd).toLocaleString()} paper
            </p>
          )}
        </div>
      </div>
    </article>
  );
}
