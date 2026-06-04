'use client';

import Link from 'next/link';
import type { FeedTerminalCard } from '@/lib/api';
import { feedCardKindLabel } from './feed-terminal-tabs';

const TAPE_KINDS = new Set(['BUY', 'SELL', 'ADD', 'REDUCE', 'THESIS', 'NEW_THESIS', 'HOT_BUY']);

function isBuyKind(kind: string) {
  return ['BUY', 'ADD', 'THESIS', 'NEW_THESIS', 'HOT_BUY'].includes(kind);
}

export function FeedLiveTape({
  cards,
  variant = 'compact',
  loading,
}: {
  cards: FeedTerminalCard[];
  variant?: 'compact' | 'hero';
  loading?: boolean;
}) {
  const isHero = variant === 'hero';
  const rows = cards.filter((c) => TAPE_KINDS.has(c.kind));

  if (isHero) {
    return (
      <section
        className="flex min-h-[50vh] flex-col overflow-hidden rounded-2xl border border-emerald-500/25 bg-gradient-to-b from-zinc-950 via-[#070a12] to-zinc-950 shadow-[0_0_40px_rgba(16,185,129,0.08)]"
        aria-label="Live trading tape"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-emerald-500/20 bg-emerald-950/20 px-4 py-3 sm:px-6">
          <div>
            <div className="flex items-center gap-2">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
              </span>
              <h2 className="text-sm font-bold uppercase tracking-[0.2em] text-emerald-300">
                Live trading tape
              </h2>
            </div>
            <p className="mt-0.5 text-xs text-zinc-500">
              Real-time conviction · buys · sells · thesis — updated every minute
            </p>
          </div>
          <Link
            href="/paper-trading"
            className="hidden shrink-0 rounded-lg border border-emerald-500/40 bg-emerald-600/20 px-3 py-1.5 text-xs font-semibold text-emerald-100 hover:bg-emerald-600/30 sm:inline-block"
          >
            Trade →
          </Link>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto font-mono">
          {loading && rows.length === 0 && (
            <div className="flex h-full min-h-[200px] items-center justify-center text-sm text-zinc-500">
              Loading live activity…
            </div>
          )}
          {!loading && rows.length === 0 && (
            <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-2 px-6 text-center text-sm text-zinc-500">
              <p>No trades on the tape yet.</p>
              <Link href="/paper-trading" className="text-emerald-400 hover:underline">
                Be the first conviction buy →
              </Link>
            </div>
          )}
          {rows.map((c) => (
            <TapeRow key={c.id} card={c} size="hero" />
          ))}
        </div>
      </section>
    );
  }

  if (rows.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/80">
      <div className="border-b border-zinc-800 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
          Live trading tape
        </h3>
      </div>
      <div className="max-h-48 overflow-y-auto font-mono text-[11px]">
        {rows.map((c) => (
          <TapeRow key={c.id} card={c} size="compact" />
        ))}
      </div>
    </div>
  );
}

function TapeRow({ card: c, size }: { card: FeedTerminalCard; size: 'hero' | 'compact' }) {
  const href = c.link ?? (c.projectSlug ? `/project/${c.projectSlug}` : undefined);
  const buy = isBuyKind(c.kind);
  const hero = size === 'hero';

  const row = (
    <div
      className={`flex items-center gap-3 border-b border-zinc-900/80 transition hover:bg-zinc-900/50 ${
        hero ? 'px-4 py-2.5 sm:px-6 sm:py-3' : 'px-4 py-1.5'
      } last:border-0`}
    >
      <span
        className={`shrink-0 font-bold ${buy ? 'text-emerald-400' : 'text-orange-400'} ${
          hero ? 'w-24 text-sm sm:text-base' : 'w-14 text-[11px]'
        }`}
      >
        {feedCardKindLabel(c.kind)}
      </span>
      <span className={`min-w-0 flex-1 truncate text-zinc-200 ${hero ? 'text-sm sm:text-base' : ''}`}>
        <span className="font-semibold text-zinc-100">{c.traderName ?? 'Trader'}</span>
        <span className="text-zinc-500"> · </span>
        <span className="text-violet-300">{c.projectTicker ?? '—'}</span>
        {c.amountUsd != null && (
          <>
            <span className="text-zinc-600"> · </span>
            <span className={buy ? 'text-emerald-300/90' : 'text-orange-300/90'}>
              ${Math.round(c.amountUsd).toLocaleString()}
            </span>
          </>
        )}
      </span>
      {hero && c.projectSlug && (
        <span className="hidden shrink-0 text-[10px] uppercase tracking-wide text-zinc-600 sm:inline">
          View
        </span>
      )}
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block">
        {row}
      </Link>
    );
  }
  return row;
}
