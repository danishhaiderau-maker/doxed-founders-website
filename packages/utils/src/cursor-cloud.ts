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
  repository: string;
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

export type CursorCloudTaskContext = {
  repository?: string | null;
  startingRef?: string | null;
};

export function normalizeGitHubRepoFullName(repoFullName: string): string {
  const trimmed = repoFullName
    .trim()
    .replace(/^https?:\/\/github\.com\//i, '')
    .replace(/^git@github\.com:/i, '')
    .replace(/\/+$/, '')
    .replace(/\.git$/i, '');
  if (!trimmed) throw new Error('GitHub repository required');
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(trimmed)) {
    throw new Error('GitHub repository must be owner/repo');
  }
  return trimmed;
}

export function githubRepoToUrl(repoFullName: string): string {
  return `https://github.com/${normalizeGitHubRepoFullName(repoFullName)}`;
}

export function buildCursorAgentUrl(agentId: string): string {
  return `https://cursor.com/agents/${agentId}`;
}

export function buildCursorCloudTaskMessage(
  spec: string,
  cursorPrompt?: string,
  context?: CursorCloudTaskContext,
): string {
  const repository = context?.repository ? normalizeGitHubRepoFullName(context.repository) : null;
  const repositoryContext = repository
    ? [
        '## Connected repository',
        `- Repository: ${repository}`,
        `- GitHub URL: ${githubRepoToUrl(repository)}`,
        context?.startingRef?.trim() ? `- Starting ref: ${context.startingRef.trim()}` : '',
        '',
        '## Isolation check',
        `Before editing, confirm Cursor is operating in ${repository} only.`,
        'Report whether the connected repository is visible and whether any prior workspace, repo, or project context appears cross-contaminated.',
        'If the visible repo is not the connected repository, stop and report the mismatch instead of changing files.',
      ].filter(Boolean).join('\n')
    : '';
  const parts = [
    'Founder OS build dispatch — implement in the connected GitHub repository.',
    '',
    repositoryContext,
    cursorPrompt?.trim() ? `## Task\n${cursorPrompt.trim()}` : '',
    spec?.trim() ? `## Spec\n${spec.trim()}` : '',
  ].filter(Boolean);
  return parts.join('\n');
}
