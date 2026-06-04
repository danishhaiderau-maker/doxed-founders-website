import { Injectable } from '@nestjs/common';
import {
  mergeFeedHubEntries,
  type FeedTerminalTab,
  type UnifiedFeedCategory,
} from '@dcf/utils';
import { UnifiedFeedService } from './unified-feed.service';
import { FeedTerminalService } from './feed-terminal.service';

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

    const stream = mergeFeedHubEntries(unifiedRes.items, terminalRes?.cards ?? [], {
      category,
      terminalTab,
      limit,
    });

    return {
      category,
      terminalTab,
      projectSlug: projectSlug ?? null,
      pulse: unifiedRes.pulse,
      hotQuestions: unifiedRes.hotQuestions,
      scoutListings: unifiedRes.scoutListings,
      stream,
      terminal: terminalRes,
      counts: {
        unified: unifiedRes.items.length,
        terminal: terminalRes?.cards.length ?? 0,
        merged: stream.length,
      },
    };
  }
}
