'use client';

import Link from 'next/link';
import { formatPercent, formatTokenPrice, formatUsd } from '@dcf/utils';
import type { TradingTimelineEvent, TradingTimelineEventType } from '@/lib/api';

const EVENT_LABELS: Record<TradingTimelineEventType, string> = {
  BUY: 'Buy',
  SELL: 'Sell',
  ADD: 'Add position',
  REDUCE: 'Reduce',
  THESIS_UPDATE: 'Thesis update',
  MILESTONE: 'Milestone',
};

const EVENT_COLORS: Record<TradingTimelineEventType, string> = {
  BUY: 'border-emerald-500/50 bg-emerald-950/30 text-emerald-300',
  SELL: 'border-red-500/40 bg-red-950/25 text-red-300',
  ADD: 'border-sky-500/40 bg-sky-950/25 text-sky-300',
  REDUCE: 'border-orange-500/40 bg-orange-950/25 text-orange-300',
  THESIS_UPDATE: 'border-violet-500/40 bg-violet-950/25 text-violet-300',
  MILESTONE: 'border-amber-500/40 bg-amber-950/25 text-amber-300',
};

type Props = {
  events: TradingTimelineEvent[];
  journeyDays: number;
  hasOlderHistory: boolean;
  olderTradeCount: number;
  showOlder: boolean;
  onShowOlder: () => void;
  loadingOlder?: boolean;
};

export function TradingTimeline({
  events,
  journeyDays,
  hasOlderHistory,
  olderTradeCount,
  showOlder,
  onShowOlder,
  loadingOlder,
}: Props) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-semibold">Trading timeline</h3>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            {showOlder
              ? 'Full trade history — how this trader thinks, not just what they made'
              : `Last ${journeyDays} days — buys, adds, thesis updates, and exits`}
          </p>
        </div>
      </div>

      {events.length === 0 ? (
        <p className="mt-6 text-sm text-[var(--color-muted)]">
          No trades in this window yet. Rankings come alive once traders record decisions here.
        </p>
      ) : (
        <ol className="relative mt-6 space-y-0 pl-1">
          {events.map((event, index) => (
            <TimelineNode key={event.id} event={event} isLast={index === events.length - 1} />
          ))}
        </ol>
      )}

      {hasOlderHistory && !showOlder && (
        <button
          type="button"
          onClick={onShowOlder}
          disabled={loadingOlder}
          className="mt-6 w-full rounded-lg border border-dashed border-[var(--color-border)] py-3 text-sm text-[var(--color-muted)] transition hover:border-[var(--color-accent)]/40 hover:text-white disabled:opacity-50"
        >
          {loadingOlder
            ? 'Loading older history…'
            : `Show older history (${olderTradeCount} earlier trade${olderTradeCount === 1 ? '' : 's'})`}
        </button>
      )}
    </section>
  );
}

function TimelineNode({ event, isLast }: { event: TradingTimelineEvent; isLast: boolean }) {
  const date = new Date(event.createdAt).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
  const label = EVENT_LABELS[event.type];
  const color = EVENT_COLORS[event.type];

  return (
    <li className="relative flex gap-4 pb-6">
      {!isLast && (
        <span
          className="absolute left-[11px] top-6 bottom-0 w-px bg-[var(--color-border)]"
          aria-hidden
        />
      )}
      <span
        className={`relative z-10 mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border ${color}`}
        aria-hidden
      >
        <span className="h-2 w-2 rounded-full bg-current opacity-80" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <time className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-muted)]">
            {date}
          </time>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${color}`}>
            {label}
          </span>
          <span className="font-semibold">${event.ticker}</span>
        </div>

        <div className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2.5 text-xs">
          {event.type !== 'THESIS_UPDATE' && (
            <p className="font-medium text-white">
              {formatUsd(event.amountUsd, 0)} DDollar
              <span className="ml-2 font-normal text-[var(--color-muted)]">
                @ {formatTokenPrice(event.priceUsd)}
              </span>
            </p>
          )}

          {event.type === 'SELL' || event.type === 'REDUCE' ? (
            <div className="mt-2 space-y-1 text-[var(--color-muted)]">
              {event.realizedReturnPct != null && (
                <p>
                  Result:{' '}
                  <span
                    className={
                      event.realizedReturnPct >= 0
                        ? 'font-medium text-[var(--color-success)]'
                        : 'font-medium text-[var(--color-danger)]'
                    }
                  >
                    {formatPercent(event.realizedReturnPct)}
                  </span>
                  {event.realizedPnlUsd != null && (
                    <span className="ml-1">({formatUsd(event.realizedPnlUsd)} net)</span>
                  )}
                </p>
              )}
              {event.whatIfHeldPct != null && event.whatIfHeldPct > 0 && (
                <p className="text-amber-300/90">
                  What If I Held? +{event.whatIfHeldTotalPct?.toFixed(0) ?? event.whatIfHeldPct.toFixed(0)}%
                  {event.missedAlphaPct != null && event.missedAlphaPct > 0 && (
                    <span className="ml-2">· Missed +{event.missedAfterExitPct?.toFixed(0) ?? event.missedAlphaPct.toFixed(0)}%</span>
                  )}
                </p>
              )}
              {event.pumpAfterExitPct != null && event.pumpAfterExitPct > 0 && (
                <p className="text-amber-200/90">
                  After exit: pumped +{event.pumpAfterExitPct.toFixed(0)}% to{' '}
                  {formatTokenPrice(event.postExitPeakPriceUsd ?? event.priceUsd)}
                </p>
              )}
              {event.exitNarrative === 'smart' && event.dropAfterExitPct != null && event.dropAfterExitPct > 0 && (
                <p className="text-sky-300/90">
                  Smart exit: fell {event.dropAfterExitPct.toFixed(0)}% after you sold
                </p>
              )}
            </div>
          ) : null}

          {event.thesis && (
            <div className="mt-2 border-t border-[var(--color-border)] pt-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400/70">
                {event.type === 'THESIS_UPDATE' ? 'Updated thesis' : 'Reason'}
              </p>
              <p className="mt-1 leading-relaxed text-zinc-300">&ldquo;{event.thesis}&rdquo;</p>
              {event.feedPostId && (
                <Link
                  href={`/feed?post=${event.feedPostId}`}
                  className="mt-2 inline-block text-[11px] text-[var(--color-accent)] hover:underline"
                >
                  View on feed →
                </Link>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
