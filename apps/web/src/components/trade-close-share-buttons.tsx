'use client';

import { ShareOnXButton } from '@/components/share-on-x-button';
import {
  buildWhatIfIHeldShareText,
  buildShareWinText,
  buildRegretShareText,
  buildSmartExitShareText,
  postExitOutcomeLabel,
} from '@dcf/utils';

type Props = {
  ticker: string;
  investedUsd: number;
  proceedsUsd: number;
  realizedReturnPct: number;
  whatIfHeldReturnPct: number;
  missedAlphaPct: number;
  portfolioUrl?: string;
  postExitPeakPriceUsd?: number;
  exitPriceUsd?: number;
  postExitTroughPriceUsd?: number;
  dropAfterExitPct?: number;
  pumpAfterExitPct?: number;
  currentVsExitPct?: number;
  avoidedLossPct?: number;
  exitNarrative?: 'regret' | 'smart' | 'neutral';
};

export function TradeCloseShareButtons({
  ticker,
  investedUsd,
  proceedsUsd,
  realizedReturnPct,
  whatIfHeldReturnPct,
  missedAlphaPct,
  portfolioUrl,
  postExitPeakPriceUsd,
  exitPriceUsd,
  postExitTroughPriceUsd,
  dropAfterExitPct = 0,
  pumpAfterExitPct = 0,
  currentVsExitPct,
  avoidedLossPct,
  exitNarrative = 'neutral',
}: Props) {
  const missedSinceExit = missedAlphaPct;
  const outcome = postExitOutcomeLabel({
    narrative: exitNarrative,
    missedAfterExitPct: missedSinceExit,
    avoidedLossPct: avoidedLossPct ?? 0,
    currentVsExitPct: currentVsExitPct ?? 0,
  });

  const winText = buildShareWinText({
    ticker,
    investedUsd,
    proceedsUsd,
    realizedReturnPct,
    portfolioUrl,
  });

  const regretText =
    exitPriceUsd != null && missedSinceExit > 0
      ? buildRegretShareText({
          ticker,
          realizedReturnPct,
          missedAfterExitPct: missedSinceExit,
          pumpAfterExitPct: pumpAfterExitPct || missedSinceExit,
          exitPriceUsd,
          postExitPeakPriceUsd: postExitPeakPriceUsd ?? exitPriceUsd,
          portfolioUrl,
        })
      : buildWhatIfIHeldShareText({
          ticker,
          investedUsd,
          proceedsUsd,
          realizedReturnPct,
          whatIfHeldReturnPct,
          missedAlphaPct: missedSinceExit,
          portfolioUrl,
        });

  const smartText =
    postExitTroughPriceUsd != null && exitPriceUsd != null && (avoidedLossPct ?? dropAfterExitPct) > 0
      ? buildSmartExitShareText({
          ticker,
          exitPriceUsd,
          postExitTroughPriceUsd,
          avoidedLossPct: avoidedLossPct ?? dropAfterExitPct,
          realizedReturnPct,
          portfolioUrl,
        })
      : null;

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <ShareOnXButton
        text={winText}
        label="Share win"
        className="border-emerald-500/40 bg-emerald-950/40 text-emerald-100"
      />
      {exitNarrative === 'regret' && missedSinceExit > 0 && (
        <ShareOnXButton
          text={regretText}
          label={`${outcome.emoji} ${outcome.title} (+${missedSinceExit.toFixed(0)}%)`}
          className="border-amber-500/40 bg-amber-950/40 text-amber-100"
        />
      )}
      {exitNarrative === 'neutral' && (
        <ShareOnXButton
          text={regretText}
          label={`${outcome.emoji} ${outcome.title}`}
          className="border-zinc-500/40 bg-zinc-900/60 text-zinc-200"
        />
      )}
      {exitNarrative === 'smart' && smartText && (
        <ShareOnXButton
          text={smartText}
          label={`${outcome.emoji} ${outcome.title} (−${(avoidedLossPct ?? dropAfterExitPct).toFixed(0)}%)`}
          className="border-sky-500/40 bg-sky-950/40 text-sky-100"
        />
      )}
    </div>
  );
}
