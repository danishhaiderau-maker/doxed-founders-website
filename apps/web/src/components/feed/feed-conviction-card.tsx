'use client';

import Link from 'next/link';
import {
  buildRegretShareText,
  buildSmartExitShareText,
  buildTimelineBuyShareText,
  buildTimelineSellShareText,
  buildTimelineThesisShareText,
  buildSiteUrl,
  formatUsd,
  formatTokenPrice,
} from '@dcf/utils';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import type { FeedTerminalCard } from '@/lib/api';
import {
  feedCardAccentClasses,
  feedCardKindAccent,
  feedCardKindLabel,
  feedKindBadgeClasses,
} from './feed-terminal-tabs';

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function buildShareText(card: FeedTerminalCard, origin: string) {
  const portfolioUrl = card.traderId ? buildSiteUrl(origin, `/portfolio/${card.traderId}`) : undefined;
  const ticker = card.projectTicker ?? 'TOKEN';

  switch (card.kind) {
    case 'MISSED_ALPHA':
      return buildRegretShareText({
        ticker,
        realizedReturnPct: card.pnlPct ?? 0,
        missedAfterExitPct: card.missedAlphaPct ?? 0,
        pumpAfterExitPct: card.missedAlphaPct ?? 0,
        exitPriceUsd: card.priceUsd ?? 0,
        postExitPeakPriceUsd: card.currentPriceUsd ?? card.priceUsd ?? 0,
        portfolioUrl,
      });
    case 'SMART_EXIT':
      return buildSmartExitShareText({
        ticker,
        exitPriceUsd: card.priceUsd ?? 0,
        postExitTroughPriceUsd: card.currentPriceUsd ?? 0,
        avoidedLossPct: card.avoidedLossPct ?? 0,
        realizedReturnPct: card.pnlPct ?? 0,
        portfolioUrl,
      });
    case 'THESIS':
    case 'NEW_THESIS':
      return buildTimelineThesisShareText({
        ticker,
        thesis: card.reason ?? '',
        portfolioUrl,
      });
    case 'BUY':
      return buildTimelineBuyShareText({
        ticker,
        amountUsd: card.amountUsd ?? 0,
        thesis: card.reason,
        portfolioUrl,
      });
    case 'SELL':
    case 'LOSS':
      return buildTimelineSellShareText({
        ticker,
        realizedReturnPct: card.pnlPct ?? 0,
        amountUsd: card.amountUsd ?? 0,
        thesis: card.reason,
        portfolioUrl,
      });
    default:
      return `${feedCardKindLabel(card.kind)} — $${ticker}\n\n${card.reason ?? ''}\n\n${buildSiteUrl(origin, card.link ?? '/feed')}`;
  }
}

export function FeedConvictionCard({ card }: { card: FeedTerminalCard }) {
  const origin = useShareOrigin();
  const accent = feedCardKindAccent(card.kind);
  const shareUrl = buildSiteUrl(origin, card.link ?? '/feed');
  const shareText = buildShareText(card, origin);

  return (
    <article className={`rounded-xl border p-4 ${feedCardAccentClasses(accent)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${feedKindBadgeClasses(accent)}`}
            >
              {feedCardKindLabel(card.kind)}
            </span>
            <span className="text-xs text-zinc-500">{timeAgo(card.at)}</span>
          </div>

          <div className="mt-2 flex flex-wrap items-baseline gap-2">
            {card.traderName && (
              <Link
                href={card.traderId ? `/portfolio/${card.traderId}` : '#'}
                className="font-semibold text-white hover:text-violet-300"
              >
                {card.traderName}
              </Link>
            )}
            {card.projectTicker && (
              <>
                {card.traderName && <span className="text-zinc-500">·</span>}
                <Link
                  href={card.projectSlug ? `/project/${card.projectSlug}` : '#'}
                  className="font-bold text-white hover:text-emerald-300"
                >
                  ${card.projectTicker}
                </Link>
              </>
            )}
            {card.kind === 'FOLLOWER_SPIKE' && card.followerSpike && (
              <span className="text-sm text-zinc-300">
                {card.followerSpike} traders followed
              </span>
            )}
          </div>

          {card.amountUsd != null && ['BUY', 'SELL', 'THESIS', 'LOSS'].includes(card.kind) && (
            <p className="mt-1 text-sm text-zinc-400">
              {formatUsd(card.amountUsd, 0)} DDollar
              {card.convictionLabel && (
                <span className="ml-2 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-400">
                  {card.convictionLabel}
                </span>
              )}
            </p>
          )}

          {card.reason && (
            <p className="mt-2 text-sm text-zinc-400">
              {card.kind === 'THESIS' ? (
                <>
                  <span className="text-zinc-500">Reason: </span>
                  &ldquo;{card.reason}&rdquo;
                </>
              ) : (
                card.reason
              )}
            </p>
          )}

          {card.kind === 'MISSED_ALPHA' && card.priceUsd != null && card.currentPriceUsd != null && (
            <div className="mt-2 grid gap-1 text-xs text-zinc-500 sm:grid-cols-2">
              <span>Sold at: {formatTokenPrice(card.priceUsd)}</span>
              <span>Current: {formatTokenPrice(card.currentPriceUsd)}</span>
            </div>
          )}

          {card.commentCount != null && card.commentCount > 0 && (
            <p className="mt-2 text-xs text-zinc-600">💬 {card.commentCount} replies</p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          {card.pnlPct != null && (
            <div className="text-right">
              <p
                className={`text-lg font-bold tabular-nums ${
                  card.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                {card.pnlPct >= 0 ? '+' : ''}
                {card.pnlPct.toFixed(1)}%
              </p>
              {card.pnlUsd != null && (
                <p className="text-xs text-zinc-500">
                  ({card.pnlUsd >= 0 ? '+' : ''}
                  {formatUsd(card.pnlUsd, 0)})
                </p>
              )}
            </div>
          )}
          {card.missedAlphaPct != null && card.kind === 'MISSED_ALPHA' && (
            <p className="text-sm font-semibold text-orange-400">
              Missed +{card.missedAlphaPct.toFixed(0)}%
            </p>
          )}
          {card.avoidedLossPct != null && card.kind === 'SMART_EXIT' && (
            <p className="text-sm font-semibold text-emerald-400">
              Avoided −{card.avoidedLossPct.toFixed(0)}%
            </p>
          )}
          <ShareOnXButton text={shareText} url={shareUrl} label="Share" />
        </div>
      </div>
    </article>
  );
}
