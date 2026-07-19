export type LiveCopyEquityPnl = {
  sessionPnlUsd: number;
  equityPnlUsd: number;
  usedBookFallback: boolean;
};

/**
 * The Bitfinex backend session P&L already contains realized + unrealized P&L
 * minus fees. Only the closed-trade-book fallback is realized-only and needs
 * the open position's unrealized P&L added for an equity-basis comparison.
 */
export function computeLiveCopyEquityPnl(input: {
  backendSessionPnlUsd: number;
  bookRealizedPnlUsd: number;
  unrealizedPnlUsd: number;
}): LiveCopyEquityPnl {
  const usedBookFallback =
    input.backendSessionPnlUsd === 0 &&
    Math.abs(input.bookRealizedPnlUsd) > 0.0001;
  const sessionPnlUsd = usedBookFallback
    ? input.bookRealizedPnlUsd
    : input.backendSessionPnlUsd;
  const equityPnlUsd = usedBookFallback
    ? sessionPnlUsd + input.unrealizedPnlUsd
    : sessionPnlUsd;

  return { sessionPnlUsd, equityPnlUsd, usedBookFallback };
}
