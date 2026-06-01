'use client';

import { ShareOnXButton } from '@/components/share-on-x-button';
import {
  buildWhatIfIHeldShareText,
  buildShareWinText,
  buildRegretShareText,
  buildSmartExitShareText,
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
  exitNarrative = 'neutral',
}: Props) {
  const winText = buildShareWinText({
    ticker,
    investedUsd,
    proceedsUsd,
    realizedReturnPct,
    portfolioUrl,
  });
  const whatIfText = buildWhatIfIHeldShareText({
    ticker,
    investedUsd,
    proceedsUsd,
    realizedReturnPct,
    whatIfHeldReturnPct,
    missedAlphaPct,
    portfolioUrl,
  });

  const regretText =
    postExitPeakPriceUsd != null && exitPriceUsd != null && pumpAfterExitPct > 5
      ? buildRegretShareText({
          ticker,
          investedUsd,
          proceedsUsd,
          realizedReturnPct,
          whatIfHeldTotalPct: whatIfHeldReturnPct,
          missedAfterExitPct: missedAlphaPct,
          postExitPeakPriceUsd,
          exitPriceUsd,
          portfolioUrl,
        })
      : null;

  const smartText =
    postExitTroughPriceUsd != null &&
    exitPriceUsd != null &&
    dropAfterExitPct > 5
      ? buildSmartExitShareText({
          ticker,
          exitPriceUsd,
          postExitTroughPriceUsd,
          dropAfterExitPct,
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
      {(missedAlphaPct > 5 || pumpAfterExitPct > 5) && (
        <ShareOnXButton
          text={regretText ?? whatIfText}
          label={
            exitNarrative === 'regret'
              ? `Sold too early (+${whatIfHeldReturnPct.toFixed(0)}%)`
              : `What If I Held? (+${whatIfHeldReturnPct.toFixed(0)}%)`
          }
          className="border-amber-500/40 bg-amber-950/40 text-amber-100"
        />
      )}
      {exitNarrative === 'smart' && smartText && (
        <ShareOnXButton
          text={smartText}
          label={`Smart exit (−${dropAfterExitPct.toFixed(0)}% after)`}
          className="border-sky-500/40 bg-sky-950/40 text-sky-100"
        />
      )}
    </div>
  );
}
