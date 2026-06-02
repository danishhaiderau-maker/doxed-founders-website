/** Routes build execution to the best connected worker — founders never pick providers in UI. */

export type BuildWorkerKey = 'CURSOR' | 'OPENHANDS' | 'FOUNDER_NODE' | 'NONE';

export type BuildWorkerConnections = {
  cursor: boolean;
  openHands: boolean;
  founderNode: boolean;
  defaultProvider?: string | null;
};

export function resolveBuildWorker(connections: BuildWorkerConnections): BuildWorkerKey {
  if (connections.cursor) return 'CURSOR';
  if (connections.openHands) return 'OPENHANDS';
  if (connections.founderNode) return 'FOUNDER_NODE';
  return 'NONE';
}

export function buildWorkerLabel(worker: BuildWorkerKey): string {
  switch (worker) {
    case 'CURSOR':
      return 'Builder Agent';
    case 'OPENHANDS':
      return 'Builder Agent';
    case 'FOUNDER_NODE':
      return 'Founder Node';
    default:
      return 'None';
  }
}

export type AiStackHealth = 'healthy' | 'needs_attention' | 'offline';

export function resolveAiStackHealth(input: {
  llmConnected: boolean;
  buildWorker: BuildWorkerKey;
  githubConnected: boolean;
}): AiStackHealth {
  if (input.buildWorker !== 'NONE' && input.llmConnected && input.githubConnected) return 'healthy';
  if (input.buildWorker !== 'NONE' || input.llmConnected) return 'needs_attention';
  return 'offline';
}
