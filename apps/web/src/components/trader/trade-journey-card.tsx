'use client';

import { useState } from 'react';
import { formatPercent, formatTokenPrice, formatUsd } from '@dcf/utils';
import { TradeCloseShareButtons } from '@/components/trade-close-share-buttons';
import { useShareOrigin } from '@/components/share-on-x-button';
import { buildPortfolioShareUrl } from '@dcf/utils';
import type { TradeJourneyCard as Journey } from '@/lib/api';

const STEP_LABELS: Record<string, string> = {
  BUY: 'Buy',
  ADD: 'Add position',
  THESIS_UPDATE: 'Thesis update',
  SELL: 'Sell',
  REDUCE: 'Reduce',
};

type Props = {
  journey: Journey;
  userId: string;
};

export function TradeJourneyCard({ journey, userId }: Props) {
  const [expanded, setExpanded] = useState(false);
  const origin = useShareOrigin();
  const portfolioUrl = buildPortfolioShareUrl(origin, userId);
  const { closed: c, events } = journey;

  return (
    <li className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-background)]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-3 p-4 text-left transition hover:bg-white/[0.02]"
      >
        {journey.logoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={journey.logoUrl} alt="" className="h-10 w-10 rounded-full" />
        ) : (
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-border)] text-xs font-bold">
            {journey.ticker.slice(0, 2)}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">${journey.ticker}</span>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                c.realizedReturnPct >= 0
                  ? 'bg-emerald-950/50 text-emerald-300'
                  : 'bg-red-950/50 text-red-300'
              }`}
            >
              {formatPercent(c.realizedReturnPct)}
            </span>
            {c.exitNarrative === 'regret' && (
              <span className="rounded-full bg-amber-950/50 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                Sold early
              </span>
            )}
            {c.exitNarrative === 'smart' && (
              <span className="rounded-full bg-sky-950/50 px-2 py-0.5 text-[10px] font-semibold text-sky-300">
                Smart exit
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            {events.length} steps · {c.durationDays}d held · tap to {expanded ? 'collapse' : 'expand'}
          </p>
        </div>
      </button>

      <div className="border-t border-[var(--color-border)] px-4 py-4">
        <div className="flex flex-col items-center gap-0">
          {events.map((event, index) => (
            <div key={event.id} className="flex w-full flex-col items-center">
              <JourneyStep event={event} />
              {index < events.length - 1 && (
                <div className="flex flex-col items-center py-1 text-[var(--color-muted)]" aria-hidden>
                  <span className="h-4 w-px bg-[var(--color-border)]" />
                  <span className="text-[10px]">↓</span>
                </div>
              )}
            </div>
          ))}

          {(c.whatIfHeldTotalPct > 0 || c.pumpAfterExitPct > 0 || c.dropAfterExitPct > 0) && (
            <>
              <div className="flex flex-col items-center py-1 text-[var(--color-muted)]" aria-hidden>
                <span className="h-4 w-px bg-[var(--color-border)]" />
                <span className="text-[10px]">↓</span>
              </div>
              <PostExitBlock closed={c} />
            </>
          )}
        </div>

        {expanded && (
          <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--color-border)] pt-4 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-[var(--color-muted)]">Entry</dt>
              <dd className="mt-0.5 font-medium">{formatTokenPrice(c.entryPriceUsd)}</dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted)]">Exit</dt>
              <dd className="mt-0.5 font-medium">{formatTokenPrice(c.exitPriceUsd)}</dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted)]">Duration</dt>
              <dd className="mt-0.5 font-medium">{c.durationDays} days</dd>
            </div>
            <div>
              <dt className="text-[var(--color-muted)]">Net</dt>
              <dd className="mt-0.5 font-medium">{formatUsd(c.proceedsUsd - c.investedUsd)}</dd>
            </div>
          </dl>
        )}

        <TradeCloseShareButtons
          ticker={c.ticker}
          investedUsd={c.investedUsd}
          proceedsUsd={c.proceedsUsd}
          realizedReturnPct={c.realizedReturnPct}
          whatIfHeldReturnPct={c.whatIfHeldTotalPct || c.whatIfHeldPct}
          missedAlphaPct={c.missedAfterExitPct || c.missedAlphaPct}
          portfolioUrl={portfolioUrl}
          postExitPeakPriceUsd={c.postExitPeakPriceUsd}
          exitPriceUsd={c.exitPriceUsd}
          postExitTroughPriceUsd={c.postExitTroughPriceUsd}
          dropAfterExitPct={c.dropAfterExitPct}
          pumpAfterExitPct={c.pumpAfterExitPct}
          exitNarrative={c.exitNarrative}
        />
      </div>
    </li>
  );
}

function JourneyStep({
  event,
}: {
  event: Journey['events'][number];
}) {
  const date = new Date(event.createdAt).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
  const label = STEP_LABELS[event.type] ?? event.type;

  return (
    <div className="w-full max-w-md rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-muted)]">{date}</span>
        <span className="font-semibold uppercase text-white">{label}</span>
        <span className="font-medium text-zinc-300">${event.ticker}</span>
      </div>
      {event.type !== 'THESIS_UPDATE' && (
        <p className="mt-1 text-zinc-300">
          {formatUsd(event.amountUsd, 0)} DDollar
          <span className="ml-2 text-[var(--color-muted)]">@ {formatTokenPrice(event.priceUsd)}</span>
        </p>
      )}
      {event.thesis && (
        <p className="mt-1.5 border-t border-[var(--color-border)] pt-1.5 italic text-emerald-200/90">
          &ldquo;{event.thesis}&rdquo;
        </p>
      )}
      {event.type === 'SELL' && event.realizedReturnPct != null && (
        <p
          className={`mt-1.5 font-semibold ${
            event.realizedReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'
          }`}
        >
          Profit {formatPercent(event.realizedReturnPct)}
        </p>
      )}
    </div>
  );
}

function PostExitBlock({ closed: c }: { closed: Journey['closed'] }) {
  return (
    <div className="w-full max-w-md space-y-2">
      {c.pumpAfterExitPct > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-950/25 px-3 py-2.5 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
            What If I Held?
          </p>
          <p className="mt-1 font-bold text-amber-100">+{c.whatIfHeldTotalPct.toFixed(0)}% total</p>
          <p className="mt-1 text-amber-200/80">
            Pumped +{c.pumpAfterExitPct.toFixed(0)}% after exit to{' '}
            {formatTokenPrice(c.postExitPeakPriceUsd)}
          </p>
          {c.missedAfterExitPct > 0 && (
            <p className="mt-1 text-amber-300">Missed alpha +{c.missedAfterExitPct.toFixed(0)}%</p>
          )}
        </div>
      )}
      {c.dropAfterExitPct > 0 && c.exitNarrative === 'smart' && (
        <div className="rounded-lg border border-sky-500/30 bg-sky-950/25 px-3 py-2.5 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-sky-400">
            After your exit
          </p>
          <p className="mt-1 font-bold text-sky-100">
            Price fell {c.dropAfterExitPct.toFixed(0)}% to{' '}
            {formatTokenPrice(c.postExitTroughPriceUsd)}
          </p>
          <p className="mt-1 text-sky-200/80">You sold near the top.</p>
        </div>
      )}
    </div>
  );
}
