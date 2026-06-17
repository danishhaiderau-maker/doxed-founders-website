/** P1.5 — persisted builder run surfaced in Mission Control (not external Cursor tab only). */

import type { AgentRuntimeStep } from './agent-runtime';
import type { BuildAdapterId } from './provider-adapters';

export type FounderAgentRunWorker = 'CURSOR' | 'OPENHANDS';

export type FounderAgentRunRecord = {
  worker: FounderAgentRunWorker;
  adapterId?: BuildAdapterId;
  adapterLabel?: string;
  status: string;
  task: string;
  repository?: string | null;
  agentId?: string | null;
  runId?: string | null;
  conversationId?: string | null;
  prUrl?: string | null;
  branch?: string | null;
  terminal: boolean;
  steps?: AgentRuntimeStep[];
  startedAt: string;
  updatedAt: string;
};

export function isAgentRunActive(run: FounderAgentRunRecord | null | undefined): boolean {
  if (!run || run.terminal) return false;
  const s = run.status.toUpperCase();
  return !['FINISHED', 'COMPLETED', 'DONE', 'ERROR', 'FAILED', 'CANCELLED', 'EXPIRED'].includes(s);
}
