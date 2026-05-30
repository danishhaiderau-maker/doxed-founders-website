'use client';

import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { buildPortfolioShareUrl, buildTraderRankShareMessage } from '@dcf/utils';

type Props = {
  userId: string;
  displayName: string;
  roi: number;
  totalValue: number;
  pnl: number;
  rank?: number;
  isLoser?: boolean;
  isBusted?: boolean;
  compact?: boolean;
};

export function TraderRankShareButton({
  userId,
  displayName,
  roi,
  totalValue,
  pnl,
  rank,
  isLoser,
  isBusted,
  compact,
}: Props) {
  const origin = useShareOrigin();
  const text = buildTraderRankShareMessage({
    displayName,
    roi,
    totalValue,
    pnl,
    rank,
    isLoser,
    isBusted,
  });

  return (
    <ShareOnXButton
      text={text}
      url={buildPortfolioShareUrl(origin, userId)}
      label={compact ? 'Share' : 'Share on X'}
      className={
        compact
          ? 'rounded-md border border-zinc-600 px-2 py-1 text-[11px] text-zinc-300 hover:border-sky-500/50 hover:text-sky-200'
          : undefined
      }
    />
  );
}
