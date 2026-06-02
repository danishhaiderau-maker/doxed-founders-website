'use client';

import { ShareOnXButton } from '@/components/share-on-x-button';
import {
  buildRegretShareText,
  buildSmartExitShareText,
  buildTimelineBuyShareText,
  buildTimelineSellShareText,
  buildTimelineThesisShareText,
} from '@dcf/utils';
import type { TradingTimelineEvent } from '@/lib/api';

type Props = {
  event: TradingTimelineEvent;
  portfolioUrl?: string;
};

export function TimelineEventShareButton({ event, portfolioUrl }: Props) {
  const common = { ticker: event.ticker, portfolioUrl };

  if (event.type === 'BUY' || event.type === 'ADD') {
    return (
      <ShareOnXButton
        text={buildTimelineBuyShareText({
          ...common,
          amountUsd: event.amountUsd,
          thesis: event.thesis,
        })}
        label="Share buy"
        className="mt-2 border-emerald-500/40 bg-emerald-950/30 text-emerald-100"
        stopPropagation
      />
    );
  }

  if (event.type === 'THESIS_UPDATE' && event.thesis) {
    return (
      <ShareOnXButton
        text={buildTimelineThesisShareText({
          ...common,
          thesis: event.thesis,
        })}
        label="Share thesis"
        className="mt-2 border-violet-500/40 bg-violet-950/30 text-violet-100"
        stopPropagation
      />
    );
  }

  if (event.type === 'SELL' || event.type === 'REDUCE') {
    const realized = event.realizedReturnPct ?? 0;
    if (
      event.exitNarrative === 'regret' &&
      event.missedAfterExitPct != null &&
      event.missedAfterExitPct > 0
    ) {
      return (
        <ShareOnXButton
          text={buildRegretShareText({
            ticker: event.ticker,
            realizedReturnPct: realized,
            missedAfterExitPct: event.missedAfterExitPct,
            pumpAfterExitPct: event.pumpAfterExitPct ?? event.missedAfterExitPct,
            exitPriceUsd: event.priceUsd,
            postExitPeakPriceUsd: event.postExitPeakPriceUsd ?? event.priceUsd,
            portfolioUrl,
          })}
          label={`Share missed alpha (+${event.missedAfterExitPct.toFixed(0)}%)`}
          className="mt-2 border-amber-500/40 bg-amber-950/30 text-amber-100"
          stopPropagation
        />
      );
    }
    if (
      event.exitNarrative === 'smart' &&
      event.avoidedLossPct != null &&
      event.avoidedLossPct > 0
    ) {
      return (
        <ShareOnXButton
          text={buildSmartExitShareText({
            ticker: event.ticker,
            exitPriceUsd: event.priceUsd,
            postExitTroughPriceUsd: event.postExitTroughPriceUsd ?? event.priceUsd,
            avoidedLossPct: event.avoidedLossPct,
            realizedReturnPct: realized,
            portfolioUrl,
          })}
          label={`Share smart exit (−${event.avoidedLossPct.toFixed(0)}%)`}
          className="mt-2 border-sky-500/40 bg-sky-950/30 text-sky-100"
          stopPropagation
        />
      );
    }
    return (
      <ShareOnXButton
        text={buildTimelineSellShareText({
          ...common,
          amountUsd: event.amountUsd,
          realizedReturnPct: realized,
          thesis: event.thesis,
        })}
        label="Share sell"
        className="mt-2 border-red-500/40 bg-red-950/30 text-red-100"
        stopPropagation
      />
    );
  }

  return null;
}
