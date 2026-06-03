export const CURSOR_CLOUD_API_BASE = 'https://api.cursor.com';

/** Fallback when GitHub default branch cannot be resolved (this monorepo uses master). */
export const CURSOR_FALLBACK_STARTING_REF = 'master';

export function resolveCursorStartingRef(branch?: string | null): string {
  const trimmed = branch?.trim();
  if (trimmed) return trimmed;
  const fromEnv =
    typeof process !== 'undefined' ? process.env.CURSOR_DEFAULT_BRANCH?.trim() : undefined;
  return fromEnv || CURSOR_FALLBACK_STARTING_REF;
}

export type CursorCloudDispatchInput = {
  apiKey: string;
  taskPrompt: string;
  repository?: string;
  startingRef?: string;
  agentId?: string | null;
  agentRepoUrl?: string | null;
};

export type CursorCloudDispatchResult = {
  agentId: string;
  runId: string;
  status: string;
  agentUrl: string;
  mode: 'create' | 'follow_up';
};

export function githubRepoToUrl(repoFullName: string): string {
  const trimmed = repoFullName.trim().replace(/^https?:\/\/github\.com\//i, '').replace(/\/+$/, '');
  if (!trimmed) throw new Error('GitHub repository required');
  return `https://github.com/${trimmed}`;
}

export function buildCursorAgentUrl(agentId: string): string {
  return `https://cursor.com/agents/${agentId}`;
}

export function buildCursorCloudTaskMessage(
  spec: string,
  cursorPrompt?: string,
  workspaceContext?: string,
): string {
  const parts = [
    'Founder OS build dispatch — implement in the connected GitHub repository.',
    '',
    workspaceContext?.trim() ? workspaceContext.trim() : '',
    workspaceContext?.trim() ? '' : '',
    cursorPrompt?.trim() ? `## Task\n${cursorPrompt.trim()}` : '',
    spec?.trim() ? `## Spec\n${spec.trim()}` : '',
  ].filter(Boolean);
  return parts.join('\n');
}
