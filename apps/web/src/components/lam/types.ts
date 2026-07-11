/**
 * Shared types for the LAM (Large Action Model) frontend components.
 * Mirrors the backend LamTask / LamStep / LamStepResult shapes from
 * apps/api/src/lam/lam.types.ts so the components are typed end-to-end.
 */

export type LamAdapterId = 'browser' | 'computer-use';

export type LamTaskStatus = 'PLANNING' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface LamStep {
  index: number;
  description: string;
  adapter: LamAdapterId;
  payload: unknown;
}

export interface LamStepResult {
  index: number;
  status: 'success' | 'failed' | 'skipped';
  summary: string;
  artifacts?: string[];
  error?: string;
  startedAt: string;
  completedAt: string;
}

export interface LamTask {
  id: string;
  userId: string;
  goal: string;
  status: LamTaskStatus;
  steps: LamStep[];
  results: LamStepResult[];
  result?: string;
  elapsedMs?: number;
  costDdollar?: number;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

export interface LamAdapterStatus {
  id: LamAdapterId;
  available: boolean;
  reason?: string;
  premium?: boolean;
}

export const STATUS_META: Record<LamTaskStatus, { label: string; color: string }> = {
  PLANNING: { label: 'Planning', color: 'text-amber-300' },
  RUNNING: { label: 'Running', color: 'text-sky-300' },
  COMPLETED: { label: 'Completed', color: 'text-emerald-300' },
  FAILED: { label: 'Failed', color: 'text-rose-300' },
};
