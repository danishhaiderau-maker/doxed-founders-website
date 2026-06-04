import { Injectable } from '@nestjs/common';
import {
  filterFounderMilestoneItems,
  filterMoneyFeedUnifiedItems,
  filterUnifiedFeedForProjectSlug,
  isMoneyFeedTerminalKind,
  mergeFeedHubEntries,
  type FeedTerminalTab,
  type UnifiedFeedCategory,
  type UnifiedFeedItem,
} from '@dcf/utils';
import { UnifiedFeedService } from './unified-feed.service';
import { FeedTerminalService, type FeedTerminalCard } from './feed-terminal.service';

@Injectable()
export class FeedHubService {
  constructor(
    private readonly unifiedFeed: UnifiedFeedService,
    private readonly feedTerminal: FeedTerminalService,
  ) {}

  async getHub(
    category: UnifiedFeedCategory = 'all',
    terminalTab: FeedTerminalTab = 'all',
    projectSlug?: string,
    limit = 50,
  ) {
    const [unifiedRes, terminalRes] = await Promise.all([
      this.unifiedFeed.getUnifiedFeed(category, 80),
      category === 'all' || category === 'trading'
        ? this.feedTerminal.getTerminal(terminalTab, projectSlug)
        : Promise.resolve(null),
    ]);

    const unifiedForHub = projectSlug
      ? filterUnifiedFeedForProjectSlug(unifiedRes.items, projectSlug)
      : unifiedRes.items;

    const stream = mergeFeedHubEntries(unifiedForHub, terminalRes?.cards ?? [], {
      category,
      terminalTab,
      limit,
    });

    const sections = this.buildMoneyFeedSections(
      unifiedForHub,
      terminalRes?.cards ?? [],
      terminalRes?.topTraders ?? [],
    );

    return {
      category,
      terminalTab,
      projectSlug: projectSlug ?? null,
      pulse: unifiedRes.pulse,
      hotQuestions: unifiedRes.hotQuestions,
      scoutListings: unifiedRes.scoutListings,
      stream,
      sections,
      terminal: terminalRes,
      counts: {
        unified: unifiedRes.items.length,
        terminal: terminalRes?.cards.length ?? 0,
        merged: stream.length,
      },
    };
  }

  private buildMoneyFeedSections(
    unified: UnifiedFeedItem[],
    terminal: FeedTerminalCard[],
    topTraders: { userId: string; name: string; pnlUsd: number }[],
  ) {
    const moneyUnified = filterMoneyFeedUnifiedItems(unified);
    const tape = terminal
      .filter((c) => isMoneyFeedTerminalKind(c.kind))
      .filter((c) => ['BUY', 'SELL', 'ADD', 'REDUCE', 'THESIS', 'HOT_BUY'].includes(c.kind))
      .slice(0, 80);

    const listings = moneyUnified
      .filter((i) => i.eventType === 'listing_live')
      .slice(0, 8);

    const predictions = moneyUnified
      .filter((i) =>
        ['hot_prediction', 'prediction_staked', 'prediction_resolved', 'scout_vote_opened'].includes(
          i.eventType,
        ),
      )
      .slice(0, 8);

    const milestones = filterFounderMilestoneItems(unified).slice(0, 6);

    const buyVol = new Map<string, number>();
    const sellVol = new Map<string, number>();
    for (const c of terminal) {
      if (!c.projectTicker || !c.amountUsd) continue;
      if (c.kind === 'BUY' || c.kind === 'ADD' || c.kind === 'THESIS') {
        buyVol.set(c.projectTicker, (buyVol.get(c.projectTicker) ?? 0) + c.amountUsd);
      }
      if (c.kind === 'SELL' || c.kind === 'REDUCE' || c.kind === 'LOSS') {
        sellVol.set(c.projectTicker, (sellVol.get(c.projectTicker) ?? 0) + c.amountUsd);
      }
    }

    const sortTickers = (m: Map<string, number>) =>
      [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([ticker, usd]) => ({ ticker, usd }));

    const watchlisted = moneyUnified
      .filter((i) => i.eventType === 'watchlist_surge')
      .map((i) => ({
        ticker: i.projectTicker ?? '?',
        detail: i.detail ?? '',
        slug: i.projectSlug,
      }));

    return {
      topMovers: {
        mostBought: sortTickers(buyVol),
        mostSold: sortTickers(sellVol),
        mostWatchlisted: watchlisted,
        mostDiscussed: tape
          .filter((c) => (c.commentCount ?? 0) > 0)
          .slice(0, 5)
          .map((c) => ({ ticker: c.projectTicker ?? '?', trader: c.traderName })),
      },
      tape,
      predictions,
      listings,
      smartMoney: topTraders.slice(0, 5),
      milestones,
    };
  }
}
