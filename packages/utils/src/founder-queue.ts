/** CEO inbox — every item requires a verb (P1 Command Center). */

export type FounderQueueItemKind =
  | 'REVIEW_PR'
  | 'PUBLISH_UPDATE'
  | 'RUN_BUILD'
  | 'SYNC_GITHUB'
  | 'CONNECT_STACK'
  | 'AGENT_REVIEW'
  | 'DEPLOY_CHECK'
  | 'SCOUT_ACTION'
  | 'MISSION_EDIT';

export type FounderQueueAction =
  | 'open_url'
  | 'publish'
  | 'dispatch_build'
  | 'merge_pr'
  | 'sync'
  | 'settings'
  | 'chat_prompt';

export type FounderQueueBucket =
  | 'needs_attention'
  | 'needs_approval'
  | 'needs_decision'
  | 'needs_deployment'
  | 'needs_publishing'
  | 'needs_review';

export const FOUNDER_QUEUE_BUCKET_LABELS: Record<FounderQueueBucket, string> = {
  needs_attention: 'Needs Attention',
  needs_approval: 'Needs Approval',
  needs_decision: 'Needs Decision',
  needs_deployment: 'Needs Deployment',
  needs_publishing: 'Needs Publishing',
  needs_review: 'Needs Review',
};

export const FOUNDER_QUEUE_BUCKET_ORDER: FounderQueueBucket[] = [
  'needs_attention',
  'needs_review',
  'needs_approval',
  'needs_publishing',
  'needs_deployment',
  'needs_decision',
];

export type FounderQueueItem = {
  id: string;
  kind: FounderQueueItemKind;
  priority: number;
  title: string;
  detail?: string;
  action: FounderQueueAction;
  href?: string;
  prompt?: string;
  /** Entity id for control actions (suggestion id, task id, etc.) */
  targetId?: string;
  /** Link to Agent Runtime run when applicable */
  sourceRunId?: string;
  createdAt?: string;
  /** 0–100 — higher = act sooner (Sprint G scoring). */
  urgencyScore?: number;
  /** 0–100 — higher = more product impact. */
  impactScore?: number;
};

export function getFounderQueueBucket(kind: FounderQueueItemKind): FounderQueueBucket {
  switch (kind) {
    case 'REVIEW_PR':
      return 'needs_review';
    case 'PUBLISH_UPDATE':
      return 'needs_approval';
    case 'DEPLOY_CHECK':
      return 'needs_deployment';
    case 'CONNECT_STACK':
    case 'SCOUT_ACTION':
    case 'MISSION_EDIT':
      return 'needs_decision';
    case 'AGENT_REVIEW':
    case 'RUN_BUILD':
    case 'SYNC_GITHUB':
    default:
      return 'needs_attention';
  }
}

export function isActionableQueueItem(item: FounderQueueItem): boolean {
  return (
    item.action === 'merge_pr' ||
    item.action === 'publish' ||
    item.action === 'dispatch_build' ||
    item.action === 'sync'
  );
}

export function countActionableQueueItems(items: FounderQueueItem[]): number {
  return items.filter(isActionableQueueItem).length;
}

export function groupFounderQueueByBucket(
  items: FounderQueueItem[],
): Record<FounderQueueBucket, FounderQueueItem[]> {
  const buckets = Object.fromEntries(
    FOUNDER_QUEUE_BUCKET_ORDER.map((b) => [b, [] as FounderQueueItem[]]),
  ) as Record<FounderQueueBucket, FounderQueueItem[]>;
  for (const item of sortFounderQueue(items)) {
    buckets[getFounderQueueBucket(item.kind)].push(item);
  }
  return buckets;
}

function fallbackUrgencyFromPriority(priority: number): number {
  return Math.max(1, 110 - priority * 25);
}

export function sortFounderQueue(items: FounderQueueItem[]): FounderQueueItem[] {
  return [...items].sort((a, b) => {
    const ua = a.urgencyScore ?? fallbackUrgencyFromPriority(a.priority);
    const ub = b.urgencyScore ?? fallbackUrgencyFromPriority(b.priority);
    if (ub !== ua) return ub - ua;
    const ia = a.impactScore ?? 50;
    const ib = b.impactScore ?? 50;
    if (ib !== ia) return ib - ia;
    return a.priority - b.priority || (a.createdAt ?? '').localeCompare(b.createdAt ?? '');
  });
}
