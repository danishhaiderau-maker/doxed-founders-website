/** Phase 3 — Provider adapter layer (Founder OS dispatches; users see Builder Agent). */

import type { FounderBrainTask } from './founder-brain-router';

export type BuildAdapterId = 'cursor' | 'openhands' | 'founder_node' | 'none';

export type LlmAdapterId = 'deepseek' | 'openai' | 'anthropic' | 'gemini' | 'openrouter' | 'jatevo' | 'ollama' | 'phala' | 'surplus';

export type ProviderAdapterKind = 'build' | 'llm' | 'research';

export type BuildAdapterDefinition = {
  id: BuildAdapterId;
  label: string;
  capabilities: ('code' | 'refactor' | 'pr')[];
  credentialKeys: string[];
};

export const BUILD_ADAPTERS: BuildAdapterDefinition[] = [
  {
    id: 'cursor',
    label: 'Builder Agent',
    capabilities: ['code', 'refactor', 'pr'],
    credentialKeys: ['cursor'],
  },
  {
    id: 'openhands',
    label: 'Builder Agent',
    capabilities: ['code', 'refactor'],
    credentialKeys: ['openhands'],
  },
  {
    id: 'founder_node',
    label: 'Founder Node',
    capabilities: ['code'],
    credentialKeys: ['ollama'],
  },
];

/** User-facing label — never vendor names in Mission Control. */
export function buildAdapterLabel(adapterId: BuildAdapterId): string {
  return BUILD_ADAPTERS.find((a) => a.id === adapterId)?.label ?? 'Builder Agent';
}

export function resolveBuildAdapter(input: {
  cursor?: boolean;
  openHands?: boolean;
  founderNode?: boolean;
  preferred?: 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE';
}): BuildAdapterId {
  if (input.preferred === 'CURSOR' && input.cursor) return 'cursor';
  if (input.preferred === 'OPENHANDS' && input.openHands) return 'openhands';
  if (input.preferred === 'FOUNDER_NODE' && input.founderNode) return 'founder_node';
  if (input.cursor) return 'cursor';
  if (input.openHands) return 'openhands';
  if (input.founderNode) return 'founder_node';
  return 'none';
}

/** Map brain task → preferred build adapter when auto-dispatching code work. */
export function preferredBuildAdapterForTask(_task: FounderBrainTask): BuildAdapterId | null {
  return 'cursor';
}

export function workerToBuildAdapter(worker: 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE'): BuildAdapterId {
  if (worker === 'CURSOR') return 'cursor';
  if (worker === 'OPENHANDS') return 'openhands';
  if (worker === 'FOUNDER_NODE') return 'founder_node';
  return 'none';
}

export function buildAdapterToWorker(adapterId: BuildAdapterId): 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE' {
  if (adapterId === 'cursor') return 'CURSOR';
  if (adapterId === 'openhands') return 'OPENHANDS';
  if (adapterId === 'founder_node') return 'FOUNDER_NODE';
  return 'NONE';
}
