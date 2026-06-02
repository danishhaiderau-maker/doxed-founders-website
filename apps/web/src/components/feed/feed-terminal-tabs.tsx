'use client';

import Link from 'next/link';
import {
  FEED_TERMINAL_TABS,
  feedCardKindAccent,
  feedCardKindLabel,
  formatUsd,
  formatTokenPrice,
  type FeedTerminalTab,
} from '@dcf/utils';
import type { FeedTerminalTab as TabId } from '@/lib/api';

export function FeedTerminalTabs({
  active,
  onChange,
}: {
  active: TabId;
  onChange: (tab: TabId) => void;
}) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
      {FEED_TERMINAL_TABS.map((t) => {
        const selected = active === t.id;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onChange(t.id as FeedTerminalTab)}
            className={`flex min-w-[100px] shrink-0 flex-col rounded-xl border px-3 py-2 text-left transition ${
              selected
                ? 'border-violet-500/50 bg-violet-950/30'
                : 'border-zinc-800 bg-zinc-950/50 hover:border-zinc-700'
            }`}
          >
            <span className="text-base">{t.icon}</span>
            <span className={`text-xs font-semibold ${selected ? 'text-white' : 'text-zinc-300'}`}>
              {t.label}
            </span>
            <span className="text-[10px] text-zinc-500">{t.subtitle}</span>
          </button>
        );
      })}
    </div>
  );
}

export function feedCardAccentClasses(accent: string) {
  switch (accent) {
    case 'emerald':
      return 'border-emerald-500/30 bg-emerald-950/10';
    case 'orange':
      return 'border-orange-500/30 bg-orange-950/10';
    case 'red':
      return 'border-red-500/30 bg-red-950/10';
    case 'violet':
      return 'border-violet-500/30 bg-violet-950/10';
    case 'sky':
      return 'border-sky-500/30 bg-sky-950/10';
    default:
      return 'border-zinc-800 bg-zinc-950/40';
  }
}

export function feedKindBadgeClasses(accent: string) {
  switch (accent) {
    case 'emerald':
      return 'bg-emerald-500/20 text-emerald-300';
    case 'orange':
      return 'bg-orange-500/20 text-orange-300';
    case 'red':
      return 'bg-red-500/20 text-red-300';
    case 'violet':
      return 'bg-violet-500/20 text-violet-300';
    case 'sky':
      return 'bg-sky-500/20 text-sky-300';
    default:
      return 'bg-zinc-800 text-zinc-300';
  }
}

export { feedCardKindLabel, feedCardKindAccent, formatUsd, formatTokenPrice, Link };
