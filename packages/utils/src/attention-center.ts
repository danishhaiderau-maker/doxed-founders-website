import type { FounderQueueItem } from './founder-queue';

/** Actionable attention — no passive “points earned” rows. */
export type AttentionItem = {
  id: string;
  severity: 'urgent' | 'normal' | 'low';
  title: string;
  verb: string;
  href?: string;
  prompt?: string;
  source: 'queue' | 'builder' | 'deploy' | 'pr' | 'publish' | 'scout' | 'stack';
};

export function founderQueueToAttention(item: FounderQueueItem): AttentionItem {
  const verbMap: Record<string, string> = {
    REVIEW_PR: 'Merge PR',
    PUBLISH_UPDATE: 'Publish',
    RUN_BUILD: 'Run build',
    SYNC_GITHUB: 'Sync',
    CONNECT_STACK: 'Connect',
    AGENT_REVIEW: 'Review run',
    DEPLOY_CHECK: 'Check deploy',
    SCOUT_ACTION: 'Resolve',
    MISSION_EDIT: 'Update mission',
  };
  const severity: AttentionItem['severity'] =
    item.kind === 'AGENT_REVIEW' || item.kind === 'DEPLOY_CHECK'
      ? 'urgent'
      : item.priority <= 2
        ? 'normal'
        : 'low';

  return {
    id: `att-${item.id}`,
    severity,
    title: item.title,
    verb: verbMap[item.kind] ?? 'Open',
    href: item.href,
    prompt: item.prompt,
    source:
      item.kind === 'REVIEW_PR'
        ? 'pr'
        : item.kind === 'PUBLISH_UPDATE'
          ? 'publish'
          : item.kind === 'AGENT_REVIEW'
            ? 'builder'
            : item.kind === 'SCOUT_ACTION'
              ? 'scout'
              : item.kind === 'CONNECT_STACK'
                ? 'stack'
                : 'queue',
  };
}

export function sortAttentionItems(items: AttentionItem[]): AttentionItem[] {
  const rank = { urgent: 0, normal: 1, low: 2 };
  return [...items].sort((a, b) => rank[a.severity] - rank[b.severity]);
}
