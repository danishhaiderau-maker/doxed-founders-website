'use client';

import type { FeedHubEntry, FeedTerminalCard } from '@/lib/api';
import { FeedConvictionCard } from '@/components/feed/feed-conviction-card';
import { UnifiedFeedItemCard } from '@/components/feed/unified-feed-item-card';

type Props = {
  stream: FeedHubEntry[];
  terminalCardsById?: Map<string, FeedTerminalCard>;
};

export function FeedHubStream({ stream, terminalCardsById }: Props) {
  return (
    <div className="space-y-3">
      {stream.map((entry) => {
        if (entry.kind === 'unified') {
          return <UnifiedFeedItemCard key={entry.item.id} item={entry.item} />;
        }
        const card = terminalCardsById?.get(entry.card.id) ?? (entry.card as FeedTerminalCard);
        return <FeedConvictionCard key={entry.card.id} card={card} />;
      })}
    </div>
  );
}
