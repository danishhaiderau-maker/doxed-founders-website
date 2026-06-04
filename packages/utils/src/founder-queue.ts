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
  createdAt?: string;
};

export function sortFounderQueue(items: FounderQueueItem[]): FounderQueueItem[] {
  return [...items].sort((a, b) => a.priority - b.priority || (a.createdAt ?? '').localeCompare(b.createdAt ?? ''));
}
