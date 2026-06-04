'use client';

import type { FeedTerminalCard } from '@/lib/api';
import { feedCardKindLabel } from './feed-terminal-tabs';

export function FeedLiveTape({ cards }: { cards: FeedTerminalCard[] }) {
  if (cards.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/80">
      <div className="border-b border-zinc-800 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
          Live trading tape
        </h3>
      </div>
      <div className="max-h-48 overflow-y-auto font-mono text-[11px]">
        {cards.map((c) => (
          <div
            key={c.id}
            className="flex items-center gap-3 border-b border-zinc-900/80 px-4 py-1.5 last:border-0"
          >
            <span
              className={`w-14 shrink-0 font-bold ${
                ['BUY', 'ADD', 'THESIS', 'HOT_BUY'].includes(c.kind)
                  ? 'text-emerald-400'
                  : 'text-orange-400'
              }`}
            >
              {feedCardKindLabel(c.kind)}
            </span>
            <span className="truncate text-zinc-300">
              {c.traderName ?? 'Trader'} · {c.projectTicker ?? '—'}
              {c.amountUsd != null ? ` · $${Math.round(c.amountUsd).toLocaleString()}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
