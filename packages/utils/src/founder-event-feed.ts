import type { UnifiedFeedItem } from './unified-feed';
import { unifiedFeedTier } from './unified-feed';

/** Map platform FounderEvent rows into unified feed items (Sprint 7b). */
export function founderEventToUnifiedItem(event: {
  id: string;
  type: string;
  title: string;
  createdAt: Date | string;
  founder: { slug: string; name: string };
  project?: { slug: string; name: string; ticker: string } | null;
  payload?: Record<string, unknown> | null;
}): UnifiedFeedItem {
  const at =
    typeof event.createdAt === 'string'
      ? event.createdAt
      : event.createdAt.toISOString();
  const project = event.project;
  const payload = event.payload ?? {};
  const detailFromPayload =
    typeof payload.summary === 'string'
      ? payload.summary
      : typeof payload.task === 'string'
        ? payload.task
        : undefined;

  const mapped = mapFounderEventType(event.type, event.title, event.founder.name, project?.ticker);

  return {
    id: `founder-event-${event.id}`,
    tier: unifiedFeedTier(mapped.eventType),
    category: 'founder',
    eventType: mapped.eventType,
    emoji: mapped.emoji,
    headline: mapped.headline ?? event.title,
    detail: detailFromPayload ?? mapped.detail,
    at,
    link: project ? `/project/${project.slug}` : `/founder/${event.founder.slug}`,
    projectSlug: project?.slug,
    projectTicker: project?.ticker,
    founderSlug: event.founder.slug,
  };
}

function mapFounderEventType(
  type: string,
  title: string,
  founderName: string,
  ticker?: string,
): { eventType: string; emoji: string; headline?: string; detail?: string } {
  const tag = ticker ? ` · ${ticker}` : '';
  switch (type) {
    case 'GITHUB_COMMIT':
      return {
        eventType: 'github_milestone',
        emoji: '🔀',
        headline: title,
        detail: `${founderName}${tag} pushed to GitHub`,
      };
    case 'GITHUB_PR_MERGED':
      return {
        eventType: 'github_milestone',
        emoji: '✅',
        headline: title,
        detail: `${founderName}${tag} merged a PR`,
      };
    case 'DEPLOY_SUCCESS':
      return {
        eventType: 'deployment',
        emoji: '🚀',
        headline: title,
        detail: `${founderName}${tag} deployed successfully`,
      };
    case 'DEPLOY_STARTED':
      return {
        eventType: 'deployment',
        emoji: '⏳',
        headline: title,
        detail: `${founderName}${tag} deploy in progress`,
      };
    case 'BUILD_PUBLISHED':
      return {
        eventType: 'build_update',
        emoji: '🔨',
        headline: title,
        detail: `${founderName}${tag} published a build update`,
      };
    case 'CURSOR_BUILD_SESSION':
      return {
        eventType: 'build_update',
        emoji: '🤖',
        headline: title,
        detail: `${founderName}${tag} · Builder agent session`,
      };
    case 'BUILD_QUEUE_CAPTURED':
      return {
        eventType: 'build_update',
        emoji: '📋',
        headline: title,
        detail: `${founderName}${tag} captured a build task`,
      };
    case 'AGENT_RUN_COMPLETE':
      return {
        eventType: 'build_update',
        emoji: '⚡',
        headline: title,
        detail: `${founderName}${tag} agent run finished`,
      };
    default:
      return {
        eventType: 'build_update',
        emoji: '📣',
        headline: title,
        detail: founderName,
      };
  }
}
