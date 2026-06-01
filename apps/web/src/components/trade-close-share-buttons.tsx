'use client';

import { ShareOnXButton } from '@/components/share-on-x-button';
import { buildWhatIfIHeldShareText, buildShareWinText } from '@dcf/utils';

type Props = {
  ticker: string;
  investedUsd: number;
  proceedsUsd: number;
  realizedReturnPct: number;
  whatIfHeldReturnPct: number;
  missedAlphaPct: number;
  portfolioUrl?: string;
};

export function TradeCloseShareButtons({
  ticker,
  investedUsd,
  proceedsUsd,
  realizedReturnPct,
  whatIfHeldReturnPct,
  missedAlphaPct,
  portfolioUrl,
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

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <ShareOnXButton
        text={winText}
        label="Share win"
        className="border-emerald-500/40 bg-emerald-950/40 text-emerald-100"
      />
      {missedAlphaPct > 5 && (
        <ShareOnXButton
          text={whatIfText}
          label={`What If I Held? (+${whatIfHeldReturnPct.toFixed(0)}%)`}
          className="border-amber-500/40 bg-amber-950/40 text-amber-100"
        />
      )}
    </div>
  );
}
