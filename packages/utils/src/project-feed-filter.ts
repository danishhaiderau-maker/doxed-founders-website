import type { UnifiedFeedItem } from './unified-feed.js';
import { isMoneyFeedUnifiedEvent } from './money-feed.js';

/** P0 — project-scoped feed views show money/trader activity only (no build/commit noise). */
export function filterUnifiedFeedForProjectSlug(
  items: UnifiedFeedItem[],
  projectSlug: string,
): UnifiedFeedItem[] {
  const slug = projectSlug.trim().toLowerCase();
  return items.filter(
    (i) => i.projectSlug?.toLowerCase() === slug && isMoneyFeedUnifiedEvent(i.eventType),
  );
}
