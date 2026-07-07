/**
 * Memory Engine — types for the kernel's four memory stores.
 * See docs/KERNEL.md §3 (service #3) and the Phase 1 scope note in §10.
 *
 * Phase 1 ships the interface; real backends land in Phases 2-4.
 */

export type MemoryStore = 'conversation' | 'project' | 'founder' | 'workspace';

export type MemoryEntry = {
  store: MemoryStore;
  /**
   * sessionId for conversation, projectId for project, userId for founder,
   * workspaceId for workspace.
   */
  scope: string;
  key: string;
  value: unknown;
  createdAt: Date;
  updatedAt: Date;
};

export type MemoryQuery = {
  store?: MemoryStore;
  scope?: string;
  keyPrefix?: string;
  limit?: number;
};
