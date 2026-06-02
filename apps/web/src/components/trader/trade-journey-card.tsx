'use client';

import { useState } from 'react';
import { formatPercent, formatTokenPrice, formatUsd, postExitOutcomeLabel } from '@dcf/utils';
import { TradeCloseShareButtons } from '@/components/trade-close-share-buttons';
import { TimelineEventShareButton } from '@/components/trader/timeline-event-share-button';
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
  const outcome = postExitOutcomeLabel({
    narrative: c.exitNarrative,
    missedAfterExitPct: c.missedAfterExitPct,
    avoidedLossPct: c.avoidedLossPct ?? 0,
    currentVsExitPct: c.currentVsExitPct ?? 0,
  });

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
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                c.exitNarrative === 'regret'
                  ? 'bg-amber-950/50 text-amber-300'
                  : c.exitNarrative === 'smart'
                    ? 'bg-sky-950/50 text-sky-300'
                    : 'bg-zinc-800/80 text-zinc-300'
              }`}
            >
              {outcome.emoji} {outcome.title}
            </span>
          </div>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            {outcome.detail} · {events.length} steps · {c.durationDays}d held
          </p>
        </div>
      </button>

      <div className="border-t border-[var(--color-border)] px-4 py-4">
        <div className="flex flex-col items-center gap-0">
          {events.map((event, index) => (
            <div key={event.id} className="flex w-full flex-col items-center">
              <JourneyStep event={event} portfolioUrl={portfolioUrl} />
              {index < events.length - 1 && (
                <div className="flex flex-col items-center py-1 text-[var(--color-muted)]" aria-hidden>
                  <span className="h-4 w-px bg-[var(--color-border)]" />
                  <span className="text-[10px]">↓</span>
                </div>
              )}
            </div>
          ))}

          {(c.pumpAfterExitPct > 0 || c.dropAfterExitPct > 0 || c.missedAfterExitPct > 0 || c.avoidedLossPct > 0) && (
            <>
              <div className="flex flex-col items-center py-1 text-[var(--color-muted)]" aria-hidden>
                <span className="h-4 w-px bg-[var(--color-border)]" />
                <span className="text-[10px]">↓</span>
              </div>
              <PostExitBlock closed={c} outcome={outcome} />
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
          whatIfHeldReturnPct={c.pumpAfterExitPct}
          missedAlphaPct={c.missedAfterExitPct}
          portfolioUrl={portfolioUrl}
          postExitPeakPriceUsd={c.postExitPeakPriceUsd}
          exitPriceUsd={c.exitPriceUsd}
          postExitTroughPriceUsd={c.postExitTroughPriceUsd}
          dropAfterExitPct={c.dropAfterExitPct}
          pumpAfterExitPct={c.pumpAfterExitPct}
          currentVsExitPct={c.currentVsExitPct}
          avoidedLossPct={c.avoidedLossPct}
          exitNarrative={c.exitNarrative}
        />
      </div>
    </li>
  );
}

function JourneyStep({
  event,
  portfolioUrl,
}: {
  event: Journey['events'][number];
  portfolioUrl: string;
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
      <TimelineEventShareButton event={event} portfolioUrl={portfolioUrl} />
    </div>
  );
}

function PostExitBlock({
  closed: c,
  outcome,
}: {
  closed: Journey['closed'];
  outcome: ReturnType<typeof postExitOutcomeLabel>;
}) {
  return (
    <div className="w-full max-w-md space-y-2">
      {c.exitNarrative === 'regret' && c.missedAfterExitPct > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-950/25 px-3 py-2.5 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
            {outcome.emoji} {outcome.title}
          </p>
          <p className="mt-1 font-bold text-amber-100">{outcome.detail}</p>
          {c.pumpAfterExitPct > c.missedAfterExitPct && (
            <p className="mt-1 text-amber-200/80">
              Peak since exit: +{c.pumpAfterExitPct.toFixed(0)}% to{' '}
              {formatTokenPrice(c.postExitPeakPriceUsd)}
            </p>
          )}
        </div>
      )}
      {c.exitNarrative === 'neutral' && (
        <div className="rounded-lg border border-zinc-600/40 bg-zinc-900/40 px-3 py-2.5 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
            {outcome.emoji} {outcome.title}
          </p>
          <p className="mt-1 text-zinc-300">Price is within ~5% of your exit.</p>
        </div>
      )}
      {c.exitNarrative === 'smart' && c.avoidedLossPct > 0 && (
        <div className="rounded-lg border border-sky-500/30 bg-sky-950/25 px-3 py-2.5 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-sky-400">
            {outcome.emoji} {outcome.title}
          </p>
          <p className="mt-1 font-bold text-sky-100">{outcome.detail}</p>
          <p className="mt-1 text-sky-200/80">
            Trough after exit: {formatTokenPrice(c.postExitTroughPriceUsd)} (−
            {c.dropAfterExitPct.toFixed(0)}% from exit)
          </p>
        </div>
      )}
    </div>
  );
}
