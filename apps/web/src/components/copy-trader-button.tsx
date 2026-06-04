'use client';

import Link from 'next/link';
import { buildPaperTradeDeepLink } from '@dcf/utils';

type Props = {
  userId: string;
  dexscreenerUrl?: string | null;
  amountUsd?: number;
  thesis?: string | null;
  className?: string;
  compact?: boolean;
};

/** Opens Trading Alpha with the same token / size as a feed or profile trade. */
export function CopyTraderButton({
  userId,
  dexscreenerUrl,
  amountUsd,
  thesis,
  className = '',
  compact = false,
}: Props) {
  const href = buildPaperTradeDeepLink({
    dexscreenerUrl,
    amountUsd,
    thesis,
    copyFromUserId: dexscreenerUrl ? undefined : userId,
    side: 'BUY',
  });

  return (
    <Link
      href={href}
      className={
        className ||
        `inline-flex items-center justify-center rounded-lg border border-amber-500/45 bg-amber-950/30 font-medium text-amber-100 transition hover:bg-amber-950/50 ${
          compact ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm'
        }`
      }
      title="Open Trading Alpha with this trade prefilled"
    >
      Copy trade
    </Link>
  );
}
