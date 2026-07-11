/**
 * Memory Engine — types for the kernel's four memory stores.
 * See docs/KERNEL.md §3 (service #3).
 *
 * Stores are Prisma-backed (ConversationMemory / ProjectMemory /
 * FounderMemory / WorkspaceMemory).
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
